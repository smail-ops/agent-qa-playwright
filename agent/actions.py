"""Les actions que le modèle peut demander, et la lecture de sa réponse JSON.

Le modèle répond avec un objet JSON comme :

    {"action": "fill", "args": {"ref": 1, "text": "standard_user"}, "reason": "..."}

Ce module vérifie que la réponse est valide avant toute exécution : une IA peut
se tromper, le programme ne doit jamais exécuter une action mal formée.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

# Nom de chaque action -> arguments obligatoires et leur type
ACTIONS: dict[str, dict[str, type]] = {
    "goto": {"url": str},
    "click": {"ref": int},
    "fill": {"ref": int, "text": str},
    "select": {"ref": int, "value": str},
    "check_text": {"text": str},
    "check_url": {"contains": str},
    "check_order": {"texts": list},
    "check_count": {"text": str, "count": int},
    "check_value": {"ref": int, "value": str},
    "check_state": {"ref": int, "state": str},
    "check_visual": {"expected": str, "seen": str, "ok": bool},
    "wait": {"seconds": int},
    "dialog": {"accept": bool},
    "press": {"key": str},
    "hover": {"ref": int},
    "scroll": {},
    "upload": {"ref": int, "file": str},
    "drag": {"ref": int, "to": int},
    "switch_tab": {"index": int},
    "finish": {"status": str},
}

MAX_WAIT_S = 30  # attente la plus longue que l'action wait accepte

# Les vérifications : leur résultat (vrai ou faux) décide du statut final, pas l'avis du modèle.
CHECKS = ("check_text", "check_url", "check_order", "check_count", "check_value", "check_state", "check_visual")

# États qu'une vérification check_state peut constater, avec les synonymes français acceptés.
# (La visibilité passe par check_text : l'agent ne voit que les éléments affichés.)
STATES = ("enabled", "disabled", "checked", "unchecked")
STATE_LABELS = {"enabled": "actif", "disabled": "grisé", "checked": "coché", "unchecked": "décoché"}
_STATE_ALIASES = {"actif": "enabled", "activé": "enabled", "grisé": "disabled", "désactivé": "disabled",
                  "coché": "checked", "décoché": "unchecked"}

# Arguments facultatifs. « absent » transforme une vérification en vérification
# négative : « le message d'erreur ne s'affiche pas », « l'URL ne contient pas ».
# « regex » : la valeur attendue est un motif (un code généré, une date), pas un texte exact.
# « text » de wait : le texte dont on attend l'affichage ; « text » de dialog : la saisie d'une fenêtre prompt.
# « double » et « button » de click : double-clic, clic droit. « ref » de press : la touche est tapée dans cet élément.
# scroll : « ref » (jusqu'à un élément) ou « to » (bottom, top).
OPTIONAL_ARGS: dict[str, dict[str, type]] = {
    "check_text": {"absent": bool},
    "check_url": {"absent": bool},
    "check_value": {"regex": bool},
    "wait": {"text": str},
    "dialog": {"text": str},
    "click": {"double": bool, "button": str},
    "press": {"ref": int},
    "scroll": {"ref": int, "to": str},
}

# Touches du clavier (noms de Playwright), et leurs noms en français : « Entrée », « Échap »...
KEYS = ("Enter", "Escape", "Tab", "Backspace", "Delete", "Space", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
        "Home", "End", "PageUp", "PageDown") + tuple(f"F{n}" for n in range(1, 13))
KEY_LABELS = {"Enter": "Entrée", "Escape": "Échap", "Tab": "Tab", "Backspace": "Retour arrière", "Delete": "Suppr",
              "Space": "Espace", "ArrowUp": "Flèche haut", "ArrowDown": "Flèche bas", "ArrowLeft": "Flèche gauche",
              "ArrowRight": "Flèche droite", "Home": "Début", "End": "Fin", "PageUp": "Page précédente",
              "PageDown": "Page suivante", "Control": "Ctrl", "Shift": "Maj", "Alt": "Alt", "Meta": "Cmd"}
_KEY_ALIASES = {
    "entree": "Enter", "return": "Enter", "retour": "Enter", "echap": "Escape", "esc": "Escape", "echappement": "Escape",
    "tabulation": "Tab", "espace": "Space", "retour arriere": "Backspace", "effacement": "Backspace", "suppr": "Delete",
    "supprimer": "Delete", "del": "Delete", "fleche haut": "ArrowUp", "fleche bas": "ArrowDown", "fleche gauche": "ArrowLeft",
    "fleche droite": "ArrowRight", "haut": "ArrowUp", "bas": "ArrowDown", "gauche": "ArrowLeft", "droite": "ArrowRight",
    "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight", "debut": "Home", "fin": "End",
    "page precedente": "PageUp", "page suivante": "PageDown",
}
_MODIFIERS = {"control": "Control", "ctrl": "Control", "shift": "Shift", "maj": "Shift", "alt": "Alt", "meta": "Meta",
              "cmd": "Meta", "command": "Meta", "commande": "Meta"}


def normalize_key(text: str) -> str:
    """Nom de touche pour Playwright : « entrée » donne Enter, « ctrl+a » donne Control+A. Lève ValueError sinon."""
    parts = [part.strip() for part in str(text).replace(" + ", "+").split("+")] if str(text).strip() != "+" else ["+"]
    if not parts or any(not part for part in parts):
        raise ValueError(f"touche « {text} » inconnue")
    *modifiers, key = parts
    names = []
    for modifier in modifiers:
        plain = unicodedata.normalize("NFKD", modifier).encode("ascii", "ignore").decode().lower()
        if plain not in _MODIFIERS:
            raise ValueError(f"touche « {text} » inconnue")
        names.append(_MODIFIERS[plain])
    plain = " ".join(unicodedata.normalize("NFKD", key).encode("ascii", "ignore").decode().lower().split())
    known = {k.lower(): k for k in KEYS}
    if plain in known:
        names.append(known[plain])
    elif plain in _KEY_ALIASES:
        names.append(_KEY_ALIASES[plain])
    elif len(key) == 1 and key.isprintable() and not key.isspace():
        names.append(key.upper() if modifiers and key.isalpha() else key)
    else:
        raise ValueError(f"touche « {text} » inconnue (par exemple : Enter, Escape, Tab, ArrowDown, Control+A)")
    return "+".join(names)


def key_label(key: str) -> str:
    """Nom d'une touche, en français : « Entrée », « Ctrl+A »."""
    return "+".join(KEY_LABELS.get(part, part) for part in key.split("+"))


def au(target: str) -> str:
    """« à » devant un nom d'élément, contracté : au champ, aux options, à la case, à l'onglet."""
    if target.startswith("le "):
        return "au " + target[3:]
    if target.startswith("les "):
        return "aux " + target[4:]
    return "à " + target


def du(target: str) -> str:
    """« de » devant un nom d'élément, contracté : du bouton, des options, de la case, de l'onglet."""
    if target.startswith("le "):
        return "du " + target[3:]
    if target.startswith("les "):
        return "des " + target[4:]
    return "de " + target


def jusqua(target: str) -> str:
    """« jusqu'à » devant un nom d'élément, contracté : jusqu'au bouton, jusqu'à la liste."""
    return "jusqu'" + au(target)

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
    if arg_type is list:  # check_order : au moins deux textes non vides, dans l'ordre
        if not isinstance(value, list) or len(value) < 2:
            raise ValueError("liste d'au moins deux textes attendue")
        if any(isinstance(item, (bool, list, dict)) or item is None or not str(item).strip() for item in value):
            raise ValueError("chaque élément doit être un texte non vide")
        return [str(item).strip() for item in value]
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

    if name == "check_count" and (clean_args["count"] < 0 or not clean_args["text"].strip()):
        raise ValueError("arguments invalides pour l'action check_count (texte non vide, nombre positif ou nul)")
    if name == "check_visual" and (not clean_args["expected"].strip() or not clean_args["seen"].strip()):
        raise ValueError("arguments « expected » et « seen » obligatoires pour l'action check_visual")
    if name == "check_state":
        state = clean_args["state"].strip().lower()
        state = _STATE_ALIASES.get(state, state)
        if state not in STATES:
            raise ValueError(f"état « {state} » invalide (attendu : {', '.join(STATES)})")
        clean_args["state"] = state
    if name == "check_value" and clean_args.get("regex"):
        try:
            re.compile(clean_args["value"])
        except re.error as exc:
            raise ValueError(f"motif « {clean_args['value']} » invalide pour l'action check_value ({exc})") from exc
    if name == "wait":
        if not 1 <= clean_args["seconds"] <= MAX_WAIT_S:
            raise ValueError(f"argument « seconds » invalide pour l'action wait (de 1 à {MAX_WAIT_S} secondes)")
        if "text" in clean_args and not clean_args["text"].strip():
            raise ValueError("argument « text » vide pour l'action wait")
    if name == "dialog" and not clean_args["accept"]:
        clean_args.pop("text", None)  # une saisie n'a de sens que si l'on répond OK
    if name == "press":
        clean_args["key"] = normalize_key(clean_args["key"])
    if name == "click":
        button = clean_args.pop("button", "left").strip().lower()
        button = {"gauche": "left", "droit": "right", "droite": "right"}.get(button, button)
        if button not in ("left", "right"):
            raise ValueError(f"bouton « {button} » invalide pour l'action click (left ou right)")
        if not clean_args.get("double"):
            clean_args.pop("double", None)
        elif button == "right":
            raise ValueError("un double-clic se fait avec le bouton gauche")
        if button == "right":
            clean_args["button"] = "right"
    if name == "scroll":
        if "ref" in clean_args:
            clean_args.pop("to", None)
        else:
            to = clean_args.get("to", "").strip().lower()
            to = {"bas": "bottom", "fin": "bottom", "end": "bottom", "haut": "top", "debut": "top", "début": "top"}.get(to, to)
            if to not in ("bottom", "top"):
                raise ValueError("indique « ref » (faire défiler jusqu'à un élément) ou « to » : bottom ou top")
            clean_args["to"] = to
    if name == "upload":
        file = clean_args["file"].strip()
        if not re.fullmatch(r"[^/\\:]+\.\w+", file) or file.startswith("."):
            raise ValueError("argument « file » : un nom de fichier du dossier donnees_de_test, sans chemin (facture.pdf)")
        clean_args["file"] = file
    if name == "drag" and clean_args["ref"] == clean_args["to"]:
        raise ValueError("arguments invalides pour l'action drag : « ref » et « to » doivent être deux éléments différents")
    if name == "switch_tab" and clean_args["index"] < 1:
        raise ValueError("argument « index » invalide pour l'action switch_tab (1 pour le premier onglet)")

    if name == "finish":
        status = clean_args["status"].strip().upper()
        status = _STATUS_ALIASES.get(status, status)
        if status not in STATUSES:
            raise ValueError(f"statut « {status} » invalide (attendu : PASS, FAIL ou BLOQUE)")
        clean_args["status"] = status
        clean_args["summary"] = str(args.get("summary", "")).strip()

    return Action(name=name, args=clean_args, reason=str(data.get("reason", "")).strip())
