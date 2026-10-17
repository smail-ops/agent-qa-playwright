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

from .codegen import write_script
from .codegen_robot import write_robot
from .providers import Provider
from .report import write_report
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
) -> RunResult:
    """Lance le navigateur, exécute le cas de test, puis écrit le rapport et les deux tests générés."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        try:
            page = browser.new_page(viewport=VIEWPORT)
            agent = Agent(provider, max_steps=max_steps, pause_s=pause_s, on_event=on_event, stop_event=stop_event)
            result = agent.run(page, case, Path(out_dir))
        finally:
            browser.close()
    write_script(result)
    write_robot(result)
    write_report(result)
    return result
