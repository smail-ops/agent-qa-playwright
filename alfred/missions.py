"""Missions d'Alfred : une mission est une exécution d'un cas de test.

La mission tourne dans un fil d'exécution à part (le navigateur Playwright y
reste du début à la fin). Chaque événement de l'agent est converti en un petit
dictionnaire JSON et ajouté à la liste des événements de la mission ; la page
web les reçoit en direct par un flux Server-Sent Events, et une page rechargée
peut tout relire depuis le début.

Une série enchaîne plusieurs missions, l'une après l'autre : c'est la relance
des tests en échec (« relance les tests en échec »). Le verdict de chaque
mission donne l'identifiant de la suivante, que la page suit à son tour.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent.anomalie import GRAVITES, NATURES
from agent.execution import memory_for, new_out_dir, run_case
from agent.memoire import Memory
from agent.navigateur import libelle
from agent.providers import Provider
from agent.report import memory_note
from agent.runner import STOPPED_SUMMARY, RunResult, Step
from agent.testcase import TestCase

from . import narration

PROVIDER_LABELS = {"gemini": "Gemini", "claude": "Claude", "scripted": "la démo sans IA"}
MAX_SERIES = 20  # missions d'une série au plus


class MissionBusy(Exception):
    """Une mission est déjà en cours."""


def failed_cases(cases: list[dict], results: list[dict]) -> list[dict]:
    """Les cas dont la dernière exécution n'a pas réussi : un échec, ou un blocage qui n'est pas un arrêt demandé.

    cases : la liste des cas d'Alfred (titre compris) ; results : les resultat.json, du plus récent au plus ancien.
    """
    latest: dict[str, dict] = {}
    for result in results:
        latest.setdefault(str(result.get("titre") or ""), result)
    failed = []
    for case in cases:
        result = latest.get(case["title"])
        if result is None or result.get("statut") not in ("FAIL", "BLOQUE"):
            continue
        if result.get("interruption") == "arret" or result.get("conclusion") == STOPPED_SUMMARY:
            continue  # arrêtée à la demande du testeur : ce n'est pas un test en échec
        failed.append(case)
    return failed


@dataclass
class Serie:
    """Plusieurs missions l'une après l'autre : la relance des tests en échec."""

    total: int
    stop: threading.Event = field(default_factory=threading.Event)
    results: list[tuple[str, str]] = field(default_factory=list)  # (cas, statut) de chaque mission terminée


@dataclass
class Options:
    """Réglages communs aux missions : fenêtre, pause, mémoire, vision, navigateur et appareil."""

    headed: bool = False
    pause_s: float = 0.0
    use_memory: bool = True
    vision: bool = False
    navigateur: str = "chromium"
    appareil: str | None = None


class Mission:
    def __init__(self, case: TestCase, provider: Provider, out_dir: Path, headed: bool, pause_s: float, max_steps: int,
                 memory: Memory | None = None, vision: bool = False, navigateur: str = "chromium",
                 appareil: str | None = None, serie: Serie | None = None, position: int = 0):
        self.id = out_dir.name
        self.case = case
        self.provider = provider
        self.out_dir = out_dir
        self.headed = headed
        self.pause_s = pause_s
        self.max_steps = max_steps
        self.memory = memory  # parcours mémorisés, ou None pour tout faire avec le modèle
        self.vision = vision  # le modèle reçoit aussi la capture d'écran
        self.navigateur = navigateur  # chromium, firefox ou webkit
        self.appareil = appareil  # téléphone ou tablette émulé
        self.serie = serie  # relance des tests en échec : la série dont la mission fait partie
        self.position = position  # rang de la mission dans sa série, à partir de 1
        self.stop_event = threading.Event()
        self.events: list[dict] = []
        self.finished = False
        self._cond = threading.Condition()

    def url(self, relative: str | Path) -> str:
        """Adresse d'un fichier de la mission (capture, rapport, test généré)."""
        return f"/missions/{self.id}/{Path(relative).as_posix()}"

    def emit(self, kind: str, **data) -> None:
        with self._cond:
            self.events.append({"id": len(self.events), "type": kind, "t": round(time.time(), 3), **data})
            self._cond.notify_all()

    def finish(self) -> None:
        with self._cond:
            self.finished = True
            self._cond.notify_all()

    def events_after(self, index: int, timeout: float) -> tuple[list[dict], bool]:
        """Événements à partir de index ; attend au plus timeout secondes s'il n'y en a pas."""
        with self._cond:
            if index >= len(self.events) and not self.finished:
                self._cond.wait(timeout)
            return self.events[index:], self.finished

    def summary(self) -> dict:
        return {"id": self.id, "title": self.case.title, "running": not self.finished}


class MissionManager:
    """Lance les missions, une à la fois, et garde celles de la session."""

    def __init__(self, reports_dir: Path, max_steps: int = 20, memory_dir: Path | None = None, env_path: Path | str = ".env"):
        self.reports_dir = Path(reports_dir)
        self.env_path = Path(env_path)  # les mots de passe des tests générés y sont rangés
        self.max_steps = max_steps
        self.memory_dir = Path(memory_dir) if memory_dir is not None else self.reports_dir.parent / "memoire"
        self.missions: dict[str, Mission] = {}
        self.current: Mission | None = None
        self._lock = threading.Lock()

    @property
    def sessions_dir(self) -> Path:
        """Les sessions enregistrées (cas qui commencent déjà connectés), à côté des rapports."""
        return self.reports_dir.parent / "sessions"

    def _busy(self) -> None:
        if self.current is not None and not self.current.finished:
            raise MissionBusy("Une mission est déjà en cours : attendez sa fin ou arrêtez-la.")

    def _create(self, case: TestCase, provider: Provider, options: Options, serie: Serie | None = None,
                position: int = 0) -> Mission:
        """Une mission prête à partir, son dossier réservé ; elle devient la mission en cours (verrou pris)."""
        memory = memory_for(provider, self.memory_dir) if options.use_memory else None
        mission = Mission(case, provider, new_out_dir(self.reports_dir, case), options.headed, options.pause_s,
                          self.max_steps, memory, options.vision, options.navigateur, options.appareil, serie, position)
        mission.out_dir.mkdir(parents=True)  # réserve le nom du dossier
        self.missions[mission.id] = mission
        self.current = mission
        return mission

    def start(self, case: TestCase, provider: Provider, headed: bool = False, pause_s: float = 0.0,
              use_memory: bool = True, vision: bool = False, navigateur: str = "chromium",
              appareil: str | None = None) -> Mission:
        options = Options(headed, pause_s, use_memory, vision, navigateur, appareil)
        with self._lock:
            self._busy()
            mission = self._create(case, provider, options)
        threading.Thread(target=self._run, args=(mission,), name=f"mission-{mission.id}", daemon=True).start()
        return mission

    def start_series(self, items: list[tuple[TestCase, Provider]], options: Options) -> Mission:
        """Lance les missions l'une après l'autre (au plus MAX_SERIES). Renvoie la première, déjà partie."""
        items = items[:MAX_SERIES]
        if not items:
            raise ValueError("Aucun cas à relancer.")
        with self._lock:
            self._busy()
            serie = Serie(len(items))
            first = self._create(*items[0], options, serie, 1)
        threading.Thread(target=self._run_series, args=(first, items, options), name=f"serie-{first.id}",
                         daemon=True).start()
        return first

    def _run_series(self, mission: Mission, items: list[tuple[TestCase, Provider]], options: Options) -> None:
        serie, index = mission.serie, 0

        def following() -> Mission | None:  # appelée juste avant le verdict : la page saura quelle mission suivre
            nonlocal index
            index += 1
            if index >= len(items) or serie.stop.is_set():
                return None
            with self._lock:
                return self._create(*items[index], options, serie, index + 1)

        while mission is not None:
            mission = self._run(mission, following)

    def _run(self, mission: Mission, following=None) -> Mission | None:
        """Exécute une mission ; dans une série, renvoie la suivante (déjà réservée), ou None."""
        provider_label = PROVIDER_LABELS.get(mission.provider.name, mission.provider.name)
        say = narration.start_speech(mission.case.title, provider_label)
        start = {}
        if mission.serie is not None:  # relance des tests en échec : « Test 2 sur 3. »
            start["serie"] = {"position": mission.position, "total": mission.serie.total}
            say = narration.series_start_speech(mission.position, mission.serie.total) + " " + say
        mission.emit(
            "start",
            title=mission.case.title,
            url=mission.case.url,
            provider=mission.provider.name,
            model=mission.provider.model,
            max_steps=mission.max_steps,
            vision=mission.vision,
            navigateur=mission.navigateur,
            appareil=mission.appareil,
            navigateur_libelle=libelle(mission.navigateur, "", mission.appareil),
            say=say,
            **start,
        )
        following_mission = None
        try:
            result = run_case(
                mission.case,
                mission.provider,
                mission.out_dir,
                headed=mission.headed,
                max_steps=mission.max_steps,
                pause_s=mission.pause_s,
                on_event=lambda kind, data: self._relay(mission, kind, data),
                stop_event=mission.stop_event,
                memory=mission.memory,
                vision=mission.vision,
                env_path=self.env_path,
                navigateur=mission.navigateur,
                appareil=mission.appareil,
                sessions_dir=self.sessions_dir,
            )
        except Exception as exc:  # navigateur absent, erreur inattendue... : la page doit le savoir
            message = f"{type(exc).__name__} : {exc}".strip()
            if mission.serie is not None:
                mission.serie.results.append((mission.case.title, "ERREUR"))
            following_mission = following() if following is not None else None
            info = self._series_info(mission, following_mission)
            say = "La mission n'a pas pu se dérouler. Le détail est affiché à l'écran."
            mission.emit("fault", message=message, say=say + (" " + info["serie"].pop("say") if info else ""), **info)
        else:
            if mission.serie is not None:
                mission.serie.results.append((mission.case.title, result.status))
            following_mission = following() if following is not None else None
            done = self._done(mission, result)
            info = self._series_info(mission, following_mission)
            if info:  # la phrase du verdict annonce la suite, ou le bilan de la série
                done["say"] = done["say"] + " " + info["serie"].pop("say")
            mission.emit("done", **done, **info)
        finally:
            mission.finish()
        return following_mission

    @staticmethod
    def _series_info(mission: Mission, following: Mission | None) -> dict:
        """Dans une série : la mission suivante, ou le bilan de la série si c'était la dernière."""
        serie = mission.serie
        if serie is None:
            return {}
        info = {"position": mission.position, "total": serie.total, "suivante": following.id if following else None}
        if following is not None:
            info["say"] = narration.series_next_speech(following.position, serie.total)
        else:
            info["bilan"] = {"lances": len(serie.results), "reussis": sum(1 for _, s in serie.results if s == "PASS")}
            info["say"] = narration.series_done_speech(serie.results, serie.total, serie.stop.is_set())
        return {"serie": info}

    def _relay(self, mission: Mission, kind: str, data: dict) -> None:
        """Traduit un événement de l'agent pour la page web."""
        if kind == "phase":
            payload = {"phase": data["phase"], "number": data["number"]}
            action = data.get("action")
            if action is not None:
                element = narration.find_element(action, data.get("observation"))
                target = narration.find_element(action, data.get("observation"), "to")
                payload["label"] = narration.action_label(action, element, target)
                payload["say"] = narration.action_speech(action, element, data["number"])
            mission.emit("phase", **payload)
        elif kind == "step":
            mission.emit("step", **self._step(mission, data["step"]))
        elif kind == "wait":  # quota par minute atteint : l'agent patiente avant de réessayer
            mission.emit("wait", seconds=data["seconds"], attempt=data["attempt"], max_attempts=data["max_attempts"],
                         say=narration.wait_speech(data["seconds"]))
        elif kind == "memory":  # parcours déjà réussi : rejoué sans le modèle
            mission.emit("memory", steps=data["steps"], saved_at=data.get("saved_at"), say=narration.memory_speech(data["steps"]))
        elif kind == "analysis":  # test en échec : le modèle rédige son diagnostic
            mission.emit("analysis", say=narration.analysis_speech())
        elif kind == "heal":  # le site a changé : le modèle reprend la main à cette étape
            label = PROVIDER_LABELS.get(mission.provider.name, mission.provider.name)
            mission.emit("heal", number=data["number"], reason=data["reason"], say=narration.heal_speech(data["number"], label))

    @staticmethod
    def _step(mission: Mission, step: Step) -> dict:
        element = step.result.element
        return {
            "number": step.number,
            "action": step.action.name if step.action else None,
            "label": narration.action_label(step.action, element, step.result.target),
            "reason": step.action.reason if step.action else "",
            "ok": step.result.ok,
            "check": step.result.check,
            "detail": step.result.detail,
            "screenshot": mission.url(step.screenshot) if step.screenshot else None,
            "memory": step.source == "memoire",
            "programme": step.source == "programme",  # bandeau de cookies accepté sans IA
            "say": narration.step_speech(step),
        }

    @staticmethod
    def _done(mission: Mission, result: RunResult) -> dict:
        files = {
            "rapport": result.report_path,
            "pytest": result.script_path,
            "robot": result.robot_path,
            "gherkin": result.feature_path,
            "robot_bdd": result.bdd_path,
            "anomalie": result.anomaly_path,
            "postman": result.postman_path,  # appels d'API de la page, s'il y en a eu
        }
        diagnosis = None
        if result.diagnosis:
            diagnosis = {"nature": NATURES[result.diagnosis["nature"]], "gravite": GRAVITES[result.diagnosis["gravite"]],
                         "titre": result.diagnosis["titre"]}
        stopped = mission.stop_event.is_set()
        return {
            "status": result.status,
            "summary": result.summary,
            "duration_s": round(result.duration_s, 1),
            "steps": len(result.steps),
            "checks_ok": sum(1 for s in result.checks if s.result.check),
            "checks_failed": sum(1 for s in result.checks if s.result.check is False),
            "model_calls": None if result.provider == "scripted" else result.model_calls,  # la démo n'appelle aucun modèle
            "memory": memory_note(result),
            "diagnosis": diagnosis,
            "folder": mission.out_dir.as_posix(),
            "files": {name: mission.url(path.name) for name, path in files.items() if path is not None},
            "stopped": stopped,
            "say": narration.done_speech(result, stopped),
        }
