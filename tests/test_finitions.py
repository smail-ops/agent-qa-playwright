"""Finitions : publicités bloquées, bandeau de cookies dans un cadre, éléments à l'écran d'abord,
capture d'écran masquée pour la vision, vérification « absent » dans les cadres, import dans Squash TM
et documentation de Postman, d'Appium et de pytest-bdd pour Alfred."""

import base64
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from test_atelier_genere import run_on_new_page
from test_industrialisation import demo_case, in_thread, scenario
from test_robot import run_robot

from agent import documentation, squash
from agent.browser import AD_URLS, MASK_ATTR, REF_ATTR, AdBlocker, Observation, observe, vision_screenshot
from agent.codegen import generate_script
from agent.codegen_gherkin import generate_bdd_robot, generate_feature
from agent.codegen_robot import generate_robot
from agent.coffre import Coffre
from agent.execution import run_case
from agent.prompts import build_user_message
from agent.providers import ScriptedProvider
from agent.runner import Agent
from agent.testcase import parse_test_case

RACINE = Path(__file__).resolve().parent.parent


# --- Publicités : les régies sont bloquées, aucune publicité plein écran ne couvre la page --------------


@pytest.mark.parametrize("url, bloquee", [
    ("https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js", True),
    ("https://securepubads.g.doubleclick.net/tag/js/gpt.js", True),
    ("http://media.net/script.js", True),
    ("https://www.google.com/recaptcha/api.js", False),  # le captcha d'un formulaire n'est pas une publicité
    ("https://media.net.exemple.fr/script.js", False),
    ("https://fausse-doubleclick.net/", False),
    ("https://boutique.exemple.fr/?source=doubleclick.net", False),
])
def test_adresses_des_regies(url, bloquee):
    assert bool(AD_URLS.match(url)) is bloquee


def test_publicites_bloquees_dans_le_navigateur(pw_browser, demo_url):
    context = pw_browser.new_context()
    try:
        blocker = AdBlocker(context)
        page = context.new_page()
        page.goto(f"{demo_url}/offres.html")
        page.wait_for_load_state("load")
        assert blocker.count == 1  # le script de la régie, dans l'en-tête de la page
    finally:
        context.close()


# --- Bandeau de cookies dans un cadre (Sourcepoint) : CT-98 -------------------------------------------


@pytest.fixture(scope="module")
def run98(demo_url, tmp_path_factory):
    folder = tmp_path_factory.mktemp("ct98")
    result = in_thread(lambda: run_case(demo_case("CT-98", demo_url), scenario("CT-98"), folder / "rapport",
                                        sessions_dir=folder / "sessions", env_path=folder / ".env"))
    assert result.status == "PASS", result.summary
    return result


def test_bandeau_du_cadre_accepte_sans_ia(run98):
    step = run98.steps[1]
    assert step.action.name == "cookies" and step.source == "programme" and step.result.ok
    assert step.action.args == {
        "outil": "Sourcepoint", "bouton": "Tout accepter", "robot": "css:button.sp_choice_type_11",
        "playwright": "page.frame_locator('iframe[title=\"SP Consent Message\"]').locator('button.sp_choice_type_11')",
        "cadre": [{"css": 'iframe[title="SP Consent Message"]', "nth": None, "label": "SP Consent Message"}]}
    assert run98.ads_blocked == 1
    report = run98.report_path.read_text(encoding="utf-8")
    assert ("| Publicités | 1 requête de régies publicitaires bloquée : aucune publicité plein écran ne couvre la page |"
            in report)


def test_script_pytest_du_cadre(run98, pw_browser):
    code = generate_script(run98)
    assert "    page.context.route(PUBLICITES, lambda route: route.abort())\n" in code
    assert ("    bandeau_cookies = page.frame_locator('iframe[title=\"SP Consent Message\"]')"
            ".locator('button.sp_choice_type_11').first\n") in code
    namespace = {}
    exec(compile(code, "test_genere.py", "exec"), namespace)
    run_on_new_page(pw_browser, namespace[f"test_{run98.case.slug}"])  # un navigateur neuf : le bandeau revient
    assert "PUBLICITES" not in generate_script(replace(run98, ads_blocked=0))  # rien de bloqué : rien à rejouer


def test_test_robot_du_cadre(run98, tmp_path):
    code = generate_robot(run98)
    assert ("    Bloquer les publicités\n    Accepter le bandeau de cookies du cadre    "
            "css:iframe[title=\"SP Consent Message\"]    css:button.sp_choice_type_11\n") in code
    assert "@{PUBLICITES}    *googlesyndication.com*    *doubleclick.net*" in code
    result = run_robot(code, tmp_path)
    assert result.returncode == 0, result.stdout


def test_scenario_gherkin_du_cadre(run98, tmp_path):
    assert "    Et j'accepte le bandeau de cookies\n    Quand je clique sur le bouton \"Voir les offres\"\n" in \
        generate_feature(run98)
    code = generate_bdd_robot(run98)
    assert "    Ouvrir le navigateur    ${url}\n    Bloquer les publicités\n" in code
    assert ("j'accepte le bandeau de cookies\n    Accepter le bandeau de cookies du cadre    "
            "css:iframe[title=\"SP Consent Message\"]    css:button.sp_choice_type_11\n") in code
    result = run_robot(code, tmp_path)
    assert result.returncode == 0, result.stdout


# --- « Absent » : ni dans la page, ni dans ses cadres, dans les tests générés aussi -----------------------


def _absent(pw_browser, demo_url, folder, texte):
    case = parse_test_case(f"# CT-89 Atelier sans message\nURL : {demo_url}/atelier.html\n\n## Étapes\n1. Ouvrir la page\n\n"
                           f"## Résultats attendus\n- « {texte} » ne s'affiche pas\n")
    steps = [{"action": "check_text", "args": {"text": texte, "absent": True}, "reason": "Résultat attendu négatif"},
             {"action": "finish", "args": {"status": "PASS", "summary": "Le texte ne s'affiche pas."}, "reason": "Vérifié"}]
    run = run_on_new_page(pw_browser, lambda page: Agent(ScriptedProvider(steps)).run(page, case, folder))
    return run, next(step for step in run.steps if step.action and step.action.name == "check_text")


def test_absent_ni_dans_la_page_ni_dans_son_cadre(pw_browser, demo_url, tmp_path):
    run, check = _absent(pw_browser, demo_url, tmp_path / "rapport", "Note enregistrée")
    assert run.status == "PASS", run.summary
    assert check.result.check is True and check.result.frames_checked == [
        [{"css": "#editeur", "nth": None, "label": "Éditeur de note"}]]
    code = generate_script(run)
    assert ("    expect(page.frame_locator('#editeur').locator('body')).not_to_contain_text('Note enregistrée', "
            "ignore_case=True, use_inner_text=True)\n") in code
    namespace = {}
    exec(compile(code, "test_genere.py", "exec"), namespace)
    run_on_new_page(pw_browser, namespace[f"test_{run.case.slug}"])
    robot = generate_robot(run)
    assert ("    La page n'affiche pas    Note enregistrée\n    Select Frame    css:#editeur\n"
            "    La page n'affiche pas    Note enregistrée\n    Unselect Frame\n") in robot
    assert run_robot(robot, tmp_path / "robot").returncode == 0
    feature = generate_feature(run)
    assert '    Alors la page et le cadre "Éditeur de note" n\'affichent pas "Note enregistrée"\n' in feature
    bdd = run_robot(generate_bdd_robot(run), tmp_path / "bdd")
    assert bdd.returncode == 0, bdd.stdout


def test_absent_de_la_page_mais_present_dans_le_cadre(pw_browser, demo_url, tmp_path):
    """Le texte n'est que dans l'éditeur (un cadre) : la vérification « absent » échoue, et dit où il est."""
    run, check = _absent(pw_browser, demo_url, tmp_path / "rapport", "Votre note ici")
    assert check.result.check is False
    assert check.result.detail == ("Texte « Votre note ici » présent (dans le cadre « Éditeur de note ») "
                                   "alors qu'il devrait être absent")
    assert run.status == "FAIL"


# --- Plus de 80 éléments : ceux qui sont à l'écran d'abord ----------------------------------------------


def test_elements_a_l_ecran_d_abord(pw_page):
    boutons = "".join(f"<button>Bouton {numero}</button>" for numero in range(1, 101))
    pw_page.set_content(f"<style>body {{ margin: 0 }} button {{ display: block; height: 40px; margin: 0 }}</style>{boutons}")
    pw_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    observation = observe(pw_page, max_elements=30)
    numeros = [int(element.text.split()[-1]) for element in observation.elements]
    assert len(numeros) == 30 and observation.more == 70
    assert 100 in numeros and 50 not in numeros  # le bas de la page, à l'écran, passe avant le milieu
    assert numeros == sorted(numeros) and [element.ref for element in observation.elements] == list(range(1, 31))
    assert pw_page.locator(f'[{REF_ATTR}="30"]').inner_text() == "Bouton 100"
    assert observation.to_prompt().endswith("(et 70 autres plus loin dans la page : fais défiler pour les voir)")
    assert "plus loin dans la page" not in observe(pw_page, max_elements=100).to_prompt()


# --- Vision : la capture d'écran envoyée au modèle cache les données personnelles -----------------------

_PAGE_PERSONNELLE = """
<style>
  body { margin: 0; font-family: sans-serif; }
  .zone { background: #ffd400; padding: 30px; margin: 10px; width: 600px; }
  input { background: #ffd400; width: 300px; height: 60px; border: 0; margin: 10px; }
</style>
<p class="zone" id="mail">Écrire à jeanne.martin@exemple.fr</p>
<p class="zone" id="mdp">Mot de passe du compte de démonstration : Azerty123!</p>
<p class="zone" id="libre">Livraison offerte dès 50 euros</p>
<label for="nom">Nom</label><input id="nom" name="nom" value="Durand">
"""


def _couleur(pw_browser, jpeg: bytes, x: float, y: float) -> list[int]:
    """Couleur d'un point de la capture, lue par le navigateur : pas de bibliothèque d'images à installer."""
    context = pw_browser.new_context()
    try:
        return context.new_page().evaluate("""async ({data, x, y}) => {
            const image = new Image();
            image.src = 'data:image/jpeg;base64,' + data;
            await image.decode();
            const canvas = document.createElement('canvas');
            canvas.width = image.width;
            canvas.height = image.height;
            const context = canvas.getContext('2d');
            context.drawImage(image, 0, 0);
            return Array.from(context.getImageData(x, y, 1, 1).data.slice(0, 3));
        }""", {"data": base64.b64encode(jpeg).decode(), "x": int(x), "y": int(y)})
    finally:
        context.close()


def _noir(rgb):
    return max(rgb) < 60


def _jaune(rgb):
    return rgb[0] > 200 and rgb[1] > 160 and rgb[2] < 100


def test_capture_de_la_vision_masquee(pw_page, pw_browser):
    pw_page.set_content(_PAGE_PERSONNELLE)
    capture = vision_screenshot(pw_page, observe(pw_page), known="", secrets=["Azerty123!"])
    couleurs = {}
    for cible in ("#mail", "#mdp", "#libre", "#nom"):
        boite = pw_page.locator(cible).bounding_box()
        couleurs[cible] = _couleur(pw_browser, capture, boite["x"] + 15, boite["y"] + 15)  # dans la marge intérieure
    assert _noir(couleurs["#mail"]) and _noir(couleurs["#mdp"]) and _noir(couleurs["#nom"]), couleurs
    assert _jaune(couleurs["#libre"]), couleurs
    assert pw_page.locator(f"[{MASK_ATTR}]").count() == 0  # les repères posés pour la capture sont retirés


def test_capture_garde_ce_que_le_cas_donne(pw_page, pw_browser):
    """L'adresse e-mail que le cas de test demande de vérifier reste visible : l'agent doit pouvoir la lire."""
    pw_page.set_content(_PAGE_PERSONNELLE)
    connu = "Vérifier que « jeanne.martin@exemple.fr » s'affiche\nSaisir « Durand » dans le champ Nom"
    capture = vision_screenshot(pw_page, observe(pw_page), known=connu)
    for cible in ("#mail", "#nom"):
        boite = pw_page.locator(cible).bounding_box()
        assert _jaune(_couleur(pw_browser, capture, boite["x"] + 15, boite["y"] + 15)), cible


def test_mot_de_passe_affiche_par_la_page_cache_au_modele():
    """Un site d'entraînement affiche le mot de passe de son compte de test : le modèle ne le lit pas non plus."""
    case = parse_test_case("# CT-61 Connexion\nURL : https://the-internet.herokuapp.com/login\n\n## Étapes\n"
                           "1. Saisir « tomsmith » dans le champ Username\n"
                           "2. Saisir « SuperSecretPassword! » dans le champ Password\n")
    page = Observation(url=case.url, title="Login Page",
                       text="Enter tomsmith for the username and SuperSecretPassword! for the password.")
    message = build_user_message(case, [], page)
    assert "SuperSecretPassword!" not in message
    assert "Enter tomsmith for the username and [mot de passe] for the password." in message


# --- Import des cas de test dans Squash TM --------------------------------------------------------------


def test_import_squash_tm(tmp_path, capsys):
    openpyxl = pytest.importorskip("openpyxl")
    dossier = tmp_path / "cas" / "boutique"
    dossier.mkdir(parents=True)
    (dossier / "CT-61_connexion.md").write_text(
        "# CT-61 Connexion au compte\nURL : https://boutique.exemple.fr/connexion\n\n"
        "## Préconditions\n- Le compte de test existe\n\n"
        "## Étapes\n1. Saisir « alfred » dans le champ Identifiant\n2. Saisir « P@ss2026! » dans le champ Mot de passe\n"
        "3. Cliquer sur « Se connecter »\n\n"
        "## Résultats attendus\n- Le message « Bienvenue » s'affiche\n- L'URL contient « /compte »\n", encoding="utf-8")
    (dossier / "panier.feature").write_text(
        "# language: fr\n# URL : https://boutique.exemple.fr/\n@CT-62 @priorite-haute\nFonctionnalité: CT-62 Panier\n\n"
        "  Scénario: Ajouter un article\n    Étant donné que je suis sur la page d'accueil\n"
        "    Quand j'ajoute « Lampe » au panier\n    Alors le panier contient « 1 » article\n", encoding="utf-8")
    (tmp_path / "cas" / "campagne.md").write_text("# Campagne\nRien à importer : pas un cas de test.\n", encoding="utf-8")
    sortie = tmp_path / "import.xlsx"
    assert squash.main([str(tmp_path / "cas"), "--projet", "Recette boutique", "--sortie", str(sortie)]) == 0
    sortie_console = capsys.readouterr().out
    assert "Cas de test      : 1 classiques (3 pas de test) et 1 Gherkin" in sortie_console
    assert "Ignoré           : " in sortie_console and "campagne.md : URL introuvable" in sortie_console

    classeur = openpyxl.load_workbook(sortie)
    assert classeur.sheetnames == ["TEST_CASES", "STEPS"]
    assert tuple(cell.value for cell in classeur["TEST_CASES"][1]) == squash.TEST_CASES
    assert tuple(cell.value for cell in classeur["STEPS"][1]) == squash.STEPS
    cas = {ligne["TC_REFERENCE"]: ligne for ligne in (dict(zip(squash.TEST_CASES, valeurs))
                                                     for valeurs in classeur["TEST_CASES"].iter_rows(min_row=2, values_only=True))}
    connexion, panier = cas["CT-61"], cas["CT-62"]
    assert connexion["ACTION"] == "C" and connexion["TC_PATH"] == "/Recette boutique/boutique/Connexion au compte"
    assert (connexion["TC_KIND"], connexion["TC_WEIGHT"], connexion["TC_STATUS"]) == ("STANDARD", "LOW", "WORK_IN_PROGRESS")
    assert "<p>Adresse : https://boutique.exemple.fr/connexion</p>" in connexion["TC_DESCRIPTION"]
    assert connexion["TC_PRE_REQUISITE"].startswith("<p>Le compte de test existe</p><p>[mot de passe] : le mot de passe")
    assert panier["TC_PATH"] == "/Recette boutique/boutique/Panier"
    assert (panier["TC_KIND"], panier["TC_WEIGHT"]) == ("GHERKIN", "HIGH")
    assert panier["TC_SCRIPT"].startswith("# language: fr\n") and "Quand j'ajoute « Lampe » au panier" in panier["TC_SCRIPT"]

    pas = [dict(zip(squash.STEPS, valeurs)) for valeurs in classeur["STEPS"].iter_rows(min_row=2, values_only=True)]
    assert [(p["TC_OWNER_PATH"], p["TC_STEP_NUM"]) for p in pas] == [(connexion["TC_PATH"], numero) for numero in (1, 2, 3)]
    assert pas[1]["TC_STEP_ACTION"] == "<p>Saisir « [mot de passe] » dans le champ Mot de passe</p>"
    assert pas[2]["TC_STEP_EXPECTED_RESULT"] == ("<ul><li>Le message « Bienvenue » s'affiche</li>"
                                                 "<li>L'URL contient « /compte »</li></ul>")
    cellules = [str(cell.value) for feuille in classeur for ligne in feuille.iter_rows() for cell in ligne]
    assert not any("P@ss2026!" in valeur for valeur in cellules)


def test_aucun_mot_de_passe_des_cas_du_projet_dans_l_import():
    """Chaque cas du projet, exporté : ses mots de passe n'y sont plus (en mot entier, comme le coffre)."""
    vus = set()
    for path in sorted((RACINE / "cas_de_test").rglob("*")):
        if path.suffix not in (".md", ".feature") or "campagne" in path.name:
            continue
        secrets = Coffre.from_case(path.read_text(encoding="utf-8")).secrets.values()
        export = squash.from_feature(path, "", "projet") if path.suffix == ".feature" else squash.from_markdown(path, "", "projet")
        contenu = json.dumps(vars(export), ensure_ascii=False)
        for secret in secrets:
            assert re.search(rf"(?<![\w]){re.escape(secret)}(?![\w])", contenu) is None, f"{path.name} : {secret}"
        vus |= set(secrets)
    assert {"secret_sauce", "SuperSecretPassword!", "admin123", "demo"} <= vus


# --- Alfred répond aussi sur Postman, Appium et pytest-bdd ------------------------------------------------

POSTMAN = "https://learning.postman.com/docs/"


@pytest.mark.parametrize("question, outil, page", [
    ("Comment lancer une collection Postman avec Newman dans GitHub Actions ?", "Postman",
     POSTMAN + "reference/newman-cli/command-line-integration-with-newman"),
    ("Comment écrire un test Postman qui vérifie le statut ?", "Postman",
     POSTMAN + "tests-and-scripts/write-scripts/postman-sandbox-reference/pm-test-expect"),
    ("Comment utiliser les variables d'environnement dans Postman ?", "Postman",
     POSTMAN + "use/send-requests/variables/managing-environments"),
    ("Comment installer Appium ?", "Appium", "https://appium.io/docs/en/latest/quickstart/install/"),
    ("Comment marche Appium ?", "Appium", "https://appium.io/docs/en/latest/intro/"),
    ("Comment écrire un scénario avec pytest-bdd ?", "pytest-bdd", "https://pytest-bdd.readthedocs.io/en/stable/"),
    ("Comment lancer les tests pytest de Playwright sur Firefox ?", "Playwright",
     "https://playwright.dev/python/docs/test-runners"),
    ("Comment émuler un iPhone avec Playwright ?", "Playwright", "https://playwright.dev/python/docs/emulation"),
    ("Comment réutiliser une session connectée avec Playwright ?", "Playwright", "https://playwright.dev/python/docs/auth"),
    ("Comment bloquer les publicités avec Playwright ?", "Playwright", "https://playwright.dev/python/docs/network"),
    ("Comment tester une API avec Playwright ?", "Playwright", "https://playwright.dev/python/docs/api-testing"),
])
def test_questions_sur_les_outils_ajoutes(question, outil, page):
    assert documentation.tools_in(question) == [outil]
    assert documentation.question_pages(question)[0].url == page


def test_pages_par_defaut_dans_la_liste():
    urls = {page.url for page in documentation.PAGES}
    assert all(url in urls for defaults in documentation.PAR_DEFAUT.values() for url in defaults)
    assert {outil for outil, _ in documentation.OUTILS} == set(documentation.PAR_DEFAUT)
