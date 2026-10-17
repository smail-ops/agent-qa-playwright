"""Le résultat d'une exécution, lisible par un programme : resultat.json dans le dossier du rapport.

Alfred s'en sert pour répondre aux questions sur les tests (« quels tests ont
échoué aujourd'hui ? », « pourquoi le dernier test a échoué ? »), et une
intégration continue peut le lire sans analyser le rapport Markdown.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import RunResult

FILE = "resultat.json"
VERSION = 1
_FOLDER_DATE = re.compile(r"^(\d{8}-\d{6})_")


def summary(run: RunResult) -> dict:
    """L'essentiel d'une exécution : statut, conclusion, vérifications en échec, diagnostic, fichiers produits."""
    files = {
        "rapport": run.report_path, "pytest": run.script_path, "robot": run.robot_path,
        "gherkin": run.feature_path, "robot_bdd": run.bdd_path, "anomalie": run.anomaly_path,
    }
    return {
        "version": VERSION,
        "titre": run.case.title,
        "url": run.case.url,
        "statut": run.status,
        "conclusion": run.summary,
        "debut": run.started_at.isoformat(timespec="seconds"),
        "duree_s": round(run.duration_s, 1),
        "modele": f"{run.provider} ({run.model})",
        "appels_modele": run.model_calls,
        "etapes_rejouees_depuis_la_memoire": run.memory_replayed,
        "vision": run.vision,
        "verifications_reussies": sum(1 for step in run.checks if step.result.check),
        "verifications_echouees": [step.result.detail for step in run.checks if step.result.check is False],
        "diagnostic": run.diagnosis,
        "fichiers": {name: path.name for name, path in files.items() if path is not None},
    }


def write_result(run: RunResult) -> Path:
    path = run.out_dir / FILE
    path.write_text(json.dumps(summary(run), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _from_report(folder: Path) -> dict | None:
    """Rapport d'avant resultat.json : le titre, le statut et la conclusion suffisent."""
    try:
        text = (folder / "rapport.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    title = re.search(r"^# Rapport : (.+)$", text, flags=re.MULTILINE)
    status = re.search(r"^\| Statut \| \*\*\S+ (PASS|FAIL|BLOQU[EÉ])\*\* \|", text, flags=re.MULTILINE)
    if not title or not status:
        return None
    conclusion = re.search(r"^\*\*Conclusion :\*\* (.*)$", text, flags=re.MULTILINE)
    stamp = _FOLDER_DATE.match(folder.name)
    started = datetime.strptime(stamp.group(1), "%Y%m%d-%H%M%S").isoformat() if stamp else ""
    return {"titre": title.group(1).strip(), "statut": status.group(1).replace("É", "E"),
            "conclusion": conclusion.group(1).strip() if conclusion else "", "debut": started}


def read_results(reports_dir: str | Path, limit: int = 15) -> list[dict]:
    """Les dernières exécutions, de la plus récente à la plus ancienne (le nom du dossier commence par la date)."""
    folders = [p for p in Path(reports_dir).glob("*") if p.is_dir() and _FOLDER_DATE.match(p.name)]
    results = []
    for folder in sorted(folders, key=lambda p: p.name, reverse=True):
        try:
            data = json.loads((folder / FILE).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            data = _from_report(folder)
        if isinstance(data, dict) and data.get("titre") and data.get("statut"):
            results.append({**data, "dossier": folder.name})
        if len(results) >= limit:
            break
    return results
