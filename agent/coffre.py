"""Mots de passe du cas de test : jamais envoyés au modèle, jamais écrits dans les fichiers produits.

Le testeur écrit le mot de passe dans le cas de test, entre guillemets, sur la ligne qui parle du
mot de passe : « Saisir « secret_sauce » dans le champ Mot de passe ». Le programme le remplace par
un repère, « [mot de passe] », dans tout ce qui part au modèle. Le modèle saisit ce repère, et le
programme tape le vrai mot de passe à sa place. Le rapport, la fiche d'anomalie et la mémoire ne
gardent que le repère.

Les tests générés, eux, rejouent sans le programme : ils lisent le mot de passe dans le fichier
.env du projet (exclu de Git), où l'agent l'écrit sous un nom comme MOT_DE_PASSE_SAUCEDEMO_COM.
Un même mot de passe sur un même site garde le même nom d'un cas de test à l'autre.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from .actions import Action

PLACEHOLDER = "[mot de passe]"
ENV_PREFIX = "MOT_DE_PASSE_"
_KEYWORD = re.compile(r"mots? de passe|password|passwd|\bmdp\b|passcode|code secret|code pin|\bpin\b", re.IGNORECASE)
_QUOTED = re.compile(r"«\s*([^«»]+?)\s*»|\"([^\"]+)\"|“([^”]+)”")
_ARGS = ("text", "value")  # arguments d'une action qui peuvent contenir un mot de passe (fill, check_value)


def _values(case_text: str) -> list[str]:
    """Les mots de passe du cas : sur une ligne qui parle de mot de passe, la valeur entre guillemets la plus proche.

    Une valeur qui contient elle-même le mot (« Mot de passe incorrect », le champ « Password ») n'en est pas un.
    """
    found: list[str] = []
    for line in case_text.splitlines():
        keyword = _KEYWORD.search(line)
        if keyword is None:
            continue
        quoted = [(match.start(), next(group for group in match.groups() if group).strip())
                  for match in _QUOTED.finditer(line)]
        quoted = [(position, value) for position, value in quoted
                  if value and not _KEYWORD.search(value) and not value.startswith("[")]
        if not quoted:
            continue
        value = min(quoted, key=lambda item: abs(item[0] - keyword.start()))[1]
        if value not in found:
            found.append(value)
    return found


def _word(value: str) -> re.Pattern:
    """La valeur en mot entier : le mot de passe « admin » ne touche pas à « administrateur »."""
    return re.compile(rf"(?<![\w]){re.escape(value)}(?![\w])")


@dataclass
class Coffre:
    """Les mots de passe d'un cas de test : repère -> valeur."""

    secrets: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_case(cls, case_text: str) -> Coffre:
        values = _values(case_text)
        if len(values) == 1:
            return cls({PLACEHOLDER: values[0]})
        return cls({f"[mot de passe {index}]": value for index, value in enumerate(values, 1)})

    def hide(self, text: str) -> str:
        """Le texte, avec un repère à la place de chaque mot de passe (le plus long d'abord)."""
        for placeholder, value in sorted(self.secrets.items(), key=lambda item: -len(item[1])):
            text = _word(value).sub(placeholder, text)
        return text

    def reveal(self, text: str) -> str:
        """Le texte, avec le vrai mot de passe à la place de chaque repère."""
        for placeholder, value in self.secrets.items():
            text = text.replace(placeholder, value)
        return text

    def _with(self, action: Action, change) -> Action:
        args = {name: change(value) if name in _ARGS and isinstance(value, str) else value for name, value in action.args.items()}
        return replace(action, args=args) if args != action.args else action

    def hide_action(self, action: Action) -> Action:
        """L'action telle qu'elle est notée (historique, rapport, mémoire) : avec le repère, jamais le mot de passe."""
        return self._with(action, self.hide)

    def reveal_action(self, action: Action) -> Action:
        """L'action telle qu'elle est exécutée : avec le vrai mot de passe."""
        return self._with(action, self.reveal)

    def placeholders_in(self, text: str) -> list[str]:
        return [placeholder for placeholder in self.secrets if placeholder in text]


# --- Le fichier .env, pour les tests générés ---------------------------------------------


def site_name(url: str) -> str:
    """Le site, pour nommer la variable : https://www.saucedemo.com/ donne SAUCEDEMO_COM."""
    host = (urlsplit(url).hostname or "site").lower()
    host = host[4:] if host.startswith("www.") else host
    return re.sub(r"[^A-Z0-9]+", "_", host.upper()).strip("_") or "SITE"


def _read_env(env_path: Path) -> dict[str, str]:
    """Les variables MOT_DE_PASSE_* du fichier .env, comme les lit python-dotenv."""
    try:
        from dotenv import dotenv_values

        values = dotenv_values(env_path, interpolate=False) if env_path.exists() else {}
    except (OSError, ImportError):
        values = {}
    return {name: value for name, value in values.items() if name.startswith(ENV_PREFIX) and value is not None}


def _env_line(name: str, value: str) -> str:
    if "'" not in value:
        return f"{name}='{value}'"  # entre apostrophes : ni échappement, ni ${...} interprété
    return f'{name}="' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def store(url: str, coffre: Coffre, placeholders: list[str], env_path: Path | str = ".env") -> dict[str, str]:
    """Nomme les mots de passe dont les tests générés ont besoin, et écrit ceux qui manquent dans le fichier .env.

    Renvoie repère -> nom de la variable. Un mot de passe déjà rangé pour ce site garde son nom.
    """
    env_path = Path(env_path)
    existing = _read_env(env_path)
    base = ENV_PREFIX + site_name(url)
    names, added = {}, []
    for placeholder in placeholders:
        value = coffre.secrets.get(placeholder)
        if value is None:
            continue
        name = next((key for key, known in existing.items() if known == value and (key == base or key.startswith(base + "_"))),
                    None)
        if name is None:
            name, index = base, 2
            while name in existing:
                name, index = f"{base}_{index}", index + 1
            existing[name] = value
            added.append(_env_line(name, value))
        names[placeholder] = name
        os.environ.setdefault(name, value)  # les tests générés peuvent tourner tout de suite
    if added:
        try:
            before = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
            header = "" if ENV_PREFIX in before else "# Mots de passe de test, lus par les tests générés (jamais écrits dans les tests)\n"
            separator = "" if not before or before.endswith("\n") else "\n"
            env_path.write_text(before + separator + header + "\n".join(added) + "\n", encoding="utf-8")
        except OSError:
            pass  # dossier protégé : les tests générés demanderont la variable
    return names


def env_names(run) -> dict[str, str]:
    """Repère -> variable du fichier .env, pour les tests générés d'une exécution."""
    if run.coffre is None or not run.coffre.secrets:
        return {}
    known = run.secrets or {}
    return {placeholder: known.get(placeholder) or default_name(run.case.url, placeholder) for placeholder in run.coffre.secrets}


def split(text: str, names: dict[str, str]) -> list[tuple[bool, str]]:
    """Le texte en morceaux : (False, texte tel quel) ou (True, variable du mot de passe)."""
    if not names or not any(placeholder in text for placeholder in names):
        return [(False, text)]
    pattern = re.compile("|".join(re.escape(placeholder) for placeholder in sorted(names, key=len, reverse=True)))
    parts, last = [], 0
    for match in pattern.finditer(text):
        if match.start() > last:
            parts.append((False, text[last:match.start()]))
        parts.append((True, names[match.group()]))
        last = match.end()
    if last < len(text):
        parts.append((False, text[last:]))
    return parts


def default_name(url: str, placeholder: str) -> str:
    """Nom de la variable quand le fichier .env n'a pas été consulté : MOT_DE_PASSE_SITE, MOT_DE_PASSE_SITE_2..."""
    index = re.search(r"(\d+)\]$", placeholder)
    suffix = f"_{index.group(1)}" if index and index.group(1) != "1" else ""
    return ENV_PREFIX + site_name(url) + suffix
