"""Données uniques : « {unique} » dans un cas de test devient une valeur différente à chaque exécution.

Un formulaire d'inscription refuse une adresse déjà utilisée ; une fiche créée deux
fois avec le même nom se confond avec la première. Le testeur écrit alors :

    2. Saisir « alfred.{unique}@exemple.fr » dans le champ E-mail

À chaque exécution, le programme remplace {unique} par la date et l'heure, par
exemple 1023143005 (23 octobre, 14 h 30 min 05 s). Le modèle lit la vraie valeur,
comme un testeur. Le rapport la note, pour retrouver la donnée créée.

Les tests générés, eux, ne la recopient pas : ils créent leur propre valeur à chaque
lancement (variable unique en Python, ${UNIQUE} avec Robot Framework, {unique} dans
le scénario Gherkin). La mémoire des parcours garde aussi {unique}, jamais la valeur.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .actions import Action

UNIQUE = "{unique}"
FORMAT = "%m%d%H%M%S"  # mois, jour, heure, minutes, secondes : 10 chiffres, valables dans un e-mail ou un identifiant
_TOKEN = re.compile(r"\{\s*unique\s*\}", re.IGNORECASE)  # {unique}, {Unique}, { unique }


def valeur_unique(now: datetime | None = None) -> str:
    """La valeur de cette exécution : 1023143005 le 23 octobre à 14 h 30 min 05 s."""
    return (now or datetime.now()).strftime(FORMAT)


def contient(text: str) -> bool:
    """Le texte demande-t-il une valeur unique ?"""
    return bool(_TOKEN.search(text or ""))


def remplacer(text: str, value: str | None) -> str:
    """Le texte avec la valeur de l'exécution à la place de {unique}."""
    return _TOKEN.sub(lambda match: value, text) if value else text


def masquer(text: str, value: str | None) -> str:
    """L'inverse : {unique} à la place de la valeur de l'exécution (pour la mémoire et les tests générés)."""
    return text.replace(value, UNIQUE) if value and text else text


def morceaux(text: str, value: str | None) -> list[tuple[bool, str]]:
    """Le texte en morceaux : (False, texte tel quel) ou (True, "") à la place de chaque valeur unique."""
    if not value or value not in text:
        return [(False, text)]
    parts: list[tuple[bool, str]] = []
    for index, chunk in enumerate(text.split(value)):
        if index:
            parts.append((True, ""))
        if chunk:
            parts.append((False, chunk))
    return parts


def _map(value, change):
    """Applique change aux textes d'un argument d'action : une chaîne, ou une liste de chaînes (check_order)."""
    if isinstance(value, str):
        return change(value)
    if isinstance(value, list):
        return [change(item) if isinstance(item, str) else item for item in value]
    return value


def remplacer_action(action: Action, value: str | None) -> Action:
    """L'action avec la valeur de l'exécution à la place de {unique} (le modèle l'a peut-être recopié tel quel)."""
    if not value:
        return action
    args = {name: _map(arg, lambda text: remplacer(text, value)) for name, arg in action.args.items()}
    return replace(action, args=args) if args != action.args else action


def masquer_args(args: dict, value: str | None) -> dict:
    """Les arguments d'une action, avec {unique} à la place de la valeur de l'exécution."""
    if not value:
        return dict(args)
    return {name: _map(arg, lambda text: masquer(text, value)) for name, arg in args.items()}
