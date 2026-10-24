"""Exécution complète d'un cas de test : navigateur, agent, puis fichiers produits.

Utilisée par la ligne de commande (python -m agent), par la campagne (python -m agent.campagne)
et par l'interface Alfred (python -m alfred), pour que tous produisent exactement les mêmes résultats.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from . import coffre
from .anomalie import diagnose, needs_analysis, write_anomaly
from .browser import AdBlocker
from .codegen import write_script
from .codegen_gherkin import write_gherkin
from .codegen_robot import write_robot
from .memoire import Memory
from .navigateur import DESKTOP_VIEWPORT, choisir, lancer, libelle, options_contexte
from .postman import ApiRecorder, write_postman
from .providers import Provider
from .report import write_report
from .resultat import write_result
from .runner import Agent, RunResult
from .testcase import TestCase

VIEWPORT = DESKTOP_VIEWPORT
SESSIONS = Path("sessions")  # sessions enregistrées (cookies, stockage local) : exclues de Git


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


def store_secrets(result: RunResult, env_path: str | Path = ".env") -> dict[str, str]:
    """Range dans le fichier .env les mots de passe tapés pendant le test : les tests générés les y liront."""
    if result.coffre is None or not result.coffre.secrets:
        return {}
    used = [placeholder for step in result.steps if step.action is not None and step.result.ok
            for value in (step.action.args.get("text"), step.action.args.get("value")) if isinstance(value, str)
            for placeholder in result.coffre.placeholders_in(value)]
    return coffre.store(result.case.url, result.coffre, list(dict.fromkeys(used)), env_path)


def session_file(name: str, sessions_dir: str | Path = SESSIONS) -> Path:
    """Fichier d'une session enregistrée : sessions/<nom>.json (format storage_state de Playwright)."""
    return Path(sessions_dir) / f"{name}.json"


def missing_session(case: TestCase, sessions_dir: str | Path = SESSIONS) -> str | None:
    """Pourquoi le cas ne peut pas commencer : la session qu'il reprend n'a pas encore été enregistrée."""
    if case.session is None or session_file(case.session, sessions_dir).is_file():
        return None
    return (f"Session « {case.session} » introuvable dans le dossier {Path(sessions_dir).as_posix()} : lance d'abord le cas "
            f"qui contient la ligne « Enregistrer la session : {case.session} ».")


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
    env_path: str | Path = ".env",
    navigateur: str = "chromium",
    appareil: str | None = None,
    sessions_dir: str | Path = SESSIONS,
    block_ads: bool = True,
) -> RunResult:
    """Lance le navigateur, exécute le cas de test, puis écrit le rapport et les tests générés.

    Les mots de passe que les tests générés devront taper sont rangés dans le fichier .env (env_path).
    navigateur : chromium, firefox ou webkit ; appareil : un téléphone ou une tablette émulés (voir navigateur.py).
    La session reprise (ligne « Session : ») est chargée avant d'ouvrir la page ; celle à enregistrer
    (« Enregistrer la session : ») est écrite dans sessions_dir si le test réussit.
    """
    navigateur, appareil = choisir(navigateur, appareil)
    blocked = missing_session(case, sessions_dir)
    with sync_playwright() as playwright:
        browser = lancer(playwright, navigateur, headed)
        try:
            options = options_contexte(playwright, appareil)
            if case.session and not blocked:  # déjà connecté : cookies et stockage local de la session
                options["storage_state"] = str(session_file(case.session, sessions_dir))
            context = browser.new_context(**options)
            blocker = AdBlocker(context) if block_ads else None  # pas de publicité plein écran sur la page testée
            recorder = ApiRecorder(context)  # appels d'API de la page : pour la collection Postman

            def relay(kind: str, data: dict) -> None:  # chaque appel d'API est rattaché à son étape
                if kind == "phase":
                    recorder.step = data["number"]
                if on_event is not None:
                    on_event(kind, data)

            page = context.new_page()
            agent = Agent(provider, max_steps=max_steps, pause_s=pause_s, on_event=relay, stop_event=stop_event,
                          memory=memory, vision=vision)
            agent.blocked = blocked
            result = agent.run(page, case, Path(out_dir))
            result.browser = libelle(navigateur, browser.version, appareil)
            result.navigateur, result.appareil = navigateur, appareil
            result.api_calls = list(recorder.calls)
            result.ads_blocked = blocker.count if blocker is not None else 0
            if case.save_session and result.status == "PASS":  # pour les cas qui commencent connectés
                path = session_file(case.save_session, sessions_dir)
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    context.storage_state(path=str(path))
                    result.session_saved = path
                except (OSError, PlaywrightError):
                    result.session_saved = None
        finally:
            browser.close()
    result.secrets = store_secrets(result, env_path)
    write_script(result)
    write_robot(result)
    write_gherkin(result)
    write_postman(result)  # seulement si la page a appelé une API
    if needs_analysis(result):  # échec ou blocage : fiche d'anomalie, avec le diagnostic du modèle si possible
        if on_event is not None and provider.name != "scripted":
            on_event("analysis", {})
        diagnosis, missing = diagnose(result, provider)
        write_anomaly(result, diagnosis, missing)
    write_report(result)
    write_result(result)  # resultat.json : le même bilan, lisible par un programme (Alfred, intégration continue)
    return result
