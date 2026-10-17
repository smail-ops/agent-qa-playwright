"""La boucle de l'agent : observer la page, demander une action au modèle, l'exécuter.

    navigateur (Playwright)  --- état de la page --->  modèle (Gemini ou Claude)
    navigateur (Playwright)  <--- action en JSON ----  modèle (Gemini ou Claude)

On recommence jusqu'à ce que le modèle termine le test (finish), ou qu'une
limite soit atteinte (nombre d'actions, erreurs à la suite).

Si le parcours de ce cas de test est déjà en mémoire (voir memoire.py), il est
d'abord rejoué sans le modèle. Le modèle ne prend la main que si le site a
changé (auto-réparation).

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
from .memoire import Memory, replay_action
from .prompts import SYSTEM_PROMPT, VISION_PROMPT, build_user_message
from .providers import Provider, ProviderError, RateLimitError
from .testcase import TestCase


@dataclass
class Step:
    """Une étape du test, telle qu'elle apparaîtra dans le rapport."""

    number: int
    action: Action | None  # None si la réponse du modèle était illisible
    result: ActionResult
    screenshot: str | None = None  # chemin relatif au dossier du rapport
    raw: str = ""  # réponse brute du modèle, utile pour comprendre une erreur
    source: str = ""  # « memoire » si l'étape a été rejouée depuis la mémoire, sans le modèle


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
    feature_path: Path | None = None  # scénario Gherkin (.feature) généré
    bdd_path: Path | None = None  # le même scénario Gherkin, exécutable avec Robot Framework
    model_calls: int = 0  # réponses demandées au modèle (0 si tout a été rejoué depuis la mémoire)
    memory_replayed: int = 0  # étapes rejouées depuis la mémoire
    healed_at: int | None = None  # étape où le parcours mémorisé ne fonctionnait plus et où le modèle a pris le relais
    memory_saved: bool = False  # parcours réussi enregistré (ou mis à jour) dans la mémoire
    final_page: Observation | None = None  # état de la page à la fin d'un test non réussi, pour l'analyse de l'échec
    vision: bool = False  # le modèle recevait aussi la capture d'écran de la page
    browser: str = ""  # navigateur utilisé, repris dans la fiche d'anomalie
    anomaly_path: Path | None = None  # fiche d'anomalie (test en échec ou bloqué)
    diagnosis: dict | None = None  # analyse de l'échec par le modèle : nature, gravité, explication

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
    visual = [s.result.by_ai for s in steps if s.result.check is not None]
    if declared == "PASS" and visual and all(visual):  # aucune mesure : seulement l'avis du modèle sur des captures
        return "PASS", f"PASS fondé uniquement sur l'avis visuel de l'IA, à confirmer. {summary}".strip()
    return declared, summary


STOPPED_SUMMARY = "Mission arrêtée à la demande de l'utilisateur."
MEMORY_SUMMARY = "Parcours rejoué depuis la mémoire, sans appel au modèle."
REPLAY_PAUSE_S = 0.4  # pendant le rejeu, juste assez pour suivre les étapes à l'écran
DEFAULT_WAIT_S = 20.0  # pause quand l'API signale une limite sans dire combien de temps attendre
MAX_WAIT_S = 90.0  # au-delà, attendre ne sert à rien : l'agent s'arrête avec un message clair


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
        max_waits: int = 3,
        memory: Memory | None = None,
        vision: bool = False,
    ):
        self.provider = provider
        self.max_steps = max_steps
        self.pause_s = pause_s
        self.max_errors = max_errors
        self.max_waits = max_waits  # pauses successives acceptées quand le quota par minute est atteint
        self.on_event = on_event  # appelée à chaque phase (observe, think, act) et à chaque étape
        self.stop_event = stop_event  # arrêt demandé depuis l'interface
        self.memory = memory  # parcours réussis, rejoués sans le modèle
        self.vision = vision  # le modèle reçoit aussi la capture d'écran de la page
        self.system_prompt = SYSTEM_PROMPT + (VISION_PROMPT if vision else "")
        self.model_calls = 0

    def _emit(self, kind: str, **data) -> None:
        if self.on_event is not None:
            self.on_event(kind, data)

    def _add(self, steps: list[Step], step: Step) -> Step:
        steps.append(step)
        self._emit("step", step=step)
        return step

    def _stop_requested(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def _sleep(self, seconds: float) -> None:
        if self.stop_event is not None:
            self.stop_event.wait(seconds)  # un arrêt demandé interrompt l'attente
        else:
            time.sleep(seconds)

    def _pause(self) -> None:
        self._sleep(self.pause_s)

    def _ask_model(self, user_message: str, observation: Observation) -> str:
        """Interroge le modèle. Quota par minute atteint : patiente le temps indiqué par l'API, puis réessaie.

        Le quota du jour, lui, ne revient pas en quelques secondes : l'erreur remonte tout de suite.
        """
        waits = 0
        while True:
            try:
                raw = self.provider.next_action(self.system_prompt, user_message, observation)
                self.model_calls += 1
                return raw
            except RateLimitError as exc:
                delay = exc.retry_after if exc.retry_after is not None else DEFAULT_WAIT_S
                if exc.daily or exc.no_free_quota or waits >= self.max_waits or delay > MAX_WAIT_S:
                    raise
                waits += 1
                self._emit("wait", seconds=round(delay, 1), attempt=waits, max_attempts=self.max_waits)
                self._sleep(delay)
                if self._stop_requested():
                    raise

    def _do(self, page: Page, action: Action, observation: Observation | None, out_dir: Path, number: int, raw: str = "",
            source: str = "") -> Step:
        self._emit("phase", phase="act", number=number, action=action, observation=observation)
        result = execute(page, action, observation)
        screenshot = f"captures/etape-{number:02d}.png"
        try:
            page.screenshot(path=str(out_dir / screenshot))
        except PlaywrightError:
            screenshot = None
        return Step(number, action, result, screenshot, raw, source)

    def _replay(self, page: Page, plan: dict, steps: list[Step], out_dir: Path) -> tuple[str | None, str, int | None]:
        """Rejoue le parcours mémorisé. Renvoie (statut, conclusion, étape de rupture).

        Statut None : le parcours ne fonctionne plus à partir de l'étape de rupture, le modèle prend le relais.
        """
        self._emit("memory", steps=len(plan["steps"]), saved_at=plan.get("saved_at"))
        for entry in plan["steps"]:
            if self._stop_requested():
                return "BLOQUE", STOPPED_SUMMARY, None
            number = len(steps) + 1
            self._emit("phase", phase="observe", number=number)
            observation = observe(page)
            action, problem = replay_action(entry, observation)
            if action is None:
                self._emit("heal", number=number, reason=problem)
                return None, "", number
            step = self._add(steps, self._do(page, action, observation, out_dir, number, source="memoire"))
            if not step.result.ok:  # l'action n'a pas abouti : le site a changé
                self._emit("heal", number=number, reason=step.result.detail)
                return None, "", number
            if self.pause_s:
                self._sleep(min(self.pause_s, REPLAY_PAUSE_S))
        return "PASS", MEMORY_SUMMARY, None

    def run(self, page: Page, case: TestCase, out_dir: Path) -> RunResult:
        self.model_calls = 0
        out_dir = Path(out_dir)
        (out_dir / "captures").mkdir(parents=True, exist_ok=True)
        started_at, start = datetime.now(), time.monotonic()

        # Première étape : ouvrir l'URL du cas de test, sans solliciter le modèle.
        opening = Action("goto", {"url": case.url}, "Ouverture de l'URL indiquée dans le cas de test")
        steps: list[Step] = []
        self._add(steps, self._do(page, opening, None, out_dir, number=1))

        status, summary, errors, healed_at = None, "", 0, None
        plan = self.memory.load(case) if self.memory is not None else None
        if plan is not None:
            status, summary, healed_at = self._replay(page, plan, steps, out_dir)
        replayed = sum(1 for s in steps if s.source == "memoire")

        while status is None:
            if self._stop_requested():
                status, summary = "BLOQUE", STOPPED_SUMMARY
                break
            if len(steps) - replayed >= self.max_steps:  # les étapes rejouées ne coûtent aucun appel au modèle
                status, summary = "BLOQUE", f"Nombre maximal d'actions atteint ({self.max_steps})."
                break
            self._emit("phase", phase="observe", number=len(steps) + 1)
            observation = observe(page)
            if self.vision:  # le modèle voit la page comme un testeur : capture JPEG, légère
                try:
                    observation.screenshot = page.screenshot(type="jpeg", quality=55)
                except PlaywrightError:
                    observation.screenshot = None
            user_message = build_user_message(case, steps, observation)
            self._emit("phase", phase="think", number=len(steps) + 1)
            try:
                raw = self._ask_model(user_message, observation)
            except ProviderError as exc:
                if self._stop_requested():
                    status, summary = "BLOQUE", STOPPED_SUMMARY
                else:
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
        final_page = None
        if status != "PASS":  # la page telle qu'elle est restée, pour comprendre l'échec
            try:
                final_page = observe(page)
            except PlaywrightError:
                final_page = None
        saved = False
        visual = any(s.action is not None and s.action.name == "check_visual" for s in steps)
        if self.memory is not None and status == "PASS" and not visual:  # parcours réussis et vérifiables sans IA
            try:
                self.memory.save(case, steps, f"{self.provider.name} ({self.provider.model})", summary)
                saved = True
            except OSError:  # disque plein, dossier protégé : le test a réussi, seule la mémoire manque
                saved = False
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
            model_calls=self.model_calls,
            memory_replayed=replayed,
            healed_at=healed_at,
            memory_saved=saved,
            final_page=final_page,
            vision=self.vision,
        )
