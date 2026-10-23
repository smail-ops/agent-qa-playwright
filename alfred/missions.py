"""Missions d'Alfred : une mission est une exécution d'un cas de test.

La mission tourne dans un fil d'exécution à part (le navigateur Playwright y
reste du début à la fin). Chaque événement de l'agent est converti en un petit
dictionnaire JSON et ajouté à la liste des événements de la mission ; la page
web les reçoit en direct par un flux Server-Sent Events, et une page rechargée
peut tout relire depuis le début.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from agent.anomalie import GRAVITES, NATURES
from agent.execution import memory_for, new_out_dir, run_case
from agent.memoire import Memory
from agent.providers import Provider
from agent.report import memory_note
from agent.runner import RunResult, Step
from agent.testcase import TestCase

from . import narration

PROVIDER_LABELS = {"gemini": "Gemini", "claude": "Claude", "scripted": "la démo sans IA"}


class MissionBusy(Exception):
    """Une mission est déjà en cours."""


class Mission:
    def __init__(self, case: TestCase, provider: Provider, out_dir: Path, headed: bool, pause_s: float, max_steps: int,
                 memory: Memory | None = None, vision: bool = False):
        self.id = out_dir.name
        self.case = case
        self.provider = provider
        self.out_dir = out_dir
        self.headed = headed
        self.pause_s = pause_s
        self.max_steps = max_steps
        self.memory = memory  # parcours mémorisés, ou None pour tout faire avec le modèle
        self.vision = vision  # le modèle reçoit aussi la capture d'écran
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

    def start(self, case: TestCase, provider: Provider, headed: bool = False, pause_s: float = 0.0,
              use_memory: bool = True, vision: bool = False) -> Mission:
        with self._lock:
            if self.current is not None and not self.current.finished:
                raise MissionBusy("Une mission est déjà en cours : attendez sa fin ou arrêtez-la.")
            memory = memory_for(provider, self.memory_dir) if use_memory else None
            mission = Mission(case, provider, new_out_dir(self.reports_dir, case), headed, pause_s, self.max_steps, memory,
                              vision)
            mission.out_dir.mkdir(parents=True)  # réserve le nom du dossier
            self.missions[mission.id] = mission
            self.current = mission
        threading.Thread(target=self._run, args=(mission,), name=f"mission-{mission.id}", daemon=True).start()
        return mission

    def _run(self, mission: Mission) -> None:
        provider_label = PROVIDER_LABELS.get(mission.provider.name, mission.provider.name)
        mission.emit(
            "start",
            title=mission.case.title,
            url=mission.case.url,
            provider=mission.provider.name,
            model=mission.provider.model,
            max_steps=mission.max_steps,
            vision=mission.vision,
            say=narration.start_speech(mission.case.title, provider_label),
        )
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
            )
        except Exception as exc:  # navigateur absent, erreur inattendue... : la page doit le savoir
            message = f"{type(exc).__name__} : {exc}".strip()
            mission.emit("fault", message=message, say="La mission n'a pas pu se dérouler. Le détail est affiché à l'écran.")
        else:
            mission.emit("done", **self._done(mission, result))
        finally:
            mission.finish()

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
