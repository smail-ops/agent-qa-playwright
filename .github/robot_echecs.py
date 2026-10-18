"""Affiche les tests Robot Framework en échec sur la page du run GitHub Actions.

Le journal complet d'un job n'est lisible qu'une fois connecté à GitHub. Les
annotations, elles, s'affichent sur la page du run : chaque test en échec y
apparaît avec son fichier et son message. Le résumé du job reprend la liste.

Usage : python .github/robot_echecs.py resultats-robot/output.xml
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from robot.api import ExecutionResult, ResultVisitor


def _data(text: str) -> str:
    """Échappement des commandes GitHub Actions (::error ...::message)."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _property(text: str) -> str:
    return _data(text).replace(":", "%3A").replace(",", "%2C")


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


class _Echecs(ResultVisitor):
    def __init__(self) -> None:
        self.tests: list[tuple[str, str, str]] = []  # (test, fichier, message)

    def visit_test(self, test) -> None:
        if test.failed:
            source = Path(str(test.source)).name if test.source else ""
            self.tests.append((test.name, source, " ".join(test.message.split())))


def annotations(output: Path) -> list[str]:
    """Une ligne ::error par test en échec, à imprimer dans le job."""
    if not output.is_file():
        message = f"{output} absent : Robot ne s'est pas lancé, voir l'étape précédente."
        return [f"::warning title=Robot Framework::{_data(message)}"]
    echecs = _Echecs()
    ExecutionResult(str(output)).visit(echecs)
    return [f"::error title={_property(f'Robot : {test}')}::{_data(f'{source} : {message}')}"
            for test, source, message in echecs.tests]


def summary(output: Path) -> str:
    """Tableau Markdown des tests en échec, pour le résumé du job."""
    if not output.is_file():
        return ""
    echecs = _Echecs()
    ExecutionResult(str(output)).visit(echecs)
    if not echecs.tests:
        return ""
    rows = [f"| {_cell(test)} | {_cell(source)} | {_cell(message)} |" for test, source, message in echecs.tests]
    return "### Tests Robot en échec\n\n| Test | Fichier | Message |\n|---|---|---|\n" + "\n".join(rows) + "\n"


def main(argv: list[str]) -> int:
    output = Path(argv[1] if len(argv) > 1 else "resultats-robot/output.xml")
    for line in annotations(output):
        print(line)
    table = summary(output)
    if table and os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(table)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
