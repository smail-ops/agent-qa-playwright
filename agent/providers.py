"""Les « cerveaux » de l'agent : Gemini, Claude, ou un faux modèle pour les tests.

Gemini et Claude sont appelés directement par leur API REST, avec la
bibliothèque standard de Python (urllib). Chaque fournisseur expose la même
méthode next_action() : l'agent peut changer de modèle sans changer de code.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .browser import Observation

# Alias de Google qui pointe toujours vers le dernier modèle Flash : le programme
# continue de fonctionner quand Google retire un ancien modèle.
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
DEFAULT_CLAUDE_MODEL = "claude-haiku-5-5"
RETRY_CODES = {429, 500, 502, 503, 529}  # quota dépassé ou serveur surchargé : on réessaie


class ProviderError(RuntimeError):
    """Erreur côté API (clé absente, quota, réponse inattendue...)."""


class Provider(Protocol):
    name: str
    model: str

    def next_action(self, system: str, user: str, observation: Observation) -> str:
        """Renvoie la réponse brute du modèle (normalement un objet JSON)."""
        ...


def post_json(url: str, headers: dict, payload: dict, timeout: float = 60, retries: int = 3) -> dict:
    """Envoie une requête POST JSON, avec nouvelles tentatives si le quota est atteint."""
    body = json.dumps(payload).encode("utf-8")
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url, data=body, method="POST", headers={"content-type": "application/json", **headers}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code in RETRY_CODES and attempt < retries:
                time.sleep(5 * 2**attempt)  # 5 s, puis 10 s, puis 20 s
                continue
            raise ProviderError(f"Erreur API {exc.code} : {detail}") from exc
        except urllib.error.URLError as exc:
            if attempt < retries:
                time.sleep(3)
                continue
            raise ProviderError(f"Connexion impossible à l'API : {exc.reason}") from exc
    raise ProviderError("Nombre maximal de tentatives atteint")


class GeminiProvider:
    """Google Gemini (offre gratuite disponible sur Google AI Studio)."""

    name = "gemini"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ProviderError("Clé GEMINI_API_KEY absente : ajoute-la dans le fichier .env")
        self.model = model or os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL

    def build_request(self, system: str, user: str) -> tuple[str, dict, dict]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key}
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        return url, headers, payload

    @staticmethod
    def parse_response(data: dict) -> str:
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"Réponse Gemini inattendue : {json.dumps(data)[:300]}") from exc
        return "".join(part.get("text", "") for part in parts)

    def next_action(self, system: str, user: str, observation: Observation) -> str:
        return self.parse_response(post_json(*self.build_request(system, user)))


class ClaudeProvider:
    """Anthropic Claude (API payante à l'usage)."""

    name = "claude"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ProviderError("Clé ANTHROPIC_API_KEY absente : ajoute-la dans le fichier .env")
        self.model = model or os.getenv("CLAUDE_MODEL") or DEFAULT_CLAUDE_MODEL

    def build_request(self, system: str, user: str) -> tuple[str, dict, dict]:
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        return url, headers, payload

    @staticmethod
    def parse_response(data: dict) -> str:
        blocks = data.get("content") if isinstance(data, dict) else None
        if not blocks:
            raise ProviderError(f"Réponse Claude inattendue : {json.dumps(data)[:300]}")
        return "".join(block.get("text", "") for block in blocks if block.get("type") == "text")

    def next_action(self, system: str, user: str, observation: Observation) -> str:
        return self.parse_response(post_json(*self.build_request(system, user)))


class ScriptedProvider:
    """Faux modèle qui rejoue un scénario écrit à l'avance.

    Il permet de tester tout le reste du programme sans clé d'API ni réseau.
    Chaque étape du scénario peut désigner un élément par son sélecteur
    (« target ») : le faux modèle retrouve alors son numéro dans la page.
    """

    name = "scripted"
    model = "scénario simulé"

    def __init__(self, steps: list[dict]):
        self.steps = list(steps)
        self.index = 0

    @classmethod
    def from_file(cls, path: str | Path) -> ScriptedProvider:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def next_action(self, system: str, user: str, observation: Observation) -> str:
        if self.index >= len(self.steps):
            return json.dumps({"action": "finish", "args": {"status": "BLOQUE", "summary": "Scénario terminé sans finish"}})
        step = dict(self.steps[self.index])
        self.index += 1
        if "raw" in step:  # permet de simuler une réponse mal formée
            return step["raw"]
        args = dict(step.get("args", {}))
        target = step.get("target")
        if target is not None:
            element = next((e for e in observation.elements if e.selector == target), None)
            args["ref"] = element.ref if element else -1
        return json.dumps(
            {"action": step["action"], "args": args, "reason": step.get("reason", "Étape du scénario")},
            ensure_ascii=False,
        )


def make_provider(name: str, model: str | None = None, scenario: str | None = None) -> Provider:
    """Crée le fournisseur demandé en ligne de commande."""
    if name == "gemini":
        return GeminiProvider(model=model)
    if name == "claude":
        return ClaudeProvider(model=model)
    if name == "scripted":
        if not scenario:
            raise ProviderError("Le fournisseur « scripted » a besoin d'un fichier --scenario")
        return ScriptedProvider.from_file(scenario)
    raise ProviderError(f"Fournisseur inconnu : {name}")
