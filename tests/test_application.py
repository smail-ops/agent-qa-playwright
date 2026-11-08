"""Alfred comme une application : la page signale sa présence, le bouton Quitter, l'arrêt automatique et Alfred.app."""

import os
import plistlib
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from playwright.sync_api import expect

from agent import __version__
from alfred.server import AlfredServer
from test_alfred import alfred, request  # noqa: F401  (alfred : le serveur des tests avec les cas de démo)

RACINE = Path(__file__).resolve().parent.parent
APP = RACINE / "Alfred.app"
URL = "http://127.0.0.1:8765/"


def demarrer(tmp_path, **options):
    """Un serveur Alfred dans un fil : le fil se termine quand Alfred s'arrête."""
    (tmp_path / "cas_de_test").mkdir(exist_ok=True)
    server = AlfredServer(("127.0.0.1", 0), tmp_path / "cas_de_test", tmp_path / "rapports", env_path=tmp_path / ".env",
                          **options)
    fil = threading.Thread(target=server.serve_forever, daemon=True)
    fil.start()
    return server, fil


def mission_factice(finie=False):
    return SimpleNamespace(finished=finie, stop_event=threading.Event(), serie=None)


# --- Quitter et arrêt automatique ---------------------------------------------------------------


def test_quitter_arrete_la_mission_puis_alfred(tmp_path):
    server, fil = demarrer(tmp_path)
    mission = mission_factice()
    server.missions.current = mission
    status, data = request(server, "POST", "/api/quitter", {})
    assert status == 202 and data == {"arret": True, "mission_arretee": True}
    fil.join(timeout=5)
    assert not fil.is_alive()
    assert mission.stop_event.is_set()
    server.server_close()


def test_quitter_refuse_une_requete_d_un_autre_site(tmp_path):
    server, fil = demarrer(tmp_path)
    status, data = request(server, "POST", "/api/quitter", {}, headers={"Origin": "https://exemple.com"})
    assert status == 403 and "autre site" in data["error"]
    time.sleep(0.3)
    assert fil.is_alive() and not server.arrete.is_set()
    server.shutdown()
    server.server_close()


def test_sans_arret_automatique_alfred_reste_en_marche(tmp_path):
    server, fil = demarrer(tmp_path)
    time.sleep(0.5)
    assert server.arret_auto is None and fil.is_alive()
    server.shutdown()
    server.server_close()


def test_arret_automatique_sans_fenetre_ouverte(tmp_path):
    server, fil = demarrer(tmp_path, arret_auto=0.4, periode_veille=0.05)
    fil.join(timeout=5)
    assert not fil.is_alive()
    server.server_close()


def test_la_page_ouverte_garde_alfred_en_marche(tmp_path):
    server, fil = demarrer(tmp_path, arret_auto=0.5, periode_veille=0.05)
    fin = time.monotonic() + 1.5
    while time.monotonic() < fin:
        assert request(server, "POST", "/api/presence", {})[0] == 200
        time.sleep(0.1)
    assert fil.is_alive()
    fil.join(timeout=5)  # plus aucun signal de la page : Alfred s'arrête
    assert not fil.is_alive()
    server.server_close()


def test_pas_d_arret_automatique_pendant_une_mission(tmp_path):
    server, fil = demarrer(tmp_path, arret_auto=0.3, periode_veille=0.05)
    mission = mission_factice()
    server.missions.current = mission
    time.sleep(1.0)
    assert fil.is_alive() and not mission.stop_event.is_set()
    mission.finished = True
    fil.join(timeout=5)
    assert not fil.is_alive()
    server.server_close()


def test_arret_auto_refuse_une_duree_nulle():
    resultat = subprocess.run([sys.executable, "-m", "alfred", "--arret-auto", "0"], cwd=RACINE, capture_output=True,
                              text=True, timeout=120)
    assert resultat.returncode == 2 and "nombre de minutes positif" in resultat.stderr


def test_bouton_quitter_de_la_page(alfred, pw_page):  # noqa: F811
    errors = []
    pw_page.on("pageerror", lambda exc: errors.append(str(exc)))
    avant = alfred.derniere_presence
    pw_page.goto(alfred.url)
    expect(pw_page.locator(".carte-cas")).to_have_count(3)
    limite = time.monotonic() + 5
    while alfred.derniere_presence == avant and time.monotonic() < limite:
        time.sleep(0.05)
    assert alfred.derniere_presence > avant  # la page s'est signalée en arrivant

    pw_page.click("#bouton-quitter")
    expect(pw_page.locator("#bouton-quitter-texte")).to_have_text("Confirmer")
    assert not alfred.arrete.is_set()  # un seul clic ne suffit pas
    pw_page.click("#bouton-quitter")
    expect(pw_page.locator("#arret")).to_be_visible()
    expect(pw_page.locator(".arret-titre")).to_have_text("Alfred est arrêté.")
    assert alfred.arrete.is_set()
    assert errors == []


# --- Alfred.app -------------------------------------------------------------------------------


def test_alfred_app_est_une_application_mac():
    infos = plistlib.loads((APP / "Contents" / "Info.plist").read_bytes())
    assert infos["CFBundleExecutable"] == "Alfred" and infos["CFBundlePackageType"] == "APPL"
    assert infos["CFBundleIdentifier"] == "io.github.smail-ops.alfred"
    assert infos["CFBundleShortVersionString"] == __version__
    lanceur = APP / "Contents" / "MacOS" / infos["CFBundleExecutable"]
    assert os.access(lanceur, os.X_OK), "le lanceur doit être exécutable (git update-index --chmod=+x)"
    assert lanceur.read_text(encoding="utf-8").startswith("#!/bin/sh\n")
    assert (APP / "Contents" / "Resources" / f"{infos['CFBundleIconFile']}.icns").read_bytes()[:4] == b"icns"


def _outil(chemin: Path, corps: str) -> None:
    chemin.write_text("#!/bin/sh\n" + corps + "\n", encoding="utf-8")
    chemin.chmod(0o755)


def _lanceur(tmp_path, *, venv=True, chrome=True, deja_lance=False, app_ailleurs=False):
    """Un projet factice avec Alfred.app, et des doublures de curl, osascript, open et du Python du projet."""
    projet, outils, traces, maison = (tmp_path / nom for nom in ("projet", "outils", "traces", "maison"))
    for dossier in (projet, outils, traces, maison):
        dossier.mkdir()
    app = (tmp_path / "Applications" if app_ailleurs else projet) / "Alfred.app"
    shutil.copytree(APP, app)
    pret = tmp_path / "alfred-pret"
    if deja_lance:
        pret.touch()
    if venv:
        python = projet / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        _outil(python, f'echo "$(pwd) $*" > "{traces}/python"\ntouch "{pret}"')
    _outil(outils / "curl", f'[ -f "{pret}" ]')
    _outil(outils / "osascript", f'printf "%s\\n" "$2" >> "{traces}/osascript"\n'
                                 f'case "$2" in *"id of application"*) exit {0 if chrome else 1} ;; esac')
    _outil(outils / "open", f'echo "$*" > "{traces}/open"')
    env = {"PATH": f"{outils}:/usr/bin:/bin", "HOME": str(maison)}
    return app, projet, traces, maison, env


def _lancer(app, env):
    return subprocess.run(["/bin/sh", str(app / "Contents" / "MacOS" / "Alfred")], env=env, capture_output=True,
                          text=True, timeout=60)


def _trace(traces: Path, outil: str) -> str:
    chemin = traces / outil
    return chemin.read_text(encoding="utf-8").strip() if chemin.exists() else ""


def test_alfred_app_demarre_alfred_et_ouvre_sa_fenetre(tmp_path):
    app, projet, traces, maison, env = _lanceur(tmp_path)
    resultat = _lancer(app, env)
    assert resultat.returncode == 0, resultat.stderr
    assert _trace(traces, "python") == f"{projet} -m alfred --no-browser --arret-auto"
    assert _trace(traces, "open") == f"-na Google Chrome --args --app={URL}"
    reglage = maison / "Library" / "Application Support" / "Alfred" / "projet"
    assert reglage.read_text(encoding="utf-8").strip() == str(projet)
    assert (maison / "Library" / "Logs" / "Alfred.log").read_text(encoding="utf-8").startswith("\n--- ")


def test_alfred_app_rouvre_seulement_la_fenetre_si_alfred_tourne(tmp_path):
    app, _, traces, _, env = _lanceur(tmp_path, deja_lance=True)
    assert _lancer(app, env).returncode == 0
    assert _trace(traces, "python") == ""  # pas de second Alfred
    assert _trace(traces, "open") == f"-na Google Chrome --args --app={URL}"


def test_alfred_app_sans_chrome_ouvre_le_navigateur_par_defaut(tmp_path):
    app, _, traces, _, env = _lanceur(tmp_path, chrome=False)
    assert _lancer(app, env).returncode == 0
    assert _trace(traces, "open") == URL


def test_alfred_app_sans_installation_previent(tmp_path):
    app, _, traces, _, env = _lanceur(tmp_path, venv=False)
    assert _lancer(app, env).returncode == 1
    assert "Environnement .venv introuvable" in _trace(traces, "osascript")
    assert _trace(traces, "open") == ""


def test_alfred_app_copiee_ailleurs_retrouve_le_projet(tmp_path):
    app, projet, traces, maison, env = _lanceur(tmp_path, app_ailleurs=True)
    reglage = maison / "Library" / "Application Support" / "Alfred" / "projet"
    reglage.parent.mkdir(parents=True)
    reglage.write_text(f"{projet}\n", encoding="utf-8")  # enregistré au dernier lancement depuis le projet
    assert _lancer(app, env).returncode == 0
    assert _trace(traces, "python").startswith(f"{projet} -m alfred")
