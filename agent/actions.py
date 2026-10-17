"""Les actions que le modèle peut demander, et la lecture de sa réponse JSON.

Le modèle répond avec un objet JSON comme :

    {"action": "fill", "args": {"ref": 1, "text": "standard_user"}, "reason": "..."}

Ce module vérifie que la réponse est valide avant toute exécution : une IA peut
se tromper, le programme ne doit jamais exécuter une action mal formée.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# Nom de chaque action -> arguments obligatoires et leur type
ACTIONS: dict[str, dict[str, type]] = {
    "goto": {"url": str},
    "click": {"ref": int},
    "fill": {"ref": int, "text": str},
    "select": {"ref": int, "value": str},
    "check_text": {"text": str},
    "check_url": {"contains": str},
    "finish": {"status": str},
}

# Arguments facultatifs. « absent » transforme une vérification en vérification
# négative : « le message d'erreur ne s'affiche pas », « l'URL ne contient pas ».
OPTIONAL_ARGS: dict[str, dict[str, type]] = {
    "check_text": {"absent": bool},
    "check_url": {"absent": bool},
}

STATUSES = ("PASS", "FAIL", "BLOQUE")
_STATUS_ALIASES = {"BLOQUÉ": "BLOQUE", "BLOCKED": "BLOQUE", "PASSED": "PASS", "FAILED": "FAIL", "OK": "PASS", "KO": "FAIL"}


@dataclass
class Action:
    """Une action demandée par le modèle."""

    name: str
    args: dict = field(default_factory=dict)
    reason: str = ""


def _extract_json(raw: str) -> str:
    """Isole l'objet JSON, même si le modèle l'entoure de texte ou de ```json."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
    if fenced:
        return fenced.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("la réponse ne contient pas d'objet JSON")
    return raw[start : end + 1]


def _convert(value: object, arg_type: type) -> object:
    """Convertit une valeur JSON dans le type attendu, sans conversion hasardeuse.

    Sans ce contrôle, null deviendrait le texte « None » et true deviendrait 1.
    """
    if value is None:
        raise ValueError("valeur vide")
    if arg_type is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise ValueError("true ou false attendu")
    if isinstance(value, bool):
        raise ValueError("booléen inattendu")
    if arg_type is int and isinstance(value, float) and not value.is_integer():
        raise ValueError("nombre entier attendu")
    return arg_type(value)


def parse_action(raw: str) -> Action:
    """Transforme la réponse texte du modèle en Action validée.

    Lève ValueError avec un message clair si la réponse est inutilisable.
    Ce message est renvoyé au modèle pour qu'il corrige sa réponse.
    """
    try:
        data = json.loads(_extract_json(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON invalide ({exc.msg})") from exc
    if not isinstance(data, dict):
        raise ValueError("la réponse doit être un objet JSON")

    name = str(data.get("action", "")).strip()
    if name not in ACTIONS:
        raise ValueError(f"action inconnue « {name} » (actions possibles : {', '.join(ACTIONS)})")

    args = data.get("args") or {}
    if not isinstance(args, dict):
        raise ValueError("« args » doit être un objet JSON")

    clean_args: dict = {}
    for arg_name, arg_type in ACTIONS[name].items():
        if arg_name not in args:
            raise ValueError(f"argument « {arg_name} » manquant pour l'action {name}")
        try:
            clean_args[arg_name] = _convert(args[arg_name], arg_type)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"argument « {arg_name} » invalide pour l'action {name}") from exc

    for arg_name, arg_type in OPTIONAL_ARGS.get(name, {}).items():
        if arg_name in args:
            try:
                clean_args[arg_name] = _convert(args[arg_name], arg_type)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"argument « {arg_name} » invalide pour l'action {name}") from exc

    if name == "finish":
        status = clean_args["status"].strip().upper()
        status = _STATUS_ALIASES.get(status, status)
        if status not in STATUSES:
            raise ValueError(f"statut « {status} » invalide (attendu : PASS, FAIL ou BLOQUE)")
        clean_args["status"] = status
        clean_args["summary"] = str(args.get("summary", "")).strip()

    return Action(name=name, args=clean_args, reason=str(data.get("reason", "")).strip())
