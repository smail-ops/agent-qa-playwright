"""La démo CT-91 (cadre, composant web, liste maison, fenêtre JavaScript, chargement lent) : de l'agent aux tests générés.

Les trois tests générés (pytest, Robot Framework, Gherkin) doivent rejouer le parcours sans IA,
comme pour les pages simples. Le vrai SeleniumLibrary les rejoue aussi dans GitHub Actions (job robot).
"""

import json
from pathlib import Path

import pytest
from test_robot import run_robot

from agent.codegen import generate_script
from agent.codegen_gherkin import generate_bdd_robot, generate_feature
from agent.codegen_robot import generate_robot
from agent.memoire import Memory
from agent.providers import ScriptedProvider
from agent.report import build_report
from agent.runner import Agent
from agent.testcase import parse_test_case

DEMO = Path(__file__).resolve().parent.parent / "cas_de_test" / "demo"
SCENARIO = json.loads((DEMO / "scenario_CT-91.json").read_text(encoding="utf-8"))
SHADOW = "dom:document.querySelector('generateur-code').shadowRoot.querySelector"
ROLE = 'xpath=//label[normalize-space(.)="Rôle"]/following::div[@tabindex][1]'  # liste « maison », désignée par son libellé


def ct91(demo_url):
    text = (DEMO / "CT-91_demo_iframe_shadow_dom.md").read_text(encoding="utf-8")
    return parse_test_case(text.replace("http://localhost:8000", demo_url))


def run_on_new_page(pw_browser, function, *args):
    context = pw_browser.new_context()
    try:
        return function(context.new_page(), *args)
    finally:
        context.close()


@pytest.fixture(scope="module")
def run91(pw_browser, demo_url, tmp_path_factory):
    """Une seule exécution de la démo pour tout le fichier : le chargement lent prend 3 secondes."""
    folder = tmp_path_factory.mktemp("ct91") / "rapport"
    result = run_on_new_page(pw_browser, lambda page: Agent(ScriptedProvider(SCENARIO)).run(page, ct91(demo_url), folder))
    assert result.status == "PASS", result.summary
    return result


def test_verdict_et_rapport(run91):
    assert len(run91.checks) == 7 and all(step.result.check for step in run91.checks)
    envoi = next(step for step in run91.steps if step.result.dialog)
    assert envoi.result.dialog["message"] == "Confirmer l'envoi de la commande ?" and envoi.result.dialog["accepted"]
    report = build_report(run91)
    assert "`#note` (cadre « Éditeur de note »)" in report and "`#generer` dans `generateur-code`" in report
    assert "« Stock chargé » (10 s au plus)" in report and "| dialog | OK |" in report
    assert "| `[data-test=\"envoyer\"]` | OK, fenêtre « Confirmer l'envoi de la commande ? » : OK |" in report


def test_script_pytest(run91, pw_browser):
    code = generate_script(run91)
    for line in [
        "page.frame_locator('#editeur').locator('#note').fill('Livrer avant midi')",
        "expect(page.frame_locator('#editeur').locator('#note')).to_have_text('Livrer avant midi', use_inner_text=True)",
        # Dans le composant web : le localisateur recommandé par Playwright y entre seul
        "page.get_by_role('button', name='Générer un code', exact=True).click()",
        "expect(page.get_by_role('textbox', name='Code promo', exact=True)).to_have_value(re.compile('^PROMO-[0-9A-F]{4}-[0-9A-F]{4}$'))",
        "expect(page.get_by_text(re.compile(re.escape('Code prêt à copier'), re.IGNORECASE)).first).to_be_visible()",
        f"page.locator({ROLE!r}).click()",
        "page.get_by_role('option', name='ESS', exact=True).click()",
        f"expect(page.locator({ROLE!r})).to_have_text('ESS', use_inner_text=True)",
        "# Fenêtre « Confirmer l'envoi de la commande ? » : réponse OK\n"
        "    page.once('dialog', lambda dialog: dialog.accept())\n"
        "    page.get_by_role('button', name='Envoyer la commande', exact=True).click()",
        "expect(page.locator('body')).to_contain_text('Stock chargé', ignore_case=True, use_inner_text=True, timeout=10000)",
    ]:
        assert line in code, line
    namespace = {}
    exec(compile(code, "test_genere.py", "exec"), namespace)
    run_on_new_page(pw_browser, namespace[f"test_{run91.case.slug}"])  # lève AssertionError si le parcours échoue


def test_test_robot(run91, tmp_path):
    code = generate_robot(run91)
    expected = f"""    Select Frame    css:#editeur
    Click Element    css:#note
    Input Text    css:#note    Livrer avant midi
    Wait Until Keyword Succeeds    ${{DELAI}}    200ms    Le texte de l'élément est    css:#note    Livrer avant midi
    Unselect Frame
    Wait Until Page Contains Element    {SHADOW}('#generer')
    Cliquer    {SHADOW}('#generer')
    Wait Until Keyword Succeeds    ${{DELAI}}    200ms    La valeur correspond au motif    {SHADOW}('#code')    ^PROMO-[0-9A-F]{{4}}-[0-9A-F]{{4}}$
    La page affiche    Code prêt à copier
    Cliquer    xpath:{ROLE[6:]}
    Cliquer    xpath://div[@role="option"][contains(normalize-space(.), "ESS")]
    Wait Until Keyword Succeeds    ${{DELAI}}    200ms    Le texte de l'élément est    xpath:{ROLE[6:]}    ESS
    La page affiche    Rôle : ESS
    Cliquer    css:[data-test="envoyer"]
    Accepter la fenêtre    Confirmer l'envoi de la commande ?
    La page affiche    Commande envoyée
    Cliquer    css:[data-test="charger"]
    Wait Until Keyword Succeeds    10s    500ms    Le texte visible contient    Stock chargé
    La page affiche    Stock chargé : 12 articles
"""
    assert expected in code
    # Chaque mot-clé écrit une seule fois, et seulement ceux qui servent
    for keyword in ("Texte visible de la page", "Accepter la fenêtre", "La valeur correspond au motif", "Le texte de l'élément est"):
        assert code.count(f"\n{keyword}\n") == 1, keyword
    assert "\nLe texte correspond au motif\n" not in code and "\nLa page n'affiche pas\n" not in code
    result = run_robot(code, tmp_path)
    assert result.returncode == 0, result.stdout


def test_scenario_gherkin(run91, tmp_path):
    feature = generate_feature(run91)
    assert """    Étant donné que je suis sur la page "{url}"
    Quand je saisis "Livrer avant midi" dans la zone de texte "Note de commande"
    Alors la zone de texte "Note de commande" contient "Livrer avant midi"
    Quand je clique sur le bouton "Générer un code"
    Alors le champ "Code promo" correspond au motif "^PROMO-[0-9A-F]{{4}}-[0-9A-F]{{4}}$"
    Et la page affiche "Code prêt à copier"
    Quand je clique sur le champ "Rôle"
    Et je clique sur l'option "ESS"
    Alors le champ "Rôle" contient "ESS"
    Et la page affiche "Rôle : ESS"
    Quand je clique sur le bouton "Envoyer la commande"
    Et j'accepte la fenêtre "Confirmer l'envoi de la commande ?"
    Alors la page affiche "Commande envoyée"
    Quand je clique sur le bouton "Charger le stock"
    Et j'attends jusqu'à 10 secondes que la page affiche "Stock chargé"
    Alors la page affiche "Stock chargé : 12 articles"
""".format(url=run91.case.url) in feature
    code = generate_bdd_robot(run91)
    assert ('je saisis "${texte}" dans la zone de texte "Note de commande"\n    Select Frame    css:#editeur\n'
            "    Click Element    css:#note\n    Input Text    css:#note    ${texte}\n    Unselect Frame\n") in code
    assert "j'attends jusqu'à ${secondes:\\d+} secondes que la page affiche \"${texte}\"\n" in code
    result = run_robot(code, tmp_path)
    assert result.returncode == 0, result.stdout


REFUS = [
    {"action": "dialog", "args": {"accept": False}},
    {"action": "click", "target": '[data-test="envoyer"]'},
    {"action": "check_text", "args": {"text": "Envoi annulé"}},
    {"action": "finish", "args": {"status": "PASS", "summary": "Envoi annulé"}},
]


def test_fenetre_refusee_dans_les_tests_generes(pw_page, pw_browser, demo_url, tmp_path):
    """Annuler : Playwright le fait par défaut, Selenium le fait avec « Handle Alert    DISMISS »."""
    run = Agent(ScriptedProvider(REFUS)).run(pw_page, ct91(demo_url), tmp_path / "rapport")
    assert run.status == "PASS", run.summary
    code = generate_script(run)
    assert "page.once('dialog', lambda dialog: dialog.dismiss())" in code
    namespace = {}
    exec(compile(code, "test_genere.py", "exec"), namespace)
    run_on_new_page(pw_browser, namespace[f"test_{run.case.slug}"])
    robot = generate_robot(run)
    assert "    Cliquer    css:[data-test=\"envoyer\"]\n    Refuser la fenêtre    Confirmer l'envoi de la commande ?\n" in robot
    assert "${affiche}=    Handle Alert    DISMISS" in robot
    assert "Et je refuse la fenêtre \"Confirmer l'envoi de la commande ?\"" in generate_feature(run)


def test_parcours_memorise_rejoue_sans_modele(pw_page, pw_browser, demo_url, tmp_path):
    """Cadre, composant, fenêtre et attente se rejouent depuis la mémoire, sans appel au modèle."""
    memory = Memory(tmp_path / "memoire")
    first = Agent(ScriptedProvider(SCENARIO), memory=memory).run(pw_page, ct91(demo_url), tmp_path / "r1")
    assert first.status == "PASS" and first.memory_saved
    saved = json.loads(memory.path(first.case).read_text(encoding="utf-8"))["steps"]
    note = next(step for step in saved if step.get("element", {}).get("selector") == "#note")
    assert note["element"]["frame"] == [{"css": "#editeur", "nth": None, "label": "Éditeur de note"}]
    assert [step["action"] for step in saved].count("dialog") == 1 and any(step["action"] == "wait" for step in saved)

    replay = run_on_new_page(pw_browser, lambda page: Agent(ScriptedProvider([]), memory=memory).run(
        page, ct91(demo_url), tmp_path / "r2"))
    assert replay.status == "PASS", replay.summary
    assert replay.model_calls == 0 and replay.memory_replayed == len(saved)
