"""Serveur web local d'Alfred, écrit avec la seule bibliothèque standard de Python.

Routes :
    GET  /                              la page d'Alfred
    GET  /api/status                    moteurs disponibles, mission en cours, maître, salutation et présentation d'Alfred
    POST /api/maitre                    enregistre le prénom du maître dans le .env de cet ordinateur
    GET  /api/cases                     cas de test du dossier cas_de_test/
    POST /api/cases                     enregistre un cas de test écrit dans la page
    POST /api/missions                  lance une mission (« memoire »: false pour tout refaire avec l'IA,
                                        « vision »: true pour envoyer aussi la capture d'écran au modèle)
    POST /api/propositions/stories      analyse un site et propose des user stories (critères en Gherkin)
    POST /api/propositions/cas          propose des cas de test pour une user story
    POST /api/propositions/enregistrer  enregistre les propositions choisies dans cas_de_test/
    POST /api/conversation              une phrase libre du testeur : réponse d'Alfred et action contrôlée
    GET  /api/missions/<id>/events      événements de la mission, en direct (Server-Sent Events)
    POST /api/missions/<id>/stop        demande l'arrêt de la mission
    GET  /missions/<id>/<fichier>       captures, rapport et tests générés
    GET  /rapports/<dossier>/<fichier>  fichiers d'une exécution passée (Alfred les ouvre quand on les lui demande)

Sécurité : Alfred n'écoute que sur 127.0.0.1, refuse les requêtes dont l'en-tête
Host n'est pas local (protection contre le « DNS rebinding »), refuse les POST
venant d'un autre site et ne sert que les fichiers de ses propres missions.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from playwright.sync_api import Error as PlaywrightError

from agent import documentation
from agent.analyse import AnalysisError, SiteAnalysis, analyse_site
from agent.memoire import Memory
from agent.propositions import (
    case_from_dict,
    propose_cases,
    propose_stories,
    save_cases,
    save_stories,
    story_text,
)
from agent.providers import (
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_GEMINI_MODEL,
    ProviderError,
    ScriptedProvider,
    make_provider,
)
from agent.resultat import read_results
from agent.testcase import TestCase, parse_test_case, slugify
from agent.user_stories import UserStory, feature_cases, story_from_dict

from . import maitre, narration
from .conversation import MAX_TEXT, build_context, converse, default_url, secrets_of
from .missions import MissionBusy, MissionManager

STATIC_DIR = Path(__file__).resolve().parent / "static"
FONT_FILE = re.compile(r"[a-z0-9-]+\.woff")
MISSION_ID = r"[\w.-]+"
CASE_CODE = re.compile(r"\s*([A-Z]{2,}-\d+)\b")
MAX_BODY = 200_000  # octets
MAX_PAUSE = 30.0  # secondes
TEXT = "text/plain; charset=utf-8"
MISSION_FILE_TYPES = {".png": "image/png", ".md": TEXT, ".py": TEXT, ".robot": TEXT, ".feature": TEXT}
DEMO_PROPOSALS = "propositions_demo.json"  # réponses enregistrées : propositions de la démo sans IA
DEMO_ORIGIN = "http://localhost:8000"
MAX_SAVED = 10  # propositions enregistrées en une fois
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


def list_cases(cases_dir: Path, memory: Memory | None = None) -> list[dict]:
    """Les cas Markdown, puis chaque scénario des fichiers Gherkin (.feature), qui se lance comme un cas."""
    found: list[tuple[str, Path, TestCase, int]] = []  # (identifiant, fichier, cas, numéro du scénario)
    for path in cases_dir.rglob("*.md"):
        try:
            found.append((path.relative_to(cases_dir).as_posix(), path, parse_test_case(path.read_text(encoding="utf-8")), 0))
        except (OSError, UnicodeDecodeError, ValueError):
            continue  # ce fichier n'est pas un cas de test
    for path in cases_dir.rglob("*.feature"):
        try:
            scenarios = feature_cases(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        name = path.relative_to(cases_dir).as_posix()
        found += [(f"{name}#{number}", path, case, number) for number, case in enumerate(scenarios, 1)]
    cases = []
    for case_id, path, case, number in found:
        cases.append({
            "id": case_id,
            "code": case_code(case.title),
            "title": case.title,
            "url": case.url,
            "host": urlsplit(case.url).netloc,
            "text": case.text,
            "gherkin": number > 0,  # scénario d'un fichier .feature
            "demo": scenario_for(path, case) is not None,
            "memory": memory.info(case) if memory is not None else None,  # parcours déjà réussi, rejoué sans IA
            "_tri": (case_code(case.title) is None, case_code(case.title) or "", case_id.partition("#")[0], number),
        })
    cases.sort(key=lambda c: c.pop("_tri"))
    return cases


def load_case(cases_dir: Path, case_id: str) -> tuple[TestCase, Path]:
    """Un cas de test (« CT-01_x.md », ou « US-01_x.feature#2 » pour un scénario), sans sortir du dossier des cas."""
    name, _, number = case_id.partition("#")
    root = cases_dir.resolve()
    path = (root / name).resolve()
    if path.is_relative_to(root) and path.is_file():
        if path.suffix == ".md" and not number:
            return parse_test_case(path.read_text(encoding="utf-8")), path
        if path.suffix == ".feature" and number.isdigit():
            scenarios = feature_cases(path.read_text(encoding="utf-8"))
            if 1 <= int(number) <= len(scenarios):
                return scenarios[int(number) - 1], path
    raise ValueError("Cas de test introuvable.")


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

    def __init__(self, address=("127.0.0.1", 8765), cases_dir="cas_de_test", reports_dir="rapports", max_steps=20,
                 env_path=".env", memory_dir=None):
        super().__init__(address, AlfredHandler)
        self.cases_dir = Path(cases_dir)
        self.env_path = Path(env_path)  # le .env de cet ordinateur : clés d'API et prénom du maître
        self.missions = MissionManager(Path(reports_dir), max_steps, memory_dir)
        self.memory = Memory(self.missions.memory_dir)
        self.proposing = threading.Lock()  # une proposition à la fois : chacune coûte une requête au modèle
        self.talking = threading.Lock()  # une phrase à la fois dans la conversation libre
        self.last_analysis: SiteAnalysis | None = None  # sert à rédiger les cas avec les libellés exacts du site
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.allowed_origins = {f"http://{host}" for host in self.allowed_hosts}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"

    def status(self) -> dict:
        current = self.missions.current
        nom = maitre.nom_du_maitre()
        return {
            "providers": providers_status(),
            "mission": current.summary() if current else None,
            "max_steps": self.missions.max_steps,
            "maitre": {"nom": nom, "suggestion": maitre.suggestion(), "ordinateur": socket.gethostname()},
            "salutation": maitre.salutation(nom),
            "identite": maitre.identite(nom),
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
            return self._json(200, {"cases": list_cases(self.server.cases_dir, self.server.memory)})
        if match := re.fullmatch(rf"/api/missions/({MISSION_ID})/events", path):
            return self._events(match.group(1))
        if match := re.fullmatch(rf"/missions/({MISSION_ID})/(.+)", path):
            return self._mission_file(match.group(1), unquote(match.group(2)))
        if match := re.fullmatch(r"/rapports/(\d{8}-\d{6}_[\w.-]+)/([\w.-]+)", path):
            return self._report_file(match.group(1), match.group(2))
        self._json(404, {"error": "Adresse inconnue."})

    def do_POST(self):
        if not self._host_ok() or not self._post_ok():
            return
        path = urlsplit(self.path).path
        if path == "/api/missions":
            return self._start_mission()
        if path == "/api/cases":
            return self._save_case()
        if path == "/api/maitre":
            return self._save_master()
        if match := re.fullmatch(rf"/api/missions/({MISSION_ID})/stop", path):
            return self._stop_mission(match.group(1))
        if path == "/api/propositions/stories":
            return self._propose(self._stories)
        if path == "/api/propositions/cas":
            return self._propose(self._cases)
        if path == "/api/propositions/enregistrer":
            return self._save_proposals()
        if path == "/api/conversation":
            return self._converse()
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
            if body.get("url"):  # champ « Site à tester » : même cas, autre adresse
                case = case.with_url(str(body["url"]))
            provider = self._provider_from(body.get("provider", "gemini"), scenario)
            pause = min(max(float(body.get("pause") or 0), 0.0), MAX_PAUSE)
            mission = self.server.missions.start(case, provider, headed=bool(body.get("headed")), pause_s=pause,
                                                 use_memory=body.get("memoire", True) is not False,
                                                 vision=body.get("vision") is True)
        except MissionBusy as exc:
            return self._json(409, {"error": str(exc)})
        except (ValueError, TypeError, ProviderError) as exc:
            return self._json(400, {"error": str(exc)})
        self._json(201, {"id": mission.id, "events": f"/api/missions/{mission.id}/events"})

    def _case_from(self, body: dict) -> tuple[TestCase, Path | None]:
        if body.get("case"):
            case, path = load_case(self.server.cases_dir, str(body["case"]))
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

    def _report_file(self, folder: str, name: str):
        """Un fichier d'une exécution passée, sans jamais sortir du dossier des rapports."""
        root = self.server.missions.reports_dir.resolve()
        path = (root / folder / name).resolve()
        if not path.is_relative_to(root) or path.suffix not in MISSION_FILE_TYPES or not path.is_file():
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

    # --- Propositions : user stories et cas de test -----------------------------------

    def _propose(self, handler):
        """Une proposition à la fois, et jamais pendant une mission : chacune demande une requête au modèle."""
        current = self.server.missions.current
        if current is not None and not current.finished:
            return self._json(409, {"error": "Une mission est en cours : attendez sa fin pour demander des propositions."})
        if not self.server.proposing.acquire(blocking=False):
            return self._json(409, {"error": "Je prépare déjà des propositions : un instant."})
        try:
            body = self._body()
            url = str(body.get("url") or "").strip()
            if not re.fullmatch(r"https?://[^\s/?#]+\S*", url, flags=re.IGNORECASE):
                raise AnalysisError("L'adresse du site doit commencer par http:// ou https://.")
            return handler(body, url)
        except (AnalysisError, TypeError) as exc:
            return self._json(400, {"error": str(exc)})
        except ProviderError as exc:
            return self._json(502, {"error": f"Le modèle n'a pas pu répondre : {exc}"})
        except ValueError as exc:  # réponse du modèle inutilisable, ou demande invalide
            return self._json(502 if getattr(exc, "du_modele", False) else 400, {"error": str(exc)})
        except PlaywrightError as exc:
            first_line = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else type(exc).__name__
            return self._json(502, {"error": f"Le navigateur n'a pas pu analyser le site : {first_line}"})
        finally:
            self.server.proposing.release()

    def _proposal_provider(self, body: dict, url: str, kind: str, story: str = ""):
        """Gemini, Claude, ou les réponses enregistrées de la démo sans IA (site de démo local seulement)."""
        name = body.get("provider", "gemini")
        if name != "scripted":
            return self._provider_from(name, None)
        address = urlsplit(url)
        if address.hostname not in ("localhost", "127.0.0.1"):
            raise ValueError("La démo sans IA des propositions ne fonctionne qu'avec le site de démo local "
                             f"({DEMO_ORIGIN}). Pour un autre site, choisissez Gemini ou Claude.")
        demo = json.loads((self.server.cases_dir / "demo" / DEMO_PROPOSALS).read_text(encoding="utf-8"))
        answer = demo["stories"] if kind == "stories" else demo["cas"].get(story) or next(iter(demo["cas"].values()))
        raw = json.dumps(answer, ensure_ascii=False).replace(DEMO_ORIGIN, f"{address.scheme}://{address.netloc}")
        return ScriptedProvider([{"raw": raw}])

    @staticmethod
    def _from_model(call):
        """Une réponse du modèle mal formée donne une erreur 502, pas 400 : la demande, elle, était valide."""
        try:
            return call()
        except ValueError as exc:
            error = ValueError(f"La réponse du modèle est inutilisable ({exc}). Réessayez.")
            error.du_modele = True
            raise error from exc

    def _stories(self, body: dict, url: str):
        try:
            provider = self._proposal_provider(body, url, "stories")
        except OSError:
            raise ValueError("Les réponses de la démo sans IA sont introuvables (cas_de_test/demo).") from None
        vision = body.get("vision") is True
        username = str(body.get("identifiant") or "").strip() or None
        password = str(body.get("mot_de_passe") or "") or None  # sert seulement à la connexion : jamais gardé
        analysis = analyse_site(url, username, password, vision=vision)
        password = None
        self.server.last_analysis = analysis
        stories = self._from_model(lambda: propose_stories(analysis, provider, vision=vision))
        self._json(200, {"analyse": analysis.to_dict(), "stories": [story.to_dict() for story in stories],
                         "say": narration.stories_speech(analysis, len(stories))})

    def _cases(self, body: dict, url: str):
        story = body.get("story")
        if isinstance(story, dict):  # une user story proposée par Alfred : relue comme à son arrivée
            parsed = story_from_dict(story)
            title, text = parsed.title, story_text(parsed)
        else:
            text = " ".join(str(body.get("texte") or "").split())
            if len(text) < 15:
                raise ValueError("Écrivez la user story, par exemple : En tant que client, je veux trier les produits "
                                 "par prix, afin de trouver le moins cher.")
            title = ""
        try:
            provider = self._proposal_provider(body, url, "cas", title)
        except OSError:
            raise ValueError("Les réponses de la démo sans IA sont introuvables (cas_de_test/demo).") from None
        last = self.server.last_analysis
        site = last.to_prompt() if last is not None and urlsplit(last.url).netloc == urlsplit(url).netloc else ""
        cases = self._from_model(lambda: propose_cases(text, url, provider, site))
        self._json(200, {"cas": [case.to_dict() for case in cases], "say": narration.cases_speech(len(cases), title)})

    def _save_proposals(self):
        """Enregistre ce que le testeur a gardé. Tout est relu à nouveau : la page ne fait jamais foi."""
        try:
            body = self._body()
            url = str(body.get("url") or "").strip()
            if not re.fullmatch(r"https?://[^\s/?#]+\S*", url, flags=re.IGNORECASE):
                raise ValueError("L'adresse du site doit commencer par http:// ou https://.")
            stories: list[UserStory] = [story_from_dict(item) for item in (body.get("stories") or [])[:MAX_SAVED]]
            cases = [case_from_dict(item) for item in (body.get("cas") or [])[:MAX_SAVED]]
            if not stories and not cases:
                raise ValueError("Cochez au moins une proposition à enregistrer.")
        except (ValueError, TypeError) as exc:
            return self._json(400, {"error": str(exc)})
        paths = save_stories(stories, url, self.server.cases_dir) + save_cases(cases, url, self.server.cases_dir)
        ids = [path.relative_to(self.server.cases_dir).as_posix() for path in paths]
        first = ids[0] + ("#1" if ids[0].endswith(".feature") else "")
        self._json(201, {"files": ids, "first": first, "say": narration.saved_speech(len(stories), len(cases))})

    # --- Conversation libre ---------------------------------------------------------

    def _conversation_provider(self, wanted: str):
        """Le moteur choisi s'il est prêt, sinon l'autre : la démo sans IA ne sait pas converser."""
        ready = providers_status()
        for name in dict.fromkeys([wanted, "gemini", "claude"]):
            if name in ("gemini", "claude") and ready[name]["ready"]:
                return self._provider_from(name, None)
        return None

    def _converse(self):
        if not self.server.talking.acquire(blocking=False):
            return self._json(409, {"error": "Je vous réponds déjà : un instant, s'il vous plaît."})
        try:
            body = self._body()
            text = " ".join(str(body.get("texte") or "").split())[:MAX_TEXT]
            if not text:
                return self._json(400, {"error": "Je n'ai rien entendu."})
            provider = self._conversation_provider(str(body.get("provider") or ""))
            pages = documentation.question_pages(text)
            if provider is None and pages:  # sans clé, une question sur un outil reçoit au moins les bonnes pages
                return self._json(200, {"reponse": "Pour vous répondre, il me faudrait une clé Gemini ou Claude. Voici déjà "
                                                   "les pages de la documentation officielle qui en parlent.",
                                        "action": {"type": "aucune"}, "sources": documentation.links(pages)})
            if provider is None:
                return self._json(503, {"error": "Je n'ai pas compris cette phrase, et pour une conversation libre il me "
                                                 "faut une clé Gemini ou Claude dans le fichier .env. Sans clé, dites par "
                                                 "exemple : lance la démo, ou lance le cas 1."})
            cases = list_cases(self.server.cases_dir, self.server.memory)
            selection = next((case for case in cases if case["id"] == body.get("selection")), None)
            current = self.server.missions.current
            mission = None
            if current is not None and not current.finished:
                done = sum(1 for event in current.events if event["type"] == "step")
                mission = f"{current.case.title} ({done} étape{'s' if done > 1 else ''} réalisée{'s' if done > 1 else ''})"
            files = [str(name) for name in body.get("fichiers") or [] if str(name) in ("rapport", "anomalie", "pytest",
                                                                                        "robot", "gherkin", "robot_bdd")]
            history = body.get("historique") if isinstance(body.get("historique"), list) else []
            results = read_results(self.server.missions.reports_dir, limit=100)  # pour retrouver un ancien rapport
            context = build_context(cases, results[:12], selection, mission, maitre.nom_du_maitre(), files)
            reply = self._from_model(lambda: converse(provider, text, history, context, cases, self.server.cases_dir,
                                                      default_url(cases, selection), secrets_of([c["text"] for c in cases]),
                                                      results))
        except ProviderError as exc:
            return self._json(502, {"error": f"Je ne peux pas consulter l'IA pour l'instant : {exc}"})
        except ValueError as exc:
            return self._json(502 if getattr(exc, "du_modele", False) else 400, {"error": str(exc)})
        finally:
            self.server.talking.release()
        self._json(200, reply)

    # --- Maître -------------------------------------------------------------------

    def _save_master(self):
        try:
            nom = maitre.enregistrer(self._body().get("nom", ""), self.server.env_path)
        except (ValueError, TypeError, OSError) as exc:
            return self._json(400, {"error": str(exc)})
        self._json(200, {"nom": nom, "salutation": maitre.salutation(nom), "identite": maitre.identite(nom)})

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
