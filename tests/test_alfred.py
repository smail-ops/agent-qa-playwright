"""Alfred : narration, serveur local (API, flux en direct, sécurité) et interface web."""

import http.client
import json
import shutil
import threading
from datetime import datetime
from pathlib import Path

import pytest
from playwright.sync_api import expect

from agent.actions import Action
from agent.browser import Element
from agent.providers import ScriptedProvider
from alfred import maitre, narration
from alfred import server as alfred_server
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
    # Le texte du <label> associé l'emporte : c'est le nom que lit l'utilisateur
    q = narration.q
    assert narration.element_name(element(placeholder="ex. Jean", label="Prénom")) == f"le champ {q('Prénom')}"
    assert narration.element_name(element("input", "checkbox", label="Conditions de vente")) == f"la case {q('Conditions de vente')}"
    assert narration.element_name(element("input", "radio", label="Point relais")) == f"l'option {q('Point relais')}"


def test_verifications_racontees():
    """Ordre, nombre, valeur et état : libellé du journal et phrase dite, accordés au féminin si besoin."""
    q = narration.q
    ordre = Action("check_order", {"texts": ["Lampe", "Sac"]})
    assert narration.action_label(ordre) == f"Vérification de l'ordre : {q('Lampe')}, puis {q('Sac')}"
    assert narration.action_speech(ordre) == f"Je vérifie que {q('Lampe')} apparaît avant {q('Sac')}."
    compte = Action("check_count", {"text": "Retirer", "count": 1})
    assert narration.action_speech(compte) == f"Je vérifie que {q('Retirer')} apparaît une fois."
    assert narration.action_label(Action("check_count", {"text": "Erreur", "count": 0})) == f"Vérification : {q('Erreur')} n'apparaît pas"
    case = element("input", "checkbox", label="CGV")
    etat = Action("check_state", {"ref": 1, "state": "checked"})
    assert narration.action_speech(etat, case) == f"Je vérifie que la case {q('CGV')} est cochée."
    bouton = element("button", text="Continuer")
    grise = Action("check_state", {"ref": 1, "state": "disabled"})
    assert narration.action_label(grise, bouton) == f"Vérification : le bouton {q('Continuer')} est grisé"
    liste = element("select", name="tri")
    assert narration.action_label(Action("check_value", {"ref": 1, "value": "Prix"}), liste) == f"Vérification : la liste {q('tri')} indique {q('Prix')}"
    secret = element("input", "password", placeholder="Password")
    valeur = Action("check_value", {"ref": 1, "value": "secret_sauce"})
    assert "secret_sauce" not in narration.action_label(valeur, secret) + narration.action_speech(valeur, secret)


def test_le_mot_de_passe_reste_secret():
    champ = element("input", "password", placeholder="Password")
    action = Action("fill", {"ref": 1, "text": "secret_sauce"})
    assert "secret_sauce" not in narration.action_label(action, champ)
    assert narration.action_speech(action, champ) == "Je saisis le mot de passe."


def test_libelles_des_verifications():
    assert narration.action_label(Action("check_text", {"text": "Products"})) == "Vérification : « Products » s'affiche"
    negative = Action("check_url", {"contains": "inventory", "absent": True})
    assert narration.action_label(negative) == "Vérification : l'URL ne contient pas « inventory »"


def test_attente_fenetre_et_nouveaux_elements_racontes():
    """Attente, réponse prévue à une fenêtre JavaScript, motif, zone de texte riche, liste « maison » et icône."""
    from agent.browser import ActionResult
    from agent.runner import Step

    q = narration.q
    attente = Action("wait", {"seconds": 15, "text": "Data loaded"})
    assert narration.action_label(attente) == f"Attente de {q('Data loaded')}, 15 s au plus"
    assert narration.action_speech(attente) == f"J'attends que {q('Data loaded')} s'affiche."
    assert narration.action_label(Action("wait", {"seconds": 3})) == "Pause de 3 s"
    refus = Action("dialog", {"accept": False})
    assert narration.action_label(refus) == "Réponse prévue à la prochaine fenêtre : Annuler"
    assert narration.action_speech(refus) == "À la prochaine fenêtre, je répondrai Annuler."
    motif = Action("check_value", {"ref": 1, "value": "^PROMO-", "regex": True})
    assert narration.action_label(motif, element(aria="Code promo")) == f"Vérification : le champ {q('Code promo')} suit le motif {q('^PROMO-')}"
    assert narration.element_name(element("body", text="Votre note", editable=True, aria="Note")) == f"la zone de texte {q('Note')}"
    assert narration.element_name(element("div", text="-- Select --", field=True, label="User Role")) == f"le champ {q('User Role')}"
    assert narration.element_name(element("div", text="ESS", role="option")) == f"l'option {q('ESS')}"
    assert narration.element_name(element("button", text="⚙", aria="Générer un code")) == f"le bouton {q('Générer un code')}"
    message = "Confirmer l'envoi ?"
    fenetre = {"type": "confirm", "message": message, "accepted": True, "text": None}
    clic = Step(2, Action("click", {"ref": 1}), ActionResult(True, "Clic", dialog=fenetre))
    assert narration.step_speech(clic) == f"Une fenêtre affichait {q(message)}. J'ai répondu OK."


# --- Serveur -------------------------------------------------------------------------


@pytest.fixture
def alfred(tmp_path, demo_url, monkeypatch):
    """Alfred avec un dossier de cas de test : la démo pointe vers le site de démo des tests."""
    monkeypatch.delenv("ALFRED_MAITRE", raising=False)  # chaque test commence sans maître enregistré
    cases = tmp_path / "cas_de_test"
    (cases / "demo").mkdir(parents=True)
    text = (DEMO_DIR / "CT-00_demo_locale.md").read_text(encoding="utf-8")
    (cases / "demo" / "CT-00_demo_locale.md").write_text(text.replace("http://localhost:8000", demo_url), encoding="utf-8")
    shutil.copy(DEMO_DIR / "scenario_CT-00.json", cases / "demo")
    (cases / "CT-01_autre.md").write_text(f"# CT-01 Autre cas\nURL : {demo_url}/index.html\n", encoding="utf-8")
    # CT-05 : la démo, avec une erreur de quota simulée au premier appel du modèle
    (cases / "demo" / "CT-05_quota.md").write_text(f"# CT-05 Quota atteint\nURL : {demo_url}/index.html\n", encoding="utf-8")
    (cases / "demo" / "scenario_CT-05.json").write_text(json.dumps([
        {"error": "rate_limit", "retry_after": 2},
        {"action": "check_url", "args": {"contains": "index"}},
        {"action": "finish", "args": {"status": "PASS", "summary": "Reprise après la pause"}},
    ]), encoding="utf-8")
    server = AlfredServer(("127.0.0.1", 0), cases, tmp_path / "rapports", env_path=tmp_path / ".env")
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
    assert [(c["code"], c["demo"]) for c in data["cases"]] == [("CT-00", True), ("CT-01", False), ("CT-05", True)]
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
    status, feature = request(alfred, "GET", done["files"]["gherkin"])
    assert status == 200 and feature.decode().startswith("# language: fr\n")
    status, bdd = request(alfred, "GET", done["files"]["robot_bdd"])
    assert status == 200 and "Alors la page affiche" in bdd.decode()
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
    assert codes == ["CT-00", "CT-01", "CT-05", "CT-12"]


# --- Interface -----------------------------------------------------------------------


def test_interface_mission_de_demo(alfred, pw_page):
    errors = []
    pw_page.on("pageerror", lambda exc: errors.append(str(exc)))
    pw_page.goto(alfred.url)

    expect(pw_page.locator(".carte-cas")).to_have_count(3)
    expect(pw_page.locator('.carte-cas[aria-pressed="true"]')).to_contain_text("CT-00")
    expect(pw_page.locator('input[name="moteur"][value="scripted"]')).to_be_checked()
    expect(pw_page.locator("#voix")).to_contain_text("Alfred, votre assistant de test")

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
    expect(pw_page.locator(".carte-cas")).to_have_count(3)
    phrases = {
        "Alfred, lance la démo": {"type": "lancer", "code": "CT-00", "demo": True},
        "lance le cas numéro deux": {"type": "lancer", "code": "CT-02"},
        "C T 3": {"type": "lancer", "code": "CT-03"},
        "test 12": {"type": "lancer", "code": "CT-12"},
        "Arrête !": {"type": "arreter"},
        "ouvre le rapport": {"type": "ouvrir", "fichier": "rapport"},
        "montre-moi le test Robot": {"type": "ouvrir", "fichier": "robot"},
        "ouvre le scénario Gherkin": {"type": "ouvrir", "fichier": "gherkin"},
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



# --- Reconnaissance du maître ----------------------------------------------------------


def test_salutation():
    soir, matin = datetime(2026, 10, 17, 20, 30), datetime(2026, 10, 17, 9, 0)
    assert maitre.salutation("Smail", soir) == "Bonsoir Smail. Ici Alfred, votre assistant de test. Quel cas de test dois-je exécuter ?"
    assert maitre.salutation(None, matin) == "Bonjour. Je suis Alfred, votre assistant de test. Comment dois-je vous appeler ?"


def test_enregistrer_le_maitre(tmp_path, monkeypatch):
    """Le prénom va dans le .env de cet ordinateur, sans toucher à la clé d'API."""
    monkeypatch.delenv("ALFRED_MAITRE", raising=False)
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=cle-de-test\nALFRED_MAITRE=Ancien\n", encoding="utf-8")
    assert maitre.enregistrer("  Jean-Pierre  ", env) == "Jean-Pierre"
    assert env.read_text(encoding="utf-8") == "GEMINI_API_KEY=cle-de-test\nALFRED_MAITRE=Jean-Pierre\n"
    assert maitre.nom_du_maitre() == "Jean-Pierre"


@pytest.mark.parametrize("nom", ["", "42", "Smail\nGEMINI_API_KEY=pirate", "<script>", "x" * 41, "Smail="])
def test_prenom_refuse(tmp_path, nom):
    with pytest.raises(ValueError, match="prénom"):
        maitre.enregistrer(nom, tmp_path / ".env")
    assert not (tmp_path / ".env").exists()


def test_choix_de_la_voix_du_mac():
    sortie = (
        "Amélie              fr_CA    # Bonjour, je m’appelle Amélie.\n"
        "Daniel              en_GB    # Hello! My name is Daniel.\n"
        "Jacques             fr_FR    # Bonjour, je m’appelle Jacques.\n"
        "Thomas (Enhanced)   fr_FR    # Bonjour, je m’appelle Thomas.\n"
    )
    assert maitre.choisir_voix(sortie) == "Thomas (Enhanced)"
    assert maitre.choisir_voix(sortie.replace("Thomas (Enhanced)", "Audrey           ")) == "Jacques"
    assert maitre.choisir_voix("Daniel              en_GB    # Hello!\n") is None


def test_salutation_silencieuse(monkeypatch):
    monkeypatch.setenv("ALFRED_VOIX", "off")
    assert maitre.saluer_a_voix_haute("Bonjour") == "voix coupée (ALFRED_VOIX=off)"


def test_api_du_maitre(alfred):
    status, data = request(alfred, "GET", "/api/status")
    assert data["maitre"]["nom"] is None and data["salutation"].endswith("Comment dois-je vous appeler ?")
    assert data["maitre"]["ordinateur"]
    status, data = request(alfred, "POST", "/api/maitre", {"nom": "Smail"})
    assert status == 200 and data["nom"] == "Smail" and "Smail. Ici Alfred" in data["salutation"]
    assert "ALFRED_MAITRE=Smail" in alfred.env_path.read_text(encoding="utf-8")
    assert request(alfred, "GET", "/api/status")[1]["maitre"]["nom"] == "Smail"
    assert request(alfred, "POST", "/api/maitre", {"nom": "Smail\nX=1"})[0] == 400


def test_interface_premier_lancement(alfred, pw_page):
    """Sans maître enregistré, Alfred demande le prénom, puis le retient pour cet ordinateur."""
    pw_page.goto(alfred.url)
    expect(pw_page.locator("#voix")).to_contain_text("Comment dois-je vous appeler ?")
    expect(pw_page.locator("#presentation")).to_be_visible()
    pw_page.fill("#presentation-nom", "Smail")
    pw_page.click("#presentation-enregistrer")
    expect(pw_page.locator("#marque-role")).to_have_text("au service de Smail")
    expect(pw_page.locator("#voix")).to_contain_text("Très bien, Smail. Je m'en souviendrai sur cet ordinateur.")
    expect(pw_page.locator("#presentation")).to_be_hidden()

    pw_page.reload()  # au prochain lancement, Alfred le reconnaît
    expect(pw_page.locator("#voix")).to_contain_text("Smail. Ici Alfred, votre assistant de test.")
    expect(pw_page.locator("#presentation")).to_be_hidden()



# --- Quota de l'API ----------------------------------------------------------------------


def test_pause_de_quota_annoncee(alfred):
    data = request(alfred, "POST", "/api/missions", {"case": "demo/CT-05_quota.md", "provider": "scripted"})[1]
    events = read_events(alfred, data["id"])
    wait = next(e for e in events if e["type"] == "wait")
    assert wait["seconds"] == 2 and wait["attempt"] == 1
    assert wait["say"] == "Le quota du modèle est atteint pour cette minute. Je patiente 2 secondes, puis je reprends."
    assert events[-1]["status"] == "PASS"


def test_interface_compte_a_rebours_du_quota(alfred, pw_page):
    pw_page.goto(alfred.url)
    pw_page.click("text=Quota atteint")
    pw_page.fill("#option-pause", "0")
    pw_page.click("#bouton-lancer")
    expect(pw_page.locator("#cadran-mot")).to_have_text("PATIENTE")
    expect(pw_page.locator("#indice")).to_contain_text("Reprise dans")
    expect(pw_page.locator(".verdict-mot")).to_have_text("Réussi", timeout=30_000)
    expect(pw_page.locator("#indice")).not_to_contain_text("Reprise")


# --- Mémoire des parcours --------------------------------------------------------------

class FauxGemini(ScriptedProvider):
    """Un « vrai » modèle pour Alfred (la mémoire ne sert pas à la démo sans IA), qui rejoue un scénario."""

    name = "gemini"
    model = "faux modèle"


PARCOURS_CT01 = [
    {"action": "check_url", "args": {"contains": "index"}},
    {"action": "finish", "args": {"status": "PASS", "summary": "Page ouverte"}},
]


def test_memoire_des_parcours(alfred, monkeypatch):
    """Première mission avec le modèle, deuxième rejouée depuis la mémoire, puis mémoire ignorée sur demande."""
    monkeypatch.setattr(alfred_server, "make_provider", lambda name: FauxGemini(PARCOURS_CT01))
    corps = {"case": "CT-01_autre.md", "provider": "gemini"}

    premiere = read_events(alfred, request(alfred, "POST", "/api/missions", corps)[1]["id"])
    assert premiere[-1]["status"] == "PASS" and premiere[-1]["model_calls"] == 2
    assert premiere[-1]["memory"] == "Parcours enregistré : la prochaine exécution se fera sans IA."
    cas = next(c for c in request(alfred, "GET", "/api/cases")[1]["cases"] if c["code"] == "CT-01")
    assert cas["memory"]["steps"] == 1

    deuxieme = read_events(alfred, request(alfred, "POST", "/api/missions", corps)[1]["id"])
    annonce = next(e for e in deuxieme if e["type"] == "memory")
    assert annonce["steps"] == 1 and "sans faire appel à l'IA" in annonce["say"]
    assert [e["memory"] for e in deuxieme if e["type"] == "step"] == [False, True]
    fin = deuxieme[-1]
    assert fin["status"] == "PASS" and fin["model_calls"] == 0
    assert fin["say"] == "Mission accomplie, sans aucun appel à l'IA : j'ai rejoué le parcours en mémoire."

    troisieme = read_events(alfred, request(alfred, "POST", "/api/missions", {**corps, "memoire": False})[1]["id"])
    assert troisieme[-1]["model_calls"] == 2 and not any(e["type"] == "memory" for e in troisieme)


def test_interface_memoire(alfred, pw_page, monkeypatch):
    """Le cas affiche son parcours en mémoire ; le journal signale les étapes rejouées."""
    monkeypatch.setattr(alfred_server, "make_provider", lambda name: FauxGemini(PARCOURS_CT01))
    monkeypatch.setenv("GEMINI_API_KEY", "cle-de-test")
    read_events(alfred, request(alfred, "POST", "/api/missions", {"case": "CT-01_autre.md", "provider": "gemini"})[1]["id"])

    pw_page.goto(alfred.url)
    pw_page.click("text=Autre cas")
    expect(pw_page.locator(".detail-memoire")).to_contain_text("Parcours en mémoire depuis le")
    pw_page.check('input[name="moteur"][value="gemini"]')
    pw_page.fill("#option-pause", "0")
    pw_page.click("#bouton-lancer")
    expect(pw_page.locator(".verdict-mot")).to_have_text("Réussi", timeout=20_000)
    expect(pw_page.locator(".note-journal").first).to_contain_text("rejoué depuis la mémoire, sans IA")
    expect(pw_page.locator(".etape-memoire")).to_have_count(1)
    expect(pw_page.locator(".verdict-chiffres")).to_contain_text("0 appel au modèle")


def test_fiche_d_anomalie_annoncee(alfred, monkeypatch):
    """Test en échec avec un modèle : Alfred annonce l'analyse, puis le diagnostic, et sert la fiche."""
    diagnostic = {"nature": "bug", "gravite": "majeure", "titre": "Le texte attendu manque",
                  "explication": "La page ne contient pas le texte.", "suggestion": "Signaler le bug."}
    scenario = [
        {"action": "check_text", "args": {"text": "Texte introuvable"}},
        {"action": "finish", "args": {"status": "FAIL", "summary": "Le texte attendu manque."}},
        {"raw": json.dumps(diagnostic, ensure_ascii=False)},  # réponse du modèle à la demande de diagnostic
    ]
    monkeypatch.setattr(alfred_server, "make_provider", lambda name: FauxGemini(scenario))
    events = read_events(alfred, request(alfred, "POST", "/api/missions", {"case": "CT-01_autre.md", "provider": "gemini"})[1]["id"])
    analyse = next(e for e in events if e["type"] == "analysis")
    assert analyse["say"] == "J'analyse l'échec pour rédiger la fiche d'anomalie."
    fin = events[-1]
    assert fin["status"] == "FAIL" and fin["model_calls"] == 3
    assert fin["diagnosis"] == {"nature": "Bug de l'application", "gravite": "Majeure", "titre": "Le texte attendu manque"}
    assert fin["say"] == ("Le test a échoué. D'après mon analyse, il s'agit probablement d'un bug de l'application. "
                          "La fiche d'anomalie est prête.")
    status, fiche = request(alfred, "GET", fin["files"]["anomalie"])
    assert status == 200 and fiche.decode().startswith("# Fiche d'anomalie : Le texte attendu manque")


# --- Propositions : user stories et cas de test ---------------------------------------------


@pytest.fixture
def alfred_propositions(alfred):
    """Alfred avec les réponses enregistrées de la démo sans IA des propositions."""
    shutil.copy(DEMO_DIR / "propositions_demo.json", alfred.cases_dir / "demo")
    return alfred


def test_propositions_de_la_demo(alfred_propositions, demo_url):
    """Analyse du site de démo, user stories, cas de test, enregistrement, puis lancement d'un scénario."""
    alfred, url = alfred_propositions, f"{demo_url}/index.html"
    corps = {"url": url, "provider": "scripted", "identifiant": "standard_user", "mot_de_passe": "secret_sauce"}
    status, data = request(alfred, "POST", "/api/propositions/stories", corps)
    assert status == 200, data
    assert data["analyse"]["logged_in"] and len(data["analyse"]["pages"]) == 3
    assert [s["priorite"] for s in data["stories"]] == ["haute", "haute", "moyenne", "moyenne"]
    assert data["say"].startswith("J'ai analysé 3 pages et je vous propose 4 user stories.")
    assert "localhost:8000" not in json.dumps(data)  # l'adresse réelle du site de démo remplace celle des réponses

    status, data_cas = request(alfred, "POST", "/api/propositions/cas", {"url": url, "provider": "scripted",
                                                                        "story": data["stories"][0]})
    assert status == 200 and len(data_cas["cas"]) == 3
    titre = narration.q("Connexion à la boutique")  # guillemets avec espaces insécables
    assert data_cas["say"] == f"Voici 3 cas de test pour {titre}. Gardez ceux qui vous conviennent."

    status, saved = request(alfred, "POST", "/api/propositions/enregistrer", {"url": url, "stories": data["stories"][:1],
                                                                             "cas": data_cas["cas"][:1]})
    assert status == 201
    assert saved["files"] == ["US-01_connexion_a_la_boutique.feature", "CT-06_connexion_avec_un_utilisateur_valide.md"]  # après CT-05, le plus grand numéro
    assert saved["first"] == "US-01_connexion_a_la_boutique.feature#1"
    cases = request(alfred, "GET", "/api/cases")[1]["cases"]
    scenarios = [c for c in cases if c["gherkin"]]
    assert [c["id"] for c in scenarios] == ["US-01_connexion_a_la_boutique.feature#1", "US-01_connexion_a_la_boutique.feature#2"]
    assert scenarios[1]["title"] == "US-01 Connexion à la boutique : Mot de passe incorrect"

    # Un scénario Gherkin se lance comme un cas de test (ici avec le scénario enregistré de la démo)
    (alfred.cases_dir / "scenario_US-01.json").write_text(json.dumps([
        {"action": "fill", "target": "#user-name", "args": {"text": "standard_user"}},
        {"action": "fill", "target": "#password", "args": {"text": "mauvais"}},
        {"action": "click", "target": '[data-test="login-button"]'},
        {"action": "check_text", "args": {"text": "Identifiant ou mot de passe incorrect"}},
        {"action": "finish", "args": {"status": "PASS", "summary": "Connexion refusée"}},
    ]), encoding="utf-8")
    status, mission = request(alfred, "POST", "/api/missions", {"case": scenarios[1]["id"], "provider": "scripted"})
    assert status == 201, mission
    assert read_events(alfred, mission["id"])[-1]["status"] == "PASS"


def test_le_mot_de_passe_de_test_ne_ressort_jamais(alfred_propositions, demo_url):
    corps = {"url": f"{demo_url}/index.html", "provider": "scripted", "identifiant": "standard_user", "mot_de_passe": "Zx9-secret"}
    status, data = request(alfred_propositions, "POST", "/api/propositions/stories", corps)
    assert status == 200 and not data["analyse"]["logged_in"]  # mauvais mot de passe pour le site de démo
    assert "Zx9-secret" not in json.dumps(data) and "Zx9-secret" not in alfred_propositions.last_analysis.to_prompt()


@pytest.mark.parametrize("chemin, corps, code, message", [
    ("/api/propositions/stories", {"url": "https://www.saucedemo.com/", "provider": "scripted"}, 400, "site de démo local"),
    ("/api/propositions/stories", {"url": "saucedemo.com", "provider": "scripted"}, 400, "http:// ou https://"),
    ("/api/propositions/stories", {"url": "https://www.saucedemo.com/", "provider": "inconnu"}, 400, "Moteur inconnu"),
    ("/api/propositions/cas", {"url": "http://localhost:8000/", "provider": "scripted", "texte": "court"}, 400, "Écrivez la user story"),
    ("/api/propositions/enregistrer", {"url": "http://localhost:8000/", "stories": [{"titre": "Sans critère"}]}, 400, "critère"),
    ("/api/propositions/enregistrer", {"url": "http://localhost:8000/"}, 400, "Cochez au moins"),
])
def test_propositions_refusees(alfred_propositions, chemin, corps, code, message):
    status, data = request(alfred_propositions, "POST", chemin, corps)
    assert status == code and message in data["error"]


def test_reponse_du_modele_inutilisable(alfred_propositions, monkeypatch):
    monkeypatch.setattr(alfred_server, "make_provider", lambda name: ScriptedProvider([{"raw": "Désolé, je ne peux pas."}]))
    status, data = request(alfred_propositions, "POST", "/api/propositions/cas",
                           {"url": "https://www.saucedemo.com/", "provider": "gemini", "texte": "En tant que client, je veux payer."})
    assert status == 502 and "réponse du modèle est inutilisable" in data["error"]


def test_interface_propositions(alfred_propositions, pw_page, demo_url):
    """Analyse du site de démo depuis la page, choix des user stories, enregistrement en Gherkin."""
    errors = []
    pw_page.on("pageerror", lambda exc: errors.append(str(exc)))
    pw_page.goto(alfred_propositions.url)
    expect(pw_page.locator(".carte-cas")).to_have_count(3)
    pw_page.click("#bouton-proposer")
    expect(pw_page.locator("#fenetre-propositions")).to_be_visible()
    pw_page.fill("#prop-url", f"{demo_url}/index.html")
    pw_page.check('input[name="prop-moteur"][value="scripted"]')
    pw_page.fill("#prop-identifiant", "standard_user")
    pw_page.fill("#prop-mot-de-passe", "secret_sauce")
    pw_page.click("#prop-lancer")
    expect(pw_page.locator(".proposition")).to_have_count(4, timeout=30_000)
    expect(pw_page.locator(".prop-bilan")).to_contain_text("3 pages analysées")
    assert pw_page.input_value("#prop-mot-de-passe") == ""  # effacé après l'analyse

    # Les cas de test d'une user story, puis retour aux user stories
    pw_page.locator(".proposition").nth(2).locator("text=Proposer des cas de test").click()
    expect(pw_page.locator(".prop-bilan")).to_have_text("Cas de test proposés pour « Tri des produits par prix » :")
    expect(pw_page.locator(".proposition")).to_have_count(2)
    pw_page.click("text=Revenir aux user stories")

    for index in (1, 2, 3):  # on ne garde que la première user story
        pw_page.locator(".proposition").nth(index).locator("input[type=checkbox]").uncheck()
    pw_page.click("text=Enregistrer la sélection en Gherkin")
    expect(pw_page.locator("#fenetre-propositions")).to_be_hidden()
    expect(pw_page.locator(".carte-cas")).to_have_count(5)
    expect(pw_page.locator('.carte-cas[aria-pressed="true"]')).to_contain_text("Scénario Gherkin")
    expect(pw_page.locator(".detail .gherkin-ligne").first).to_contain_text("Étant donné que je suis sur la page de connexion")
    assert errors == []


# --- Conversation libre -------------------------------------------------------------------


def conversation(reponse, action):
    """Le faux modèle répond à une phrase du testeur."""
    return FauxGemini([{"raw": json.dumps({"reponse": reponse, "action": action}, ensure_ascii=False)}])


def test_conversation_libre(alfred, monkeypatch):
    """La phrase part au modèle avec le contexte (cas, résultats) ; l'action revient contrôlée."""
    read_events(alfred, request(alfred, "POST", "/api/missions", {"case": "demo/CT-00_demo_locale.md", "provider": "scripted"})[1]["id"])
    monkeypatch.setenv("GEMINI_API_KEY", "cle-de-test")
    sent = {}
    faux = conversation("Très bien, je relance l'autre cas.", {"type": "lancer", "cas": "CT-01"})
    original = faux.complete
    monkeypatch.setattr(faux, "complete", lambda system, user, images=(), max_tokens=None: sent.update(user=user) or original(system, user))
    monkeypatch.setattr(alfred_server, "make_provider", lambda name: faux)

    status, data = request(alfred, "POST", "/api/conversation", {"texte": "relance l'autre cas sur le même site",
                                                                  "provider": "scripted", "historique": []})
    assert status == 200, data
    assert data == {"reponse": "Très bien, je relance l'autre cas.",
                    "action": {"type": "lancer", "cas": "CT-01_autre.md", "code": "CT-01"}}
    assert "- CT-01 | Autre cas |" in sent["user"]
    assert "CT-00 Démo locale : connexion et panier | PASS" in sent["user"]  # résultat de la mission précédente (resultat.json)


def test_conversation_sans_cle(alfred, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    status, data = request(alfred, "POST", "/api/conversation", {"texte": "quels tests ont échoué aujourd'hui ?"})
    assert status == 503 and "clé Gemini ou Claude" in data["error"] and "lance la démo" in data["error"]
    assert request(alfred, "POST", "/api/conversation", {"texte": "  "})[0] == 400


def test_interface_conversation_et_cas_dicte(alfred, pw_page, monkeypatch, demo_url):
    """Un cas dicté à Alfred s'affiche, puis « vas-y » le lance."""
    monkeypatch.setenv("GEMINI_API_KEY", "cle-de-test")
    dicte = conversation("Voici le cas. Dites vas-y pour le lancer.", {
        "type": "rediger", "titre": "Page de connexion", "url": f"{demo_url}/index.html",
        "etapes": ["Ouvrir la page de connexion"], "resultats": ["Le titre « Boutique Démo » s'affiche"]})
    mission = FauxGemini([{"action": "check_text", "args": {"text": "Boutique Démo"}},
                          {"action": "finish", "args": {"status": "PASS", "summary": "Titre affiché"}}])
    fournisseurs = iter([dicte, mission])
    monkeypatch.setattr(alfred_server, "make_provider", lambda name: next(fournisseurs))

    errors = []
    pw_page.on("pageerror", lambda exc: errors.append(str(exc)))
    pw_page.goto(alfred.url)
    expect(pw_page.locator(".carte-cas")).to_have_count(3)
    pw_page.fill("#dialogue-texte", "rédige un cas qui vérifie le titre de la page de connexion de la démo")
    pw_page.press("#dialogue-texte", "Enter")
    expect(pw_page.locator("#vous")).to_have_text("Vous : « rédige un cas qui vérifie le titre de la page de connexion de la démo »")
    expect(pw_page.locator("#editeur-texte")).to_have_value(
        f"# CT-06 Page de connexion\nURL : {demo_url}/index.html\n\n## Étapes\n1. Ouvrir la page de connexion\n\n"
        "## Résultats attendus\n- Le titre « Boutique Démo » s'affiche\n", timeout=15_000)
    expect(pw_page.locator("#voix")).to_have_text("Voici le cas. Dites vas-y pour le lancer.")

    pw_page.fill("#option-pause", "0")
    pw_page.fill("#dialogue-texte", "vas-y")
    pw_page.press("#dialogue-texte", "Enter")
    expect(pw_page.locator(".verdict-mot")).to_have_text("Réussi", timeout=30_000)
    assert pw_page.evaluate("Alfred.etat.historique.length") == 2
    assert errors == []


def test_commandes_simples_et_phrases_libres(alfred, pw_page):
    """Les questions et les phrases longues partent au modèle ; « vas-y » lance le cas affiché."""
    pw_page.goto(alfred.url)
    expect(pw_page.locator(".carte-cas")).to_have_count(3)
    phrases = {
        "pourquoi le dernier test a échoué ?": {"type": "inconnu"},
        "quels tests ont échoué aujourd'hui": {"type": "inconnu"},
        "lance le cas 3 sur le site de recette, s'il te plaît": {"type": "inconnu"},
        "vas-y": {"type": "lancer_courant"},
        "Alfred, c'est parti !": {"type": "lancer_courant"},
        "ouvre le rapport du test 3": {"type": "ouvrir", "fichier": "rapport"},
        "bonjour Alfred": {"type": "saluer"},
    }
    for phrase, attendu in phrases.items():
        assert pw_page.evaluate("(t) => Alfred.comprendre(t)", phrase) == attendu, phrase


def test_fichiers_des_executions_passees(alfred):
    """Alfred ouvre le rapport d'une exécution passée, et rien d'autre."""
    dossier = alfred.missions.reports_dir / "20261017-234105_ct_14"
    dossier.mkdir(parents=True)
    (dossier / "anomalie.md").write_text("# Fiche d'anomalie : test\n", encoding="utf-8")
    (dossier / "resultat.json").write_text("{}", encoding="utf-8")
    status, data = request(alfred, "GET", "/rapports/20261017-234105_ct_14/anomalie.md")
    assert status == 200 and data.decode().startswith("# Fiche d'anomalie")
    assert request(alfred, "GET", "/rapports/20261017-234105_ct_14/resultat.json")[0] == 404  # type de fichier non servi
    assert request(alfred, "GET", "/rapports/20261017-234105_ct_14/..%2F..%2F.env")[0] == 404
    assert request(alfred, "GET", "/rapports/../.env")[0] == 404
