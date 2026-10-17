"""Les « cerveaux » de l'agent : Gemini, Claude, ou un faux modèle pour les tests.

Gemini et Claude sont appelés directement par leur API REST, avec la
bibliothèque standard de Python (urllib). Chaque fournisseur expose les mêmes
méthodes : next_action() pour l'agent, complete() pour une demande libre
(diagnostic, user stories, cas de test). L'agent peut changer de modèle sans
changer de code. Les deux acceptent des images : avec la vision, le modèle
voit aussi la capture d'écran de la page.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .browser import Observation

# Alias de Google qui pointe toujours vers le dernier modèle Flash : le programme
# continue de fonctionner quand Google retire un ancien modèle.
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
DEFAULT_CLAUDE_MODEL = "claude-haiku-5-5"
RETRY_CODES = {500, 502, 503, 529}  # serveur surchargé : on réessaie après une courte pause


class ProviderError(RuntimeError):
    """Erreur côté API (clé absente, quota, réponse inattendue...)."""


def quota_reset_time(now: datetime | None = None) -> str:
    """Heure locale du prochain renouvellement des quotas journaliers de Google (minuit en Californie)."""
    try:
        from zoneinfo import ZoneInfo

        pacific = ZoneInfo("America/Los_Angeles")
    except Exception:  # base des fuseaux horaires absente
        return "à minuit, heure de Californie"
    now_pt = (now or datetime.now(timezone.utc)).astimezone(pacific)
    reset = (now_pt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone()
    return f"à {reset.hour} h" + (f" {reset.minute:02d}" if reset.minute else "") + " (heure de ton ordinateur)"


class RateLimitError(ProviderError):
    """Quota de l'API atteint (erreur 429).

    retry_after : délai conseillé par l'API, en secondes, s'il est connu.
    daily : le quota du jour est épuisé, attendre quelques secondes ne servira à rien.
    no_free_quota : ce modèle n'a aucun quota gratuit pour ce projet (limite 0).
    """

    def __init__(self, detail: str = "", retry_after: float | None = None, daily: bool = False,
                 no_free_quota: bool = False):
        self.detail, self.retry_after, self.daily, self.no_free_quota = detail, retry_after, daily, no_free_quota
        super().__init__(self.explain())

    def explain(self) -> str:
        if self.no_free_quota:
            return ("ce modèle n'a pas de quota gratuit pour ton projet (limite 0). Choisis un modèle de l'offre "
                    "gratuite avec GEMINI_MODEL dans .env, ou active la facturation.")
        if self.daily:
            return (f"quota du jour épuisé pour ce modèle. Il se renouvelle {quota_reset_time()}. En attendant : "
                    "un autre modèle (GEMINI_MODEL dans .env) ou la démo sans IA.")
        return ("limite de requêtes atteinte (erreur 429). Allonge la pause entre deux actions, "
                "ou réessaie dans quelques minutes.")


def _seconds(value) -> float | None:
    """« 17s », « 17.5s » ou « 30 » donnent un nombre de secondes."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*s?\s*", str(value or ""))
    return float(match.group(1)) if match else None


def parse_rate_limit(body: str, headers=None) -> RateLimitError:
    """Lit une réponse 429 de Gemini (QuotaFailure, RetryInfo) ou de Claude (en-tête retry-after)."""
    try:
        data = json.loads(body)
    except ValueError:
        data = {}
    error = data.get("error") if isinstance(data, dict) else None
    error = error if isinstance(error, dict) else {}
    retry_after, daily, no_free_quota = None, False, False
    for detail in error.get("details") or []:
        if not isinstance(detail, dict):
            continue
        kind = str(detail.get("@type", ""))
        if kind.endswith("RetryInfo"):
            retry_after = _seconds(detail.get("retryDelay"))
        elif kind.endswith("QuotaFailure"):
            for violation in detail.get("violations") or []:
                quota = f"{violation.get('quotaId', '')} {violation.get('quotaMetric', '')}"
                daily = daily or "perday" in quota.lower().replace("_", "")
                no_free_quota = no_free_quota or str(violation.get("quotaValue", "")) == "0"
    if retry_after is None and headers is not None:
        retry_after = _seconds(headers.get("retry-after"))
    return RateLimitError(str(error.get("message", ""))[:300], retry_after, daily, no_free_quota)


Image = tuple[str, bytes]  # (type MIME, contenu), par exemple ("image/jpeg", b"...")


def images_of(observation: Observation | None) -> list[Image]:
    """La capture d'écran jointe à l'observation, si la vision est activée."""
    screenshot = getattr(observation, "screenshot", None)
    return [("image/jpeg", screenshot)] if screenshot else []


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


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
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429:  # quota : c'est l'agent qui décide d'attendre ou non, et il le dit
                raise parse_rate_limit(detail, exc.headers) from exc
            if exc.code in RETRY_CODES and attempt < retries:
                time.sleep(5 * 2**attempt)  # 5 s, puis 10 s, puis 20 s
                continue
            raise ProviderError(f"Erreur API {exc.code} : {detail[:500]}") from exc
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

    def build_request(self, system: str, user: str, images: list[Image] = (), max_tokens: int | None = None,
                      ) -> tuple[str, dict, dict]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key}
        # Google conseille de placer l'image avant le texte qui en parle
        parts = [{"inlineData": {"mimeType": mime, "data": _b64(data)}} for mime, data in images]
        parts.append({"text": user})
        config = {"temperature": 0, "responseMimeType": "application/json"}
        if max_tokens:
            config["maxOutputTokens"] = max_tokens
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": config,
        }
        return url, headers, payload

    @staticmethod
    def parse_response(data: dict) -> str:
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"Réponse Gemini inattendue : {json.dumps(data)[:300]}") from exc
        return "".join(part.get("text", "") for part in parts)

    def complete(self, system: str, user: str, images: list[Image] = (), max_tokens: int | None = None) -> str:
        return self.parse_response(post_json(*self.build_request(system, user, images, max_tokens)))

    def next_action(self, system: str, user: str, observation: Observation) -> str:
        return self.complete(system, user, images_of(observation))


class ClaudeProvider:
    """Anthropic Claude (API payante à l'usage)."""

    name = "claude"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ProviderError("Clé ANTHROPIC_API_KEY absente : ajoute-la dans le fichier .env")
        self.model = model or os.getenv("CLAUDE_MODEL") or DEFAULT_CLAUDE_MODEL

    def build_request(self, system: str, user: str, images: list[Image] = (), max_tokens: int | None = None,
                      ) -> tuple[str, dict, dict]:
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        content: str | list = user
        if images:
            content = [{"type": "image", "source": {"type": "base64", "media_type": mime, "data": _b64(data)}}
                       for mime, data in images] + [{"type": "text", "text": user}]
        payload = {
            "model": self.model,
            "max_tokens": max_tokens or 1024,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        return url, headers, payload

    @staticmethod
    def parse_response(data: dict) -> str:
        blocks = data.get("content") if isinstance(data, dict) else None
        if not blocks:
            raise ProviderError(f"Réponse Claude inattendue : {json.dumps(data)[:300]}")
        return "".join(block.get("text", "") for block in blocks if block.get("type") == "text")

    def complete(self, system: str, user: str, images: list[Image] = (), max_tokens: int | None = None) -> str:
        return self.parse_response(post_json(*self.build_request(system, user, images, max_tokens)))

    def next_action(self, system: str, user: str, observation: Observation) -> str:
        return self.complete(system, user, images_of(observation))


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
        if step.get("error") == "rate_limit":  # permet de simuler une erreur 429 de l'API
            raise RateLimitError("quota simulé", step.get("retry_after"), step.get("daily", False))
        args = dict(step.get("args", {}))
        target = step.get("target")
        if target is not None:
            element = next((e for e in observation.elements if e.selector == target), None)
            args["ref"] = element.ref if element else -1
        return json.dumps(
            {"action": step["action"], "args": args, "reason": step.get("reason", "Étape du scénario")},
            ensure_ascii=False,
        )

    def complete(self, system: str, user: str, images: list[Image] = (), max_tokens: int | None = None) -> str:
        """Réponse libre (diagnostic, propositions) : le « raw » de l'étape suivante du scénario."""
        if self.index >= len(self.steps):
            raise ProviderError("Scénario terminé : aucune réponse prévue pour cette demande")
        step = dict(self.steps[self.index])
        self.index += 1
        if step.get("error") == "rate_limit":
            raise RateLimitError("quota simulé", step.get("retry_after"), step.get("daily", False))
        return step["raw"] if "raw" in step else json.dumps(step, ensure_ascii=False)


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
