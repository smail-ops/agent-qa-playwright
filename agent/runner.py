"""La boucle de l'agent : observer la page, demander une action au modèle, l'exécuter.

    navigateur (Playwright)  --- état de la page --->  modèle (Gemini ou Claude)
    navigateur (Playwright)  <--- action en JSON ----  modèle (Gemini ou Claude)

On recommence jusqu'à ce que le modèle termine le test (finish), ou qu'une
limite soit atteinte (nombre d'actions, erreurs à la suite).

Si le parcours de ce cas de test est déjà en mémoire (voir memoire.py), il est
d'abord rejoué sans le modèle. Le modèle ne prend la main que si le site a
changé (auto-réparation).

Un bandeau de cookies qui couvre la page est accepté par le programme, sans le
modèle, avant qu'il ne regarde la page (voir cookies.py).

« {unique} » dans le cas de test devient une valeur propre à l'exécution, que
le modèle lit comme une donnée de test ordinaire (voir donnees.py).

Une interface (Alfred) peut suivre l'exécution en direct grâce à on_event,
et demander l'arrêt grâce à stop_event.
"""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from . import cookies
from .actions import Action, parse_action
from .coffre import Coffre
from .browser import DATA_DIR, ActionResult, Observation, execute, observe, open_closed_shadows, vision_screenshot
from .donnees import contient, remplacer, remplacer_action, valeur_unique
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
    source: str = ""  # « memoire » : rejouée depuis la mémoire ; « programme » : faite sans le modèle (bandeau de cookies)


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
    closed_shadow: bool = False  # la page avait des composants web fermés, ouverts pour le test
    coffre: Coffre | None = None  # mots de passe du cas : les étapes ne gardent que leur repère
    secrets: dict | None = None  # repère -> variable du fichier .env, pour les tests générés
    unique: str | None = None  # valeur de {unique} pour cette exécution (voir donnees.py)
    interruption: str | None = None  # quota_jour, quota, modele, arret ou session : l'exécution n'a pas pu conclure
    navigateur: str = "chromium"  # chromium, firefox ou webkit
    appareil: str | None = None  # téléphone ou tablette émulé, par exemple « iPhone 15 »
    session_saved: Path | None = None  # fichier de la session enregistrée (ligne « Enregistrer la session »)
    ads_blocked: int = 0  # requêtes de régies publicitaires bloquées (une publicité plein écran couvrirait la page)
    api_calls: list | None = None  # appels d'API faits par la page (fetch, XMLHttpRequest), voir postman.py
    postman_path: Path | None = None  # collection Postman de ces appels
    postman_env_path: Path | None = None  # son environnement ({{baseUrl}}, variables secrètes vides)

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


def interruption_of(exc: ProviderError) -> str:
    """Pourquoi le modèle n'a pas répondu : quota_jour (il revient demain), quota (dans quelques minutes) ou modele.

    no_free_quota (aucun quota gratuit pour ce modèle) compte comme quota_jour : attendre ne suffira pas.
    """
    if isinstance(exc, RateLimitError):
        return "quota_jour" if exc.daily or exc.no_free_quota else "quota"
    return "modele"


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
        accept_cookies: bool = True,
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
        self.accept_cookies = accept_cookies  # bandeau de cookies accepté par le programme (sauf cas qui en parle)
        self._cookies_on = accept_cookies
        self.system_prompt = SYSTEM_PROMPT + (VISION_PROMPT if vision else "")
        self.model_calls = 0
        self.page: Page | None = None  # onglet où agit l'agent : il change quand un clic ouvre un nouvel onglet
        self._closed_shadows = 0
        self.coffre = Coffre()  # mots de passe du cas de test en cours
        self.unique: str | None = None  # valeur de {unique} pour l'exécution en cours
        self.blocked: str | None = None  # raison connue avant de commencer (session absente) : BLOQUÉ dès l'ouverture
        self.interruption: str | None = None

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

    def _observe(self, page: Page) -> Observation:
        observation = observe(page)
        self._closed_shadows = max(self._closed_shadows, observation.closed_shadows)
        return observation

    def _do(self, page: Page, action: Action, observation: Observation | None, out_dir: Path, number: int, raw: str = "",
            source: str = "") -> Step:
        # {unique} recopié tel quel (par le modèle, ou par la mémoire) : la valeur de cette exécution
        action = remplacer_action(action, self.unique)
        # L'étape garde le repère « [mot de passe] » ; seule l'action exécutée porte le vrai mot de passe
        action = self.coffre.hide_action(action)
        self._emit("phase", phase="act", number=number, action=action, observation=observation)
        result = execute(page, self.coffre.reveal_action(action), observation)
        if result.page is not None:  # nouvel onglet, ou retour à un autre onglet : l'agent continue là
            self.page = page = result.page
        if action.name == "upload" and result.ok:  # le fichier accompagne les tests générés, qui le joignent aussi
            try:
                shutil.copyfile(DATA_DIR / action.args["file"], out_dir / action.args["file"])
            except OSError:
                pass
        screenshot = f"captures/etape-{number:02d}.png"
        try:
            page.screenshot(path=str(out_dir / screenshot))
        except PlaywrightError:
            screenshot = None
        return Step(number, action, result, screenshot, raw, source)

    def _cookies(self, page: Page, steps: list[Step], out_dir: Path, wait_s: float = 0.0) -> None:
        """Un bandeau de cookies couvre la page : le programme l'accepte, sans le modèle (voir cookies.py)."""
        if not self._cookies_on or sum(1 for s in steps if s.action and s.action.name == "cookies") >= cookies.MAX_PER_RUN:
            return
        banner = cookies.find(page, wait_s)
        if banner is None:
            return
        number = len(steps) + 1
        action = Action("cookies", banner.args(), "Bandeau de cookies accepté par le programme, comme le ferait un testeur")
        self._emit("phase", phase="act", number=number, action=action, observation=None)
        result = cookies.accept(page, banner)
        screenshot = f"captures/etape-{number:02d}.png"
        try:
            page.screenshot(path=str(out_dir / screenshot))
        except PlaywrightError:
            screenshot = None
        self._add(steps, Step(number, action, result, screenshot, source="programme"))

    def _replay(self, page: Page, plan: dict, steps: list[Step], out_dir: Path) -> tuple[str | None, str, int | None]:
        """Rejoue le parcours mémorisé. Renvoie (statut, conclusion, étape de rupture).

        Statut None : le parcours ne fonctionne plus à partir de l'étape de rupture, le modèle prend le relais.
        """
        self._emit("memory", steps=len(plan["steps"]), saved_at=plan.get("saved_at"))
        for entry in plan["steps"]:
            if self._stop_requested():
                return "BLOQUE", STOPPED_SUMMARY, None
            self._cookies(self.page, steps, out_dir)
            number = len(steps) + 1
            self._emit("phase", phase="observe", number=number)
            observation = self._observe(self.page)
            action, problem = replay_action(entry, observation)
            if action is None:
                self._emit("heal", number=number, reason=problem)
                return None, "", number
            step = self._add(steps, self._do(self.page, action, observation, out_dir, number, source="memoire"))
            if not step.result.ok:  # l'action n'a pas abouti : le site a changé
                self._emit("heal", number=number, reason=step.result.detail)
                return None, "", number
            if self.pause_s:
                self._sleep(min(self.pause_s, REPLAY_PAUSE_S))
        return "PASS", MEMORY_SUMMARY, None

    def run(self, page: Page, case: TestCase, out_dir: Path) -> RunResult:
        self.model_calls, self.page, self._closed_shadows, self.interruption = 0, page, 0, None
        # {unique} : une valeur propre à cette exécution. Le modèle lit le cas avec cette valeur (model_case) ;
        # la mémoire et les tests générés, eux, gardent {unique} (case reste le cas tel qu'il est écrit).
        self.unique = valeur_unique() if contient(case.text) or contient(case.url) else None
        model_case = replace(case, url=remplacer(case.url, self.unique), text=remplacer(case.text, self.unique))
        self.coffre = Coffre.from_case(model_case.text)
        open_closed_shadows(page.context)  # avant toute page : les composants fermés seront créés ouverts
        out_dir = Path(out_dir)
        (out_dir / "captures").mkdir(parents=True, exist_ok=True)
        started_at, start = datetime.now(), time.monotonic()

        # Première étape : ouvrir l'URL du cas de test, sans solliciter le modèle.
        opening = Action("goto", {"url": model_case.url}, "Ouverture de l'URL indiquée dans le cas de test")
        steps: list[Step] = []
        self._add(steps, self._do(page, opening, None, out_dir, number=1))
        # Le bandeau de cookies s'affiche souvent juste après la page : on l'attend s'il y a un outil de consentement
        self._cookies_on = self.accept_cookies and cookies.wanted(case.text)
        self._cookies(self.page, steps, out_dir, cookies.WAIT_S)

        status, summary, errors, healed_at = None, "", 0, None
        if self.blocked:  # la session que le cas reprend n'a pas encore été enregistrée
            status, summary, self.interruption = "BLOQUE", self.blocked, "session"
        plan = self.memory.load(case) if self.memory is not None and status is None else None
        if plan is not None:
            status, summary, healed_at = self._replay(self.page, plan, steps, out_dir)
        replayed = sum(1 for s in steps if s.source == "memoire")

        while status is None:
            free = replayed + sum(1 for s in steps if s.source == "programme")  # étapes faites sans le modèle
            if self._stop_requested():
                status, summary, self.interruption = "BLOQUE", STOPPED_SUMMARY, "arret"
                break
            if len(steps) - free >= self.max_steps:  # les étapes faites sans le modèle ne comptent pas
                status, summary = "BLOQUE", f"Nombre maximal d'actions atteint ({self.max_steps})."
                break
            self._cookies(self.page, steps, out_dir)
            self._emit("phase", phase="observe", number=len(steps) + 1)
            observation = self._observe(self.page)
            if self.vision:  # le modèle voit la page comme un testeur : capture JPEG légère, données personnelles cachées
                try:
                    observation.screenshot = vision_screenshot(self.page, observation, model_case.text,
                                                               list(self.coffre.secrets.values()))
                except PlaywrightError:
                    observation.screenshot = None
            user_message = build_user_message(model_case, steps, observation)
            self._emit("phase", phase="think", number=len(steps) + 1)
            try:
                raw = self._ask_model(user_message, observation)
            except ProviderError as exc:
                if self._stop_requested():
                    status, summary, self.interruption = "BLOQUE", STOPPED_SUMMARY, "arret"
                else:
                    status, summary = "BLOQUE", f"Le modèle n'a pas pu répondre : {exc}"
                    self.interruption = interruption_of(exc)
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
                step = self._add(steps, self._do(self.page, action, observation, out_dir, number=len(steps) + 1, raw=raw))
                errors = 0 if step.result.ok else errors + 1

            if errors >= self.max_errors:
                status, summary = "BLOQUE", f"{self.max_errors} erreurs de suite : l'agent s'arrête."
            elif self.pause_s:
                self._pause()

        status, summary = final_status(status, summary, steps)
        if status == "BLOQUE" and summary == STOPPED_SUMMARY:  # arrêt demandé, pendant le rejeu de la mémoire compris
            self.interruption = "arret"
        final_page = None
        if status != "PASS":  # la page telle qu'elle est restée, pour comprendre l'échec
            try:
                final_page = self._observe(self.page)
            except PlaywrightError:
                final_page = None
        saved = False
        visual = any(s.action is not None and s.action.name == "check_visual" for s in steps)
        if self.memory is not None and status == "PASS" and not visual:  # parcours réussis et vérifiables sans IA
            try:
                self.memory.save(case, steps, f"{self.provider.name} ({self.provider.model})", summary, unique=self.unique)
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
            closed_shadow=self._closed_shadows > 0,
            coffre=self.coffre,
            unique=self.unique,
            interruption=self.interruption,
        )
