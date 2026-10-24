"""Bandeaux de cookies : acceptés par le programme, sans IA, puis par les tests générés.

La démo CT-92 imite le bandeau de Google (Funding Choices), celui d'automationexercise.com
vu depuis l'Europe : il couvre la page dès l'ouverture et bloque les clics.
"""

import json
import time
from pathlib import Path

import pytest
from test_atelier_genere import run_on_new_page
from test_robot import run_robot

from agent import cookies
from agent.anomalie import reproduction_step
from agent.codegen import generate_script
from agent.codegen_gherkin import generate_bdd_robot, generate_feature
from agent.codegen_robot import generate_robot
from agent.memoire import Memory
from agent.prompts import describe_step
from agent.providers import ScriptedProvider
from agent.report import build_report
from agent.runner import Agent
from agent.testcase import parse_test_case
from alfred import narration

DEMO = Path(__file__).resolve().parent.parent / "cas_de_test" / "demo"
SCENARIO = json.loads((DEMO / "scenario_CT-92.json").read_text(encoding="utf-8"))


def ct92(demo_url, text=None):
    text = text or (DEMO / "CT-92_demo_inscription_lettre.md").read_text(encoding="utf-8")
    return parse_test_case(text.replace("http://localhost:8000", demo_url))


@pytest.fixture(scope="module")
def run92(pw_browser, demo_url, tmp_path_factory):
    folder = tmp_path_factory.mktemp("ct92") / "rapport"
    result = run_on_new_page(pw_browser, lambda page: Agent(ScriptedProvider(SCENARIO)).run(page, ct92(demo_url), folder))
    assert result.status == "PASS", result.summary
    return result


# --- Le bandeau, dans le rapport et pour Alfred ------------------------------------------


def test_bandeau_accepte_sans_ia(run92):
    """Le bandeau couvre la page : sans lui, la saisie de l'adresse échouerait. Le programme l'accepte avant."""
    cookie_step = run92.steps[1]
    assert cookie_step.action.name == "cookies" and cookie_step.source == "programme" and cookie_step.result.ok
    assert cookie_step.action.args == {"outil": "Google", "bouton": "Autoriser", "playwright": "page.locator('button.fc-cta-consent')",
                                       "robot": "css:button.fc-cta-consent"}
    assert all(step.result.ok for step in run92.steps) and len(run92.checks) == 2
    report = build_report(run92)
    assert ("| 2 | cookies | « Autoriser » (Google) | OK | Sans IA : Bandeau de cookies accepté par le programme, "
            "comme le ferait un testeur |") in report
    assert describe_step(cookie_step).startswith('2. cookies "Autoriser" -> OK : Bandeau de cookies (Google) accepté')
    assert reproduction_step(cookie_step) == "Accepter le bandeau de cookies (« Autoriser »)"
    assert narration.action_label(cookie_step.action) == f"Bandeau de cookies accepté : {narration.q('Autoriser')}"
    assert narration.action_speech(cookie_step.action) == "J'accepte le bandeau de cookies."


# --- Les tests générés acceptent le bandeau s'il s'affiche ---------------------------------


def test_script_pytest(run92, pw_browser):
    code = generate_script(run92)
    assert """    page.goto('{url}')
    # Bandeau de cookies (Google) : accepté s'il s'affiche, comme pendant l'exécution de l'agent
    bandeau_cookies = page.locator('button.fc-cta-consent').first
    page.add_locator_handler(bandeau_cookies, lambda: bandeau_cookies.click())
    page.get_by_role('textbox', name='Adresse e-mail', exact=True).fill('lecteur@exemple.fr')
""".format(url=run92.case.url) in code
    namespace = {}
    exec(compile(code, "test_genere.py", "exec"), namespace)
    run_on_new_page(pw_browser, namespace[f"test_{run92.case.slug}"])  # un navigateur neuf : le bandeau revient


def test_test_robot(run92, tmp_path):
    code = generate_robot(run92)
    assert ("    Ouvrir le navigateur    {url}\n    Accepter le bandeau de cookies    css:button.fc-cta-consent\n"
            "    Input Text    css:#email    lecteur@exemple.fr\n").format(url=run92.case.url) in code
    assert code.count("\nAccepter le bandeau de cookies\n") == 1
    result = run_robot(code, tmp_path)
    assert result.returncode == 0, result.stdout


def test_scenario_gherkin(run92, tmp_path):
    feature = generate_feature(run92)
    assert f"""    Étant donné que je suis sur la page "{run92.case.url}"
    Et j'accepte le bandeau de cookies
    Quand je saisis "lecteur@exemple.fr" dans le champ "Adresse e-mail"
""" in feature
    code = generate_bdd_robot(run92)
    assert "j'accepte le bandeau de cookies\n    Accepter le bandeau de cookies    css:button.fc-cta-consent\n" in code
    result = run_robot(code, tmp_path)
    assert result.returncode == 0, result.stdout


# --- Mémoire, et cas de test qui porte sur le bandeau ---------------------------------------


def test_parcours_memorise_sans_le_bandeau(pw_browser, demo_url, tmp_path):
    """La mémoire ne garde pas le bandeau : le programme l'accepte à chaque exécution, avant le rejeu."""
    memory = Memory(tmp_path / "memoire")
    first = run_on_new_page(pw_browser, lambda page: Agent(ScriptedProvider(SCENARIO), memory=memory).run(
        page, ct92(demo_url), tmp_path / "r1"))
    assert first.status == "PASS" and first.memory_saved
    saved = json.loads(memory.path(first.case).read_text(encoding="utf-8"))["steps"]
    assert "cookies" not in [step["action"] for step in saved]

    class Gemini(ScriptedProvider):  # la mémoire ne sert qu'aux vrais modèles
        name = "gemini"

    replay = run_on_new_page(pw_browser, lambda page: Agent(Gemini([]), memory=memory).run(
        page, ct92(demo_url), tmp_path / "r2"))
    assert replay.status == "PASS", replay.summary
    assert replay.model_calls == 0 and replay.memory_replayed == len(saved)
    assert replay.steps[1].action.name == "cookies" and replay.steps[1].source == "programme"


def test_cas_de_test_qui_porte_sur_le_bandeau(pw_page, demo_url, tmp_path):
    """« Le bandeau de cookies s'affiche » : le programme n'y touche pas, c'est l'objet du test."""
    text = (DEMO / "CT-92_demo_inscription_lettre.md").read_text(encoding="utf-8").split("## Étapes")[0]
    text += "## Étapes\n1. Ouvrir la page\n\n## Résultats attendus\n- Le bandeau de cookies s'affiche\n"
    scenario = [{"action": "check_text", "args": {"text": "Ce site demande votre consentement"}},
                {"action": "finish", "args": {"status": "PASS", "summary": "Bandeau affiché"}}]
    run = Agent(ScriptedProvider(scenario)).run(pw_page, ct92(demo_url, text), tmp_path / "rapport")
    assert run.status == "PASS" and "cookies" not in [step.action.name for step in run.steps if step.action]
    assert pw_page.locator(".fc-consent-root").count() == 1


def cas(titre, etapes, resultats):
    return f"# {titre}\nURL : https://exemple.fr/\n\n## Étapes\n{etapes}\n\n## Résultats attendus\n{resultats}\n"


@pytest.mark.parametrize("texte, laisse", [
    (cas("CT-50 Inscription", "1. Saisir « a@b.fr » dans le champ Adresse e-mail", "- « Merci » s'affiche"), False),
    # Accepter le bandeau, comme le demandent les cas d'Automation Exercise : c'est le travail du programme
    (cas("CT-51 Contact", "1. Si une fenêtre de consentement aux cookies s'affiche, cliquer sur « Consent »",
         "- « Success! » s'affiche"), False),
    (cas("CT-52 Boutique", "1. Ajouter des cookies au chocolat dans le panier", "- Le panier contient 2 cookies"), False),
    (cas("CT-53 Le bandeau de cookies s'affiche à la première visite", "1. Ouvrir la page", "- Le titre s'affiche"), True),
    (cas("CT-54 Refus", "1. Cliquer sur « Refuser les cookies »", "- La page reste utilisable"), True),
    (cas("CT-55 Choix", "1. Cliquer sur « Autoriser »", "- La fenêtre de consentement disparaît"), True),
    (cas("CT-56 Options", "1. Cliquer sur « Gérer les options »\n2. Valider", "- « Choix enregistré » s'affiche"), True),
    ("Fonctionnalité: Bandeau\n  Scénario: Première visite\n    Quand j'ouvre la page\n    Alors le bandeau de cookies s'affiche",
     True),
])
def test_cas_qui_porte_sur_les_cookies(texte, laisse):
    assert cookies.wanted(texte) is not laisse


def test_cas_de_la_campagne_terrain():
    """Les cas d'Automation Exercise demandent d'accepter le bandeau : le programme le fait pour eux."""
    dossier = Path(__file__).resolve().parent.parent / "cas_de_test" / "terrain"
    for fichier in sorted(dossier.rglob("CT-*.md")):
        assert cookies.wanted(fichier.read_text(encoding="utf-8")), fichier.name


# --- Reconnaître un bandeau, et seulement un bandeau ----------------------------------------

MAISON = """<html><body><h1>Boutique</h1><p>Bienvenue.</p>
<div id="bandeau" style="position: fixed; bottom: 0; left: 0; right: 0; background: #eee; padding: 12px">
  Nous utilisons des cookies pour améliorer votre expérience.
  <button onclick="document.title = 'personnaliser'">Personnaliser</button>
  <button onclick="this.parentElement.remove()"><span>✓</span> Tout accepter</button>
</div></body></html>"""

PAS_UN_BANDEAU = """<html><body>
<header style="position: fixed; top: 0">Livraison offerte <button onclick="document.title = 'clic 1'">Accepter</button></header>
<form><p>J'ai lu la politique de cookies et les conditions de vente.</p>
  <button type="button" onclick="document.title = 'clic 2'">Accepter</button></form>
</body></html>"""

USERCENTRICS = """<html><body><p>Page</p><div id="usercentrics-root"></div><script>
  const racine = document.getElementById('usercentrics-root').attachShadow({mode: 'open'});
  racine.innerHTML = '<div role="dialog">Cookies <button data-testid="uc-accept-all-button">Tout accepter</button></div>';
  racine.querySelector('button').onclick = () => racine.innerHTML = '';
</script></body></html>"""


def test_bandeau_fait_par_le_site(pw_page):
    pw_page.set_content(MAISON)
    banner = cookies.find(pw_page)
    assert (banner.outil, banner.texte, banner.nom) == ("", "✓ Tout accepter", "Bandeau de cookies du site")
    assert banner.playwright == "page.get_by_role('button', name='✓ Tout accepter', exact=True)"
    assert banner.robot == 'xpath://button[normalize-space(.)="✓ Tout accepter"]'
    assert eval(banner.playwright, {"page": pw_page}).count() == 1  # le code des tests générés désigne bien ce bouton
    assert pw_page.locator("xpath=" + banner.robot.removeprefix("xpath:")).count() == 1  # et celui des tests Robot
    assert cookies.accept(pw_page, banner).ok and pw_page.locator("#bandeau").count() == 0
    assert pw_page.title() == ""  # « Personnaliser » n'a pas été cliqué


def test_un_bouton_accepter_hors_bandeau_n_est_jamais_clique(pw_page):
    """Un en-tête fixe qui ne parle pas de cookies, un formulaire qui en parle mais n'est pas un bandeau : rien."""
    pw_page.set_content(PAS_UN_BANDEAU)
    assert cookies.find(pw_page) is None
    assert cookies.dismiss(pw_page) is None and pw_page.title() == ""


def test_bandeau_dans_un_composant_web(pw_page):
    pw_page.set_content(USERCENTRICS)
    banner = cookies.find(pw_page)
    assert banner.outil == "Usercentrics" and banner.robot.startswith("dom:document.querySelector('#usercentrics-root')")
    assert cookies.accept(pw_page, banner).ok


def test_bandeau_qui_s_affiche_apres_la_page(pw_page):
    """Un outil de consentement est chargé : l'agent attend son bandeau, qui arrive une demi-seconde plus tard."""
    pw_page.set_content("""<html><head><script>window.__tcfapi = function () {};</script></head><body><p>Page</p><script>
      setTimeout(() => { document.body.insertAdjacentHTML('beforeend',
        '<div class="fc-consent-root"><button class="fc-button fc-cta-consent">Autoriser</button></div>'); }, 500);
    </script></body></html>""")
    assert cookies.find(pw_page) is None  # sans attente : rien encore
    banner = cookies.find(pw_page, wait_s=3)
    assert banner is not None and banner.outil == "Google"


def test_sans_outil_de_consentement_aucune_attente(pw_page, demo_url):
    pw_page.goto(f"{demo_url}/index.html")
    start = time.monotonic()
    assert cookies.find(pw_page, wait_s=cookies.WAIT_S) is None
    assert time.monotonic() - start < 1


def test_analyse_d_un_site_avec_bandeau(pw_page, demo_url):
    """L'analyse d'un site accepte aussi le bandeau : ses boutons ne sont pas pris pour ceux du site."""
    from agent.analyse import explore

    analysis = explore(pw_page, f"{demo_url}/newsletter.html", max_pages=1)
    assert analysis.pages[0].buttons == ["« S'inscrire »"]  # ni « Autoriser », ni « Gérer les options »
