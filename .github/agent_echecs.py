"""Affiche les exécutions de l'agent qui n'ont pas réussi sur la page du run GitHub Actions.

Chaque exécution écrit resultat.json dans son dossier de rapport. Ce script en tire
une annotation par exécution qui n'est pas PASS : le cas, le navigateur, le statut,
la conclusion et les vérifications en échec, lisibles sans ouvrir le journal du job.

Usage : python .github/agent_echecs.py rapports
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


def annotations(reports: Path) -> list[str]:
    """Une ligne ::error par exécution qui n'est pas PASS."""
    files = sorted(reports.rglob("resultat.json")) if reports.is_dir() else []
    if not files:
        message = f"aucun resultat.json dans {reports} : l'agent s'est arrêté avant la fin, voir l'étape en échec."
        return [f"::warning title=Agent::{_data(message)}"]
    lines = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            lines.append(f"::warning title=Agent::{_data(f'{path} illisible : {exc}')}")
            continue
        if data.get("statut") == "PASS":
            continue
        where = ", ".join(part for part in (data.get("navigateur"), data.get("appareil")) if part)
        title = f"Agent : {data.get('titre', path.parent.name)}" + (f" ({where})" if where else "")
        message = f"{data.get('statut')} : {data.get('conclusion', '')}"
        failed = data.get("verifications_echouees") or []
        if failed:
            message += " Vérifications en échec : " + " ; ".join(failed)
        if data.get("interruption"):
            message += f" (interruption : {data['interruption']})"
        lines.append(f"::error title={_property(title)}::{_data(message)}")
    return lines


if __name__ == "__main__":
    for line in annotations(Path(sys.argv[1] if len(sys.argv) > 1 else "rapports")):
        print(line)
