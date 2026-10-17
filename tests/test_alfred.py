"""Alfred : narration, serveur local (API, flux en direct, sécurité) et interface web."""

import http.client
import json
import shutil
import threading
from pathlib import Path

import pytest
from playwright.sync_api import expect

from agent.actions import Action
from agent.browser import Element
from alfred import narration
from alfred.server import AlfredServer

DEMO_DIR = Path(__file__).resolve().parent.parent / "cas_de_test" / "demo"


def element(tag="input", type_=None, text="", **attrs):
    return Element(ref=1, tag=tag, type=type_, text=text, attrs=attrs, selector="#x")


# --- Narration ---------------------------------------------------------------------


def test_noms_des_elements():
    assert narration.element_name(element(placeholder="Username")) == "le champ « Username »"
    assert narration.element_name(element("input", "submit", text="Login")) == "le bouton « Login »"
    assert narration.element_name(element("a", text="Panier")) == "le lien « Panier »"
    assert narration.element_name(element("select", name="sort")) == "la liste « sort »"


def test_le_mot_de_passe_reste_secret():
    champ = element("input", "password", placeholder="Password")
    action = Action("fill", {"ref": 1, "text": "secret_sauce"})
    assert "secret_sauce" not in narration.action_label(action, champ)
    assert narration.action_speech(action, champ) == "Je saisis le mot de passe."


def test_libelles_des_verifications():
    assert narration.action_label(Action("check_text", {"text": "Products"})) == "Vérification : « Products » s'affiche"
    negative = Action("check_url", {"contains": "inventory", "absent": True})
    assert narration.action_label(negative) == "Vérification : l'URL ne contient pas « inventory »"


# --- Serveur -------------------------------------------------------------------------


@pytest.fixture
def alfred(tmp_path, demo_url):
    """Alfred avec un dossier de cas de test : la démo pointe vers le site de démo des tests."""
    cases = tmp_path / "cas_de_test"
    (cases / "demo").mkdir(parents=True)
    text = (DEMO_DIR / "CT-00_demo_locale.md").read_text(encoding="utf-8")
    (cases / "demo" / "CT-00_demo_locale.md").write_text(text.replace("http://localhost:8000", demo_url), encoding="utf-8")
    shutil.copy(DEMO_DIR / "scenario_CT-00.json", cases / "demo")
    (cases / "CT-01_autre.md").write_text(f"# CT-01 Autre cas\nURL : {demo_url}/index.html\n", encoding="utf-8")
    server = AlfredServer(("127.0.0.1", 0), cases, tmp_path / "rapports")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def request(server, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=60)
    sent = {"Content-Type": "application/json"} if body is not None else {}
    sent.update(headers or {})
    connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=sent)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    kind = response.getheader("Content-Type", "")
    return response.status, json.loads(data) if kind.startswith("application/json") else data


def read_events(server, mission_id, until=("done", "fault"), on_event=None):
    """Lit le flux Server-Sent Events d'une mission jusqu'au verdict."""
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=60)
    connection.request("GET", f"/api/missions/{mission_id}/events")
    response = connection.getresponse()
    assert response.getheader("Content-Type").startswith("text/event-stream")
    events, kind = [], None
    for raw in response:
        line = raw.decode().rstrip("\n")
        if line.startswith("event: "):
            kind = line[len("event: "):]
        elif line.startswith("data: "):
            event = json.loads(line[len("data: "):])
            assert event["type"] == kind
            events.append(event)
            if on_event:
                on_event(event)
            if kind in until:
                break
    connection.close()
    return events


def test_liste_des_cas(alfred):
    status, data = request(alfred, "GET", "/api/cases")
    assert status == 200
    assert [(c["code"], c["demo"]) for c in data["cases"]] == [("CT-00", True), ("CT-01", False)]
    assert data["cases"][0]["id"] == "demo/CT-00_demo_locale.md"


def test_les_cles_ne_sortent_pas(alfred, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "cle-secrete-de-test")
    status, data = request(alfred, "GET", "/api/status")
    assert status == 200 and data["providers"]["gemini"]["ready"] is True
    assert "cle-secrete-de-test" not in json.dumps(data)


def test_mission_complete_en_direct(alfred):
    status, data = request(alfred, "POST", "/api/missions", {"case": "demo/CT-00_demo_locale.md", "provider": "scripted"})
    assert status == 201, data
    events = read_events(alfred, data["id"])

    assert events[0]["type"] == "start" and events[-1]["type"] == "done"
    assert [e["id"] for e in events] == list(range(len(events)))
    steps = [e for e in events if e["type"] == "step"]
    assert [s["number"] for s in steps] == list(range(1, 9))
    assert [s["check"] for s in steps].count(True) == 3
    assert "secret_sauce" not in json.dumps(events)  # le mot de passe n'est jamais diffusé
    phases = [e["phase"] for e in events if e["type"] == "phase"]
    assert phases[:4] == ["act", "observe", "think", "act"]

    done = events[-1]
    assert done["status"] == "PASS" and done["stopped"] is False
    status, robot = request(alfred, "GET", done["files"]["robot"])
    assert status == 200 and "La page affiche    Produits" in robot.decode()
    status, image = request(alfred, "GET", steps[1]["screenshot"])
    assert status == 200 and image.startswith(b"\x89PNG")


def test_site_a_tester(alfred, demo_url):
    """Le même cas sur une autre adresse : le fichier du cas ne change pas."""
    autre = f"{demo_url}/index.html?env=recette"
    corps = {"case": "demo/CT-00_demo_locale.md", "provider": "scripted", "url": autre}
    status, data = request(alfred, "POST", "/api/missions", corps)
    assert status == 201, data
    events = read_events(alfred, data["id"])
    assert events[0]["url"] == autre
    first_step = next(e for e in events if e["type"] == "step")
    assert first_step["label"] == f"Ouverture de {autre}"
    assert events[-1]["status"] == "PASS"
    assert "env=recette" not in (alfred.cases_dir / "demo" / "CT-00_demo_locale.md").read_text(encoding="utf-8")

def test_une_seule_mission_a_la_fois_et_arret(alfred):
    corps = {"case": "demo/CT-00_demo_locale.md", "provider": "scripted", "pause": 5}
    status, data = request(alfred, "POST", "/api/missions", corps)
    assert status == 201
    assert request(alfred, "POST", "/api/missions", corps)[0] == 409

    def stop_after_first_step(event):
        if event["type"] == "step" and event["number"] == 2:
            assert request(alfred, "POST", f"/api/missions/{data['id']}/stop", {})[0] == 202

    done = read_events(alfred, data["id"], on_event=stop_after_first_step)[-1]
    assert done["status"] == "BLOQUE" and done["stopped"] is True
    assert done["say"].startswith("Mission arrêtée")


def test_reprise_apres_rechargement(alfred):
    """Un flux rouvert après la fin renvoie tout ; avec Last-Event-ID final, il répond 204 (fin du flux)."""
    data = request(alfred, "POST", "/api/missions", {"case": "demo/CT-00_demo_locale.md", "provider": "scripted"})[1]
    events = read_events(alfred, data["id"])
    assert read_events(alfred, data["id"]) == events
    status, _ = request(alfred, "GET", f"/api/missions/{data['id']}/events", headers={"Last-Event-ID": str(events[-1]["id"])})
    assert status == 204


@pytest.mark.parametrize(
    "corps, message",
    [
        ({"case": "../../etc/passwd", "provider": "scripted"}, "introuvable"),
        ({"case": "CT-01_autre.md", "provider": "scripted"}, "scénario"),
        ({"markdown": "# CT-09 Sans URL", "provider": "scripted"}, "URL introuvable"),
        ({"case": "CT-01_autre.md", "provider": "gemini"}, "GEMINI_API_KEY"),
        ({"case": "CT-01_autre.md", "provider": "inconnu"}, "Moteur inconnu"),
        ({}, "Choisissez"),
        ({"case": "demo/CT-00_demo_locale.md", "provider": "scripted", "url": "ftp://serveur"}, "http:// ou https://"),
    ],
)
def test_demandes_refusees(alfred, monkeypatch, corps, message):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    status, data = request(alfred, "POST", "/api/missions", corps)
    assert status == 400 and message in data["error"]


def test_protections(alfred):
    assert request(alfred, "GET", "/api/cases", headers={"Host": "pirate.example:80"})[0] == 403  # DNS rebinding
    corps = {"case": "demo/CT-00_demo_locale.md", "provider": "scripted"}
    assert request(alfred, "POST", "/api/missions", corps, headers={"Origin": "https://pirate.example"})[0] == 403
    assert request(alfred, "POST", "/api/missions", corps, headers={"Content-Type": "text/plain"})[0] == 415
    data = request(alfred, "POST", "/api/missions", corps)[1]
    read_events(alfred, data["id"])
    for chemin in ("../../../../etc/passwd", "%2e%2e/%2e%2e/rapport.md", "captures/../../autre/rapport.md"):
        assert request(alfred, "GET", f"/missions/{data['id']}/{chemin}")[0] == 404
    assert request(alfred, "GET", "/missions/inconnue/rapport.md")[0] == 404


def test_enregistrer_un_cas(alfred):
    texte = "# CT-12 Tri des produits\nURL : https://www.saucedemo.com/\n\n## Étapes\n1. Trier par prix\n"
    status, data = request(alfred, "POST", "/api/cases", {"markdown": texte})
    assert status == 201 and data["id"] == "CT-12_tri_des_produits.md"
    assert (alfred.cases_dir / data["id"]).read_text(encoding="utf-8") == texte
    assert request(alfred, "POST", "/api/cases", {"markdown": texte})[0] == 409
    codes = [c["code"] for c in request(alfred, "GET", "/api/cases")[1]["cases"]]
    assert codes == ["CT-00", "CT-01", "CT-12"]


# --- Interface -----------------------------------------------------------------------


def test_interface_mission_de_demo(alfred, pw_page):
    errors = []
    pw_page.on("pageerror", lambda exc: errors.append(str(exc)))
    pw_page.goto(alfred.url)

    expect(pw_page.locator(".carte-cas")).to_have_count(2)
    expect(pw_page.locator('.carte-cas[aria-pressed="true"]')).to_contain_text("CT-00")
    expect(pw_page.locator('input[name="moteur"][value="scripted"]')).to_be_checked()
    expect(pw_page.locator("#voix")).to_contain_text("Quel cas de test dois-je exécuter ?")

    pw_page.fill("#option-pause", "0")
    pw_page.click("#bouton-lancer")
    expect(pw_page.locator(".verdict-mot")).to_have_text("Réussi", timeout=30_000)
    expect(pw_page.locator(".etape")).to_have_count(8)
    expect(pw_page.locator('.etape[data-type="verifie"]')).to_have_count(3)
    expect(pw_page.locator("#cadran-mot")).to_have_text("RÉUSSI")
    assert pw_page.locator("#vision-image").get_attribute("src").endswith("captures/etape-08.png")
    assert pw_page.locator("body").get_attribute("data-etat") == "pass"

    pw_page.click("text=Test Robot")
    expect(pw_page.locator("#fichier-contenu")).to_contain_text("*** Test Cases ***")
    pw_page.click("#fichier-fermer")
    assert errors == []


def test_interface_comprend_les_commandes_vocales(alfred, pw_page):
    pw_page.goto(alfred.url)
    expect(pw_page.locator(".carte-cas")).to_have_count(2)
    phrases = {
        "Alfred, lance la démo": {"type": "lancer", "code": "CT-00", "demo": True},
        "lance le cas numéro deux": {"type": "lancer", "code": "CT-02"},
        "C T 3": {"type": "lancer", "code": "CT-03"},
        "test 12": {"type": "lancer", "code": "CT-12"},
        "Arrête !": {"type": "arreter"},
        "ouvre le rapport": {"type": "ouvrir", "fichier": "rapport"},
        "montre-moi le test Robot": {"type": "ouvrir", "fichier": "robot"},
        "bonsoir": {"type": "saluer"},
        "fais-moi un café": {"type": "inconnu"},
    }
    for phrase, attendu in phrases.items():
        assert pw_page.evaluate("(t) => Alfred.comprendre(t)", phrase) == attendu, phrase


def test_interface_explique_un_moteur_indisponible(alfred, pw_page, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    pw_page.goto(alfred.url)
    pw_page.click("text=Autre cas")
    expect(pw_page.locator("#bouton-lancer")).to_be_disabled()
    expect(pw_page.locator('input[name="moteur"][value="scripted"]')).to_be_disabled()
    expect(pw_page.locator("#indice")).to_contain_text("GEMINI_API_KEY")


def test_interface_site_a_tester(alfred, pw_page, demo_url):
    pw_page.goto(alfred.url)
    champ = pw_page.locator("#url-mission")
    expect(champ).to_have_value(f"{demo_url}/index.html")
    expect(pw_page.locator("#url-retablir")).to_be_hidden()

    champ.fill("pas une adresse")
    expect(pw_page.locator("#bouton-lancer")).to_be_disabled()
    expect(pw_page.locator("#indice")).to_contain_text("http:// ou https://")

    autre = f"{demo_url}/index.html?env=recette"
    champ.fill(autre)
    expect(pw_page.locator("#url-note")).to_contain_text("Adresse modifiée pour cette mission")
    expect(pw_page.locator("#url-retablir")).to_be_visible()
    pw_page.fill("#option-pause", "0")
    pw_page.click("#bouton-lancer")
    expect(pw_page.locator(".verdict-mot")).to_have_text("Réussi", timeout=30_000)
    expect(pw_page.locator(".etape-libelle").first).to_have_text(f"Ouverture de {autre}")

    pw_page.click("#url-retablir")
    expect(champ).to_have_value(f"{demo_url}/index.html")
    expect(pw_page.locator("#url-retablir")).to_be_hidden()
