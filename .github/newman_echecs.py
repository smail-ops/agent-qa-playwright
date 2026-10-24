"""Affiche les requêtes Postman en échec sur la page du run GitHub Actions.

Newman écrit son bilan en JSON (--reporters cli,json --reporter-json-export ...).
Ce script en tire une annotation par vérification en échec : la requête, le test
et le message, lisibles sur la page du run sans ouvrir le journal du job.

Usage : python .github/newman_echecs.py resultats-newman/newman.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _data(text: str) -> str:
    """Échappement des commandes GitHub Actions (::error ...::message)."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _property(text: str) -> str:
    return _data(text).replace(":", "%3A").replace(",", "%2C")


def annotations(report: Path) -> list[str]:
    """Une ligne ::error par vérification en échec, à imprimer dans le job."""
    if not report.is_file():
        message = f"{report} absent : Newman ne s'est pas lancé, voir l'étape précédente."
        return [f"::warning title=Newman::{_data(message)}"]
    data = json.loads(report.read_text(encoding="utf-8"))
    lines = []
    for failure in data.get("run", {}).get("failures", []):
        error = failure.get("error") or {}
        item = (failure.get("source") or {}).get("name") or "requête"
        test = error.get("test") or error.get("name") or "erreur"
        message = " ".join(str(error.get("message") or "").split())
        lines.append(f"::error title={_property(f'Postman : {item}')}::{_data(f'{test} : {message}')}")
    return lines


if __name__ == "__main__":
    for line in annotations(Path(sys.argv[1] if len(sys.argv) > 1 else "resultats-newman/newman.json")):
        print(line)
