"""Affiche les tests pytest en échec sur la page du run GitHub Actions.

Le journal complet d'un job n'est lisible qu'une fois connecté à GitHub. Les
annotations, elles, s'affichent sur la page du run : chaque test en échec y
apparaît avec la fin de son message d'erreur. Le résumé du job reprend la liste.

pytest écrit son bilan au format JUnit (--junitxml=resultats-pytest/junit.xml).

Usage : python .github/pytest_echecs.py resultats-pytest/junit.xml
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_MESSAGE = 1800  # caractères gardés : la fin du message, là où pytest écrit l'assertion qui a échoué


def _data(text: str) -> str:
    """Échappement des commandes GitHub Actions (::error ...::message)."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _property(text: str) -> str:
    return _data(text).replace(":", "%3A").replace(",", "%2C")


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def echecs(report: Path) -> list[tuple[str, str]]:
    """(test, message) de chaque test en échec ou en erreur."""
    found = []
    for case in ET.parse(report).getroot().iter("testcase"):
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        if problem is None:
            continue
        name = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
        text = (problem.text or problem.get("message") or "").strip()
        found.append((name, text[-MAX_MESSAGE:]))
    return found


def annotations(report: Path) -> list[str]:
    """Une ligne ::error par test en échec, à imprimer dans le job."""
    if not report.is_file():
        message = f"{report} absent : pytest ne s'est pas lancé, voir l'étape précédente."
        return [f"::warning title=pytest::{_data(message)}"]
    return [f"::error title={_property(f'pytest : {name}')}::{_data(message)}" for name, message in echecs(report)]


def _erreur(message: str) -> str:
    """La ligne qui dit l'erreur : la première que pytest marque « E », sinon la dernière."""
    lines = message.splitlines()
    marked = [line[1:].strip() for line in lines if line.startswith("E ")]
    return marked[0] if marked else (lines[-1] if lines else "")


def summary(report: Path) -> str:
    """Tableau Markdown des tests en échec, pour le résumé du job."""
    if not report.is_file():
        return ""
    rows = [f"| {_cell(name)} | {_cell(_erreur(message))} |" for name, message in echecs(report)]
    if not rows:
        return ""
    return "### Tests pytest en échec\n\n| Test | Erreur |\n|---|---|\n" + "\n".join(rows) + "\n"


def main(argv: list[str]) -> int:
    report = Path(argv[1] if len(argv) > 1 else "resultats-pytest/junit.xml")
    for line in annotations(report):
        print(line)
    table = summary(report)
    if table and os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(table)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
