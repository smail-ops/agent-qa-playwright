"""Serveur web local d'Alfred, écrit avec la seule bibliothèque standard de Python.

Routes :
    GET  /                              la page d'Alfred
    GET  /api/status                    moteurs disponibles (clés présentes ou non) et mission en cours
    GET  /api/cases                     cas de test du dossier cas_de_test/
    POST /api/cases                     enregistre un cas de test écrit dans la page
    POST /api/missions                  lance une mission
    GET  /api/missions/<id>/events      événements de la mission, en direct (Server-Sent Events)
    POST /api/missions/<id>/stop        demande l'arrêt de la mission
    GET  /missions/<id>/<fichier>       captures, rapport et tests générés

Sécurité : Alfred n'écoute que sur 127.0.0.1, refuse les requêtes dont l'en-tête
Host n'est pas local (protection contre le « DNS rebinding »), refuse les POST
venant d'un autre site et ne sert que les fichiers de ses propres missions.
"""

from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from agent.providers import (
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_GEMINI_MODEL,
    ProviderError,
    ScriptedProvider,
    make_provider,
)
from agent.testcase import TestCase, parse_test_case, slugify

from .missions import MissionBusy, MissionManager

STATIC_DIR = Path(__file__).resolve().parent / "static"
FONT_FILE = re.compile(r"[a-z0-9-]+\.woff")
MISSION_ID = r"[\w.-]+"
CASE_CODE = re.compile(r"\s*([A-Z]{2,}-\d+)\b")
MAX_BODY = 200_000  # octets
MAX_PAUSE = 30.0  # secondes
TEXT = "text/plain; charset=utf-8"
MISSION_FILE_TYPES = {".png": "image/png", ".md": TEXT, ".py": TEXT, ".robot": TEXT}
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def case_code(title: str) -> str | None:
    """« CT-01 Connexion valide » donne « CT-01 »."""
    match = CASE_CODE.match(title)
    return match.group(1) if match else None


def scenario_for(path: Path, case: TestCase) -> Path | None:
    """Scénario de la démo sans IA, s'il existe : scenario_CT-00.json à côté du cas."""
    code = case_code(case.title)
    scenario = path.parent / f"scenario_{code}.json" if code else None
    return scenario if scenario is not None and scenario.is_file() else None


def list_cases(cases_dir: Path) -> list[dict]:
    cases = []
    for path in cases_dir.rglob("*.md"):
        try:
            case = parse_test_case(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue  # ce fichier n'est pas un cas de test
        cases.append({
            "id": path.relative_to(cases_dir).as_posix(),
            "code": case_code(case.title),
            "title": case.title,
            "url": case.url,
            "host": urlsplit(case.url).netloc,
            "text": case.text,
            "demo": scenario_for(path, case) is not None,
        })
    cases.sort(key=lambda c: (c["code"] is None, c["code"] or "", c["id"]))
    return cases


def resolve_case(cases_dir: Path, case_id: str) -> Path:
    """Chemin d'un cas de test, sans jamais sortir du dossier des cas."""
    root = cases_dir.resolve()
    path = (root / case_id).resolve()
    if path.suffix != ".md" or not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Cas de test introuvable.")
    return path


def providers_status() -> dict:
    """Moteurs disponibles. Les clés ne quittent jamais le serveur : seule leur présence est indiquée."""
    gemini, claude = bool(os.getenv("GEMINI_API_KEY")), bool(os.getenv("ANTHROPIC_API_KEY"))
    return {
        "gemini": {
            "ready": gemini,
            "model": os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL,
            "hint": None if gemini else "Ajoutez GEMINI_API_KEY dans le fichier .env, puis relancez Alfred.",
        },
        "claude": {
            "ready": claude,
            "model": os.getenv("CLAUDE_MODEL") or DEFAULT_CLAUDE_MODEL,
            "hint": None if claude else "Ajoutez ANTHROPIC_API_KEY dans le fichier .env, puis relancez Alfred.",
        },
        "scripted": {"ready": True, "model": "scénario simulé", "hint": None},
    }


class AlfredServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address=("127.0.0.1", 8765), cases_dir="cas_de_test", reports_dir="rapports", max_steps=20):
        super().__init__(address, AlfredHandler)
        self.cases_dir = Path(cases_dir)
        self.missions = MissionManager(Path(reports_dir), max_steps)
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.allowed_origins = {f"http://{host}" for host in self.allowed_hosts}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"

    def status(self) -> dict:
        current = self.missions.current
        return {
            "providers": providers_status(),
            "mission": current.summary() if current else None,
            "max_steps": self.missions.max_steps,
        }


class AlfredHandler(BaseHTTPRequestHandler):
    server: AlfredServer
    server_version = "Alfred"
    sys_version = ""

    def log_message(self, format, *args):  # pas de journal HTTP dans le terminal
        pass

    # --- Routage -------------------------------------------------------------

    def do_GET(self):
        if not self._host_ok():
            return
        path = urlsplit(self.path).path
        if path == "/":
            return self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        if path.startswith("/polices/") and FONT_FILE.fullmatch(path.removeprefix("/polices/")):
            return self._send_file(STATIC_DIR / "polices" / path.removeprefix("/polices/"), "font/woff", cache=True)
        if path == "/api/status":
            return self._json(200, self.server.status())
        if path == "/api/cases":
            return self._json(200, {"cases": list_cases(self.server.cases_dir)})
        if match := re.fullmatch(rf"/api/missions/({MISSION_ID})/events", path):
            return self._events(match.group(1))
        if match := re.fullmatch(rf"/missions/({MISSION_ID})/(.+)", path):
            return self._mission_file(match.group(1), unquote(match.group(2)))
        self._json(404, {"error": "Adresse inconnue."})

    def do_POST(self):
        if not self._host_ok() or not self._post_ok():
            return
        path = urlsplit(self.path).path
        if path == "/api/missions":
            return self._start_mission()
        if path == "/api/cases":
            return self._save_case()
        if match := re.fullmatch(rf"/api/missions/({MISSION_ID})/stop", path):
            return self._stop_mission(match.group(1))
        self._json(404, {"error": "Adresse inconnue."})

    # --- Protections -----------------------------------------------------------

    def _host_ok(self) -> bool:
        if self.headers.get("Host", "") in self.server.allowed_hosts:
            return True
        self._json(403, {"error": "Alfred ne répond qu'aux adresses locales (127.0.0.1 ou localhost)."})
        return False

    def _post_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.allowed_origins:
            self._json(403, {"error": "Requête venant d'un autre site : refusée."})
            return False
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._json(415, {"error": "Le corps de la requête doit être au format JSON."})
            return False
        return True

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("Requête trop volumineuse.")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("Un objet JSON est attendu.")
        return data

    # --- Missions ----------------------------------------------------------------

    def _start_mission(self):
        try:
            body = self._body()
            case, scenario = self._case_from(body)
            provider = self._provider_from(body.get("provider", "gemini"), scenario)
            pause = min(max(float(body.get("pause") or 0), 0.0), MAX_PAUSE)
            mission = self.server.missions.start(case, provider, headed=bool(body.get("headed")), pause_s=pause)
        except MissionBusy as exc:
            return self._json(409, {"error": str(exc)})
        except (ValueError, TypeError, ProviderError) as exc:
            return self._json(400, {"error": str(exc)})
        self._json(201, {"id": mission.id, "events": f"/api/missions/{mission.id}/events"})

    def _case_from(self, body: dict) -> tuple[TestCase, Path | None]:
        if body.get("case"):
            path = resolve_case(self.server.cases_dir, str(body["case"]))
            case = parse_test_case(path.read_text(encoding="utf-8"))
            return case, scenario_for(path, case)
        if body.get("markdown"):
            return parse_test_case(str(body["markdown"])), None
        raise ValueError("Choisissez un cas de test ou écrivez-en un.")

    @staticmethod
    def _provider_from(name: str, scenario: Path | None):
        if name == "scripted":
            if scenario is None:
                raise ValueError("La démo sans IA n'existe que pour les cas qui ont un scénario enregistré, comme CT-00.")
            return ScriptedProvider.from_file(scenario)
        if name not in ("gemini", "claude"):
            raise ValueError(f"Moteur inconnu : {name}")
        return make_provider(name)

    def _stop_mission(self, mission_id: str):
        mission = self.server.missions.missions.get(mission_id)
        if mission is None:
            return self._json(404, {"error": "Mission inconnue."})
        mission.stop_event.set()
        self._json(202, {"stopping": not mission.finished})

    def _events(self, mission_id: str):
        """Flux Server-Sent Events : chaque événement de la mission, dès qu'il arrive."""
        mission = self.server.missions.missions.get(mission_id)
        if mission is None:
            return self._json(404, {"error": "Mission inconnue."})
        last = self.headers.get("Last-Event-ID", "")
        index = int(last) + 1 if last.isdigit() else 0  # reprise après une reconnexion
        if mission.finished and index >= len(mission.events):
            self.send_response(204)  # rien de plus : le navigateur arrête de se reconnecter
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            while True:
                events, finished = mission.events_after(index, timeout=15)
                for event in events:
                    data = json.dumps(event, ensure_ascii=False)
                    self.wfile.write(f"id: {event['id']}\nevent: {event['type']}\ndata: {data}\n\n".encode())
                index += len(events)
                if not events and not finished:
                    self.wfile.write(b": en attente\n\n")  # garde la connexion ouverte
                self.wfile.flush()
                if finished and index >= len(mission.events):
                    return
        except (BrokenPipeError, ConnectionResetError):
            return  # la page a été fermée

    def _mission_file(self, mission_id: str, relative: str):
        mission = self.server.missions.missions.get(mission_id)
        root = mission.out_dir.resolve() if mission else None
        path = (root / relative).resolve() if root else None
        if path is None or not path.is_relative_to(root) or path.suffix not in MISSION_FILE_TYPES or not path.is_file():
            return self._json(404, {"error": "Fichier introuvable."})
        self._send_file(path, MISSION_FILE_TYPES[path.suffix])

    # --- Cas de test -------------------------------------------------------------

    def _save_case(self):
        try:
            text = str(self._body().get("markdown", "")).strip()
            case = parse_test_case(text)
        except (ValueError, TypeError) as exc:
            return self._json(400, {"error": str(exc)})
        code = case_code(case.title)
        name = f"{code}_{slugify(case.title[len(code):])}.md" if code else f"{case.slug}.md"
        path = self.server.cases_dir / name
        if path.exists():
            return self._json(409, {"error": f"Le fichier {name} existe déjà dans cas_de_test : changez le titre."})
        path.write_text(text + "\n", encoding="utf-8")
        self._json(201, {"id": name})

    # --- Réponses ----------------------------------------------------------------

    def _send_file(self, path: Path, content_type: str, cache: bool = False):
        try:
            data = path.read_bytes()
        except OSError:
            return self._json(404, {"error": "Fichier introuvable."})
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)
