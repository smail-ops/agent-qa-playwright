"""Exécution complète d'un cas de test : navigateur, agent, puis fichiers produits.

Utilisée par la ligne de commande (python -m agent) et par l'interface Alfred
(python -m alfred), pour que les deux produisent exactement les mêmes résultats.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from .anomalie import diagnose, needs_analysis, write_anomaly
from .codegen import write_script
from .codegen_gherkin import write_gherkin
from .codegen_robot import write_robot
from .memoire import Memory
from .providers import Provider
from .report import write_report
from .resultat import write_result
from .runner import Agent, RunResult
from .testcase import TestCase

VIEWPORT = {"width": 1280, "height": 800}


def new_out_dir(base: str | Path, case: TestCase) -> Path:
    """Dossier du rapport : rapports/<date>_<cas_de_test>, sans écraser un dossier existant."""
    path = Path(base) / f"{datetime.now():%Y%m%d-%H%M%S}_{case.slug}"
    candidate, index = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}-{index}")
        index += 1
    return candidate


def memory_for(provider: Provider, folder: str | Path = "memoire") -> Memory | None:
    """La mémoire sert aux vrais modèles. La démo sans IA rejoue déjà un scénario enregistré."""
    return None if provider.name == "scripted" else Memory(folder)


def run_case(
    case: TestCase,
    provider: Provider,
    out_dir: str | Path,
    *,
    headed: bool = False,
    max_steps: int = 20,
    pause_s: float = 0.0,
    on_event: Callable[[str, dict], None] | None = None,
    stop_event: threading.Event | None = None,
    memory: Memory | None = None,
    vision: bool = False,
) -> RunResult:
    """Lance le navigateur, exécute le cas de test, puis écrit le rapport et les tests générés."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        try:
            page = browser.new_page(viewport=VIEWPORT)
            agent = Agent(provider, max_steps=max_steps, pause_s=pause_s, on_event=on_event, stop_event=stop_event,
                          memory=memory, vision=vision)
            result = agent.run(page, case, Path(out_dir))
            result.browser = f"Chromium {browser.version}"
        finally:
            browser.close()
    write_script(result)
    write_robot(result)
    write_gherkin(result)
    if needs_analysis(result):  # échec ou blocage : fiche d'anomalie, avec le diagnostic du modèle si possible
        if on_event is not None and provider.name != "scripted":
            on_event("analysis", {})
        diagnosis, missing = diagnose(result, provider)
        write_anomaly(result, diagnosis, missing)
    write_report(result)
    write_result(result)  # resultat.json : le même bilan, lisible par un programme (Alfred, intégration continue)
    return result
