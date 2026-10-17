"""La boucle de l'agent : observer la page, demander une action au modèle, l'exécuter.

    navigateur (Playwright)  --- état de la page --->  modèle (Gemini ou Claude)
    navigateur (Playwright)  <--- action en JSON ----  modèle (Gemini ou Claude)

On recommence jusqu'à ce que le modèle termine le test (finish), ou qu'une
limite soit atteinte (nombre d'actions, erreurs à la suite).

Une interface (Alfred) peut suivre l'exécution en direct grâce à on_event,
et demander l'arrêt grâce à stop_event.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from .actions import Action, parse_action
from .browser import ActionResult, Observation, execute, observe
from .prompts import SYSTEM_PROMPT, build_user_message
from .providers import Provider, ProviderError
from .testcase import TestCase


@dataclass
class Step:
    """Une étape du test, telle qu'elle apparaîtra dans le rapport."""

    number: int
    action: Action | None  # None si la réponse du modèle était illisible
    result: ActionResult
    screenshot: str | None = None  # chemin relatif au dossier du rapport
    raw: str = ""  # réponse brute du modèle, utile pour comprendre une erreur


@dataclass
class RunResult:
    """Bilan complet d'une exécution."""

    case: TestCase
    provider: str
    model: str
    status: str  # PASS, FAIL ou BLOQUE
    summary: str
    steps: list[Step]
    started_at: datetime
    duration_s: float
    out_dir: Path
    report_path: Path | None = None
    script_path: Path | None = None  # test pytest-playwright généré
    robot_path: Path | None = None  # test Robot Framework / SeleniumLibrary généré

    @property
    def checks(self) -> list[Step]:
        return [s for s in self.steps if s.result.check is not None]


def final_status(declared: str, summary: str, steps: list[Step]) -> tuple[str, str]:
    """Le statut final s'appuie sur les vérifications réelles, pas seulement sur l'avis du modèle."""
    checks = [s.result.check for s in steps if s.result.check is not None]
    if any(check is False for check in checks):
        if declared == "FAIL":
            return "FAIL", summary
        return "FAIL", f"Au moins une vérification a échoué. {summary}".strip()
    if declared == "PASS" and not checks:
        return "BLOQUE", "Le modèle a conclu PASS sans aucune vérification : test non concluant."
    return declared, summary


STOPPED_SUMMARY = "Mission arrêtée à la demande de l'utilisateur."


class Agent:
    """Exécute un cas de test avec un fournisseur de modèle donné."""

    def __init__(
        self,
        provider: Provider,
        max_steps: int = 20,
        pause_s: float = 0.0,
        max_errors: int = 3,
        on_event: Callable[[str, dict], None] | None = None,
        stop_event: threading.Event | None = None,
    ):
        self.provider = provider
        self.max_steps = max_steps
        self.pause_s = pause_s
        self.max_errors = max_errors
        self.on_event = on_event  # appelée à chaque phase (observe, think, act) et à chaque étape
        self.stop_event = stop_event  # arrêt demandé depuis l'interface

    def _emit(self, kind: str, **data) -> None:
        if self.on_event is not None:
            self.on_event(kind, data)

    def _add(self, steps: list[Step], step: Step) -> Step:
        steps.append(step)
        self._emit("step", step=step)
        return step

    def _stop_requested(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def _pause(self) -> None:
        if self.stop_event is not None:
            self.stop_event.wait(self.pause_s)  # un arrêt demandé interrompt la pause
        else:
            time.sleep(self.pause_s)

    def _do(self, page: Page, action: Action, observation: Observation | None, out_dir: Path, number: int, raw: str = "") -> Step:
        self._emit("phase", phase="act", number=number, action=action, observation=observation)
        result = execute(page, action, observation)
        screenshot = f"captures/etape-{number:02d}.png"
        try:
            page.screenshot(path=str(out_dir / screenshot))
        except PlaywrightError:
            screenshot = None
        return Step(number, action, result, screenshot, raw)

    def run(self, page: Page, case: TestCase, out_dir: Path) -> RunResult:
        out_dir = Path(out_dir)
        (out_dir / "captures").mkdir(parents=True, exist_ok=True)
        started_at, start = datetime.now(), time.monotonic()

        # Première étape : ouvrir l'URL du cas de test, sans solliciter le modèle.
        opening = Action("goto", {"url": case.url}, "Ouverture de l'URL indiquée dans le cas de test")
        steps: list[Step] = []
        self._add(steps, self._do(page, opening, None, out_dir, number=1))

        status, summary, errors = None, "", 0
        while status is None:
            if self._stop_requested():
                status, summary = "BLOQUE", STOPPED_SUMMARY
                break
            if len(steps) >= self.max_steps:
                status, summary = "BLOQUE", f"Nombre maximal d'actions atteint ({self.max_steps})."
                break
            self._emit("phase", phase="observe", number=len(steps) + 1)
            observation = observe(page)
            user_message = build_user_message(case, steps, observation)
            self._emit("phase", phase="think", number=len(steps) + 1)
            try:
                raw = self.provider.next_action(SYSTEM_PROMPT, user_message, observation)
            except ProviderError as exc:
                status, summary = "BLOQUE", f"Le modèle n'a pas pu répondre : {exc}"
                break

            try:
                action = parse_action(raw)
            except ValueError as exc:
                self._add(steps, Step(len(steps) + 1, None, ActionResult(False, f"Réponse illisible : {exc}"), raw=raw))
                errors += 1
            else:
                if action.name == "finish":
                    status, summary = action.args["status"], action.args.get("summary", "")
                    break
                step = self._add(steps, self._do(page, action, observation, out_dir, number=len(steps) + 1, raw=raw))
                errors = 0 if step.result.ok else errors + 1

            if errors >= self.max_errors:
                status, summary = "BLOQUE", f"{self.max_errors} erreurs de suite : l'agent s'arrête."
            elif self.pause_s:
                self._pause()

        status, summary = final_status(status, summary, steps)
        return RunResult(
            case=case,
            provider=self.provider.name,
            model=self.provider.model,
            status=status,
            summary=summary,
            steps=steps,
            started_at=started_at,
            duration_s=time.monotonic() - start,
            out_dir=out_dir,
        )
