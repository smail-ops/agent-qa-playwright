"""Clavier, survol, double-clic, clic droit, défilement, onglets, fichiers, glisser-déposer et composants fermés.

Les démos CT-93 et CT-94 les réunissent : l'agent les joue, puis les trois tests générés (pytest, Robot
Framework, Gherkin) les rejouent sans IA. Le vrai SeleniumLibrary les rejoue aussi dans GitHub Actions.
"""

import json
from pathlib import Path

import pytest
from test_atelier_genere import run_on_new_page
from test_robot import run_robot

from agent.actions import normalize_key, parse_action
from agent.anomalie import reproduction_step
from agent.browser import ActionResult, execute, observe, open_closed_shadows
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


def demo_case(name, demo_url):
    text = (DEMO / name).read_text(encoding="utf-8")
    return parse_test_case(text.replace("http://localhost:8000", demo_url))


def scenario(code):
    return json.loads((DEMO / f"scenario_{code}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def run93(pw_browser, demo_url, tmp_path_factory):
    folder = tmp_path_factory.mktemp("ct93") / "rapport"
    case = demo_case("CT-93_demo_clavier_souris.md", demo_url)
    result = run_on_new_page(pw_browser, lambda page: Agent(ScriptedProvider(scenario("CT-93"))).run(page, case, folder))
    assert result.status == "PASS", result.summary
    return result


@pytest.fixture(scope="module")
def run94(pw_browser, demo_url, tmp_path_factory):
    folder = tmp_path_factory.mktemp("ct94") / "rapport"
    case = demo_case("CT-94_demo_onglets_fichiers.md", demo_url)
    result = run_on_new_page(pw_browser, lambda page: Agent(ScriptedProvider(scenario("CT-94"))).run(page, case, folder))
    assert result.status == "PASS", result.summary
    return result


def replay_pytest(run, pw_browser):
    """Le test pytest généré, exécuté sur un navigateur neuf (fichiers joints copiés à côté de lui)."""
    path = run.out_dir / f"test_{run.case.slug}.py"
    namespace = {"__file__": str(path)}
    exec(compile(generate_script(run), str(path), "exec"), namespace)
    run_on_new_page(pw_browser, namespace[f"test_{run.case.slug}"])


# --- Lecture de la réponse du modèle -------------------------------------------------------


@pytest.mark.parametrize("touche, attendu", [
    ("Entrée", "Enter"), ("échap", "Escape"), ("Tab", "Tab"), ("Shift+Tab", "Shift+Tab"), ("flèche bas", "ArrowDown"),
    ("ctrl+a", "Control+A"), ("Retour arrière", "Backspace"), ("F5", "F5"), ("x", "x"),
])
def test_touches_du_clavier(touche, attendu):
    assert normalize_key(touche) == attendu


@pytest.mark.parametrize("reponse, attendu", [
    ({"action": "click", "args": {"ref": 3, "double": True}}, {"ref": 3, "double": True}),
    ({"action": "click", "args": {"ref": 3, "button": "droit"}}, {"ref": 3, "button": "right"}),
    ({"action": "click", "args": {"ref": 3, "double": False, "button": "left"}}, {"ref": 3}),
    ({"action": "press", "args": {"key": "entrée", "ref": 2}}, {"key": "Enter", "ref": 2}),
    ({"action": "scroll", "args": {"to": "bas"}}, {"to": "bottom"}),
    ({"action": "scroll", "args": {"ref": 9, "to": "bottom"}}, {"ref": 9}),
    ({"action": "upload", "args": {"ref": 4, "file": "facture.txt"}}, {"ref": 4, "file": "facture.txt"}),
    ({"action": "drag", "args": {"ref": 2, "to": 7}}, {"ref": 2, "to": 7}),
    ({"action": "switch_tab", "args": {"index": 1}}, {"index": 1}),
])
def test_nouvelles_actions_lues(reponse, attendu):
    assert parse_action(json.dumps(reponse)).args == attendu


@pytest.mark.parametrize("reponse, raison", [
    ({"action": "press", "args": {"key": "Bonjour"}}, "touche « Bonjour » inconnue"),
    ({"action": "click", "args": {"ref": 1, "double": True, "button": "right"}}, "bouton gauche"),
    ({"action": "scroll", "args": {}}, "« ref »"),
    ({"action": "upload", "args": {"ref": 4, "file": "../.env"}}, "sans chemin"),
    ({"action": "upload", "args": {"ref": 4, "file": "/etc/passwd"}}, "sans chemin"),
    ({"action": "drag", "args": {"ref": 2, "to": 2}}, "deux éléments différents"),
    ({"action": "switch_tab", "args": {"index": 0}}, "premier onglet"),
])
def test_nouvelles_actions_refusees(reponse, raison):
    with pytest.raises(ValueError, match=raison):
        parse_action(json.dumps(reponse))


# --- CT-93 : clavier, survol, double-clic, clic droit, défilement ---------------------------


def test_clavier_et_souris_dans_le_rapport(run93):
    report = build_report(run93)
    for line in ("| press | `#recherche` ← touche « Entrée » | OK |", "| hover | `#catalogue` | OK |",
                 "| click | `#titre-projet` (double-clic) | OK |", "| click | `#zone-fichier` (clic droit) | OK |",
                 "| press | touche « Échap » | OK |", "| scroll | bas de la page | OK |"):
        assert line in report, line
    steps = {step.action.name + str(step.action.args.get("double", "")) + str(step.action.args.get("button", "")): step
             for step in run93.steps if step.action}
    assert reproduction_step(steps["press"]) == "Appuyer sur la touche Échap"
    assert reproduction_step(steps["clickTrue"]) == "Double-cliquer sur le bouton « Projet sans nom »"
    assert reproduction_step(steps["clickright"]) == "Faire un clic droit sur le bouton « Fichier rapport.pdf »"
    assert reproduction_step(steps["scroll"]) == "Faire défiler la page jusqu'en bas"
    assert describe_step(steps["press"]).startswith('12. press "Escape" -> OK')
    hover = next(step for step in run93.steps if step.action and step.action.name == "hover")
    assert narration.action_label(hover.action, hover.result.element) == f"Survol du bouton {narration.q('Catalogue')}"
    assert narration.action_speech(hover.action, hover.result.element) == f"Je survole le bouton {narration.q('Catalogue')}."


def test_clavier_et_souris_dans_les_tests_generes(run93, pw_browser, tmp_path):
    code = generate_script(run93)
    for line in ("page.locator('#recherche').press('Enter')", "page.locator('#catalogue').hover()",
                 "page.locator('#titre-projet').dblclick()", "page.locator('#zone-fichier').click(button='right')",
                 "page.keyboard.press('Escape')", "page.evaluate('window.scrollTo(0, document.body.scrollHeight)')"):
        assert line in code, line
    replay_pytest(run93, pw_browser)

    robot = generate_robot(run93)
    for line in ("Press Keys    css:#recherche    ENTER", "Mouse Over    css:#catalogue", "Double Click Element    css:#titre-projet",
                 "Open Context Menu    css:#zone-fichier", "Press Keys    None    ESCAPE",
                 "Execute Javascript    window.scrollTo(0, document.body.scrollHeight)"):
        assert f"    {line}\n" in robot, line
    result = run_robot(robot, tmp_path / "classique")
    assert result.returncode == 0, result.stdout

    feature = generate_feature(run93)
    for line in ('Et j\'appuie sur la touche "Entrée" dans le champ "Recherche"', 'Quand je survole le bouton "Catalogue"',
                 'Quand je double-clique sur le bouton "Projet sans nom"',
                 'Quand je fais un clic droit sur le bouton "Fichier rapport.pdf"', 'Quand j\'appuie sur la touche "Échap"',
                 "Quand je fais défiler la page jusqu'en bas"):
        assert f"    {line}\n" in feature, line
    result = run_robot(generate_bdd_robot(run93), tmp_path / "gherkin")
    assert result.returncode == 0, result.stdout


# --- CT-94 : nouvel onglet, fichier joint, glisser-déposer, composant fermé -----------------


def test_onglets_fichiers_dans_le_rapport(run94):
    opening = run94.steps[1]
    assert opening.result.opened_tab and "Nouvel onglet ouvert" in opening.result.detail
    assert reproduction_step(opening) == "Cliquer sur le lien « Ouvrir la fiche produit », ce qui ouvre un nouvel onglet"
    assert (run94.out_dir / "facture.txt").read_text(encoding="utf-8").startswith("Facture n° 2026-001")
    drag = next(step for step in run94.steps if step.action and step.action.name == "drag")
    assert drag.result.target.selector == "#termine"
    assert reproduction_step(drag) == "Glisser l'élément « Tâche : relire le cahier de tests » sur l'élément « Terminé »"
    assert run94.closed_shadow
    report = build_report(run94)
    for line in ("| click | `#lien-fiche` | OK, nouvel onglet ouvert |", "| switch_tab | onglet n°1 | OK |",
                 "| upload | `#piece-jointe` ← fichier « facture.txt » | OK |", "| drag | `#carte` → `#termine` | OK |",
                 "| Composants fermés | Shadow DOM « closed » ouverts pour le test"):
        assert line in report, line


def test_onglets_fichiers_dans_les_tests_generes(run94, pw_browser, tmp_path):
    code = generate_script(run94)
    assert """    page.context.add_init_script(OUVRIR_COMPOSANTS)
    page.goto('{url}')
    with page.expect_popup() as nouvel_onglet:
        page.locator('#lien-fiche').click()
    page = nouvel_onglet.value
""".format(url=run94.case.url) in code
    for line in ("page = page.context.pages[0]  # onglet n°1", "page.locator('#piece-jointe').set_input_files(DOSSIER / 'facture.txt')",
                 "page.locator('#carte').drag_to(page.locator('#termine'))",
                 "page.locator('note-fermee').locator('#note-interne').fill('À relire lundi')"):
        assert line in code, line
    replay_pytest(run94, pw_browser)

    robot = generate_robot(run94)
    for line in ("Ouvrir les composants fermés", "Click Element    css:#lien-fiche\n    Switch Window    NEW",
                 "@{onglets}=    Get Window Handles\n    Switch Window    ${onglets}[0]",
                 "Choose File    css:#piece-jointe    ${CURDIR}${/}facture.txt", "Glisser-déposer    css:#carte    css:#termine"):
        assert f"    {line}\n" in robot, line
    assert "${OUVRIR_COMPOSANTS}    (() => {" in robot
    (tmp_path / "classique").mkdir()
    (tmp_path / "classique" / "facture.txt").write_bytes((run94.out_dir / "facture.txt").read_bytes())
    result = run_robot(robot, tmp_path / "classique")
    assert result.returncode == 0, result.stdout

    feature = generate_feature(run94)
    for line in ("Et je passe sur le nouvel onglet", "Quand je reviens sur l'onglet n°1",
                 'Quand je joins le fichier "facture.txt" au champ "Pièce jointe"',
                 'Quand je glisse l\'élément "Tâche : relire le cahier de tests" sur l\'élément "Terminé"'):
        assert f"    {line}\n" in feature, line
    (tmp_path / "gherkin").mkdir()
    (tmp_path / "gherkin" / "facture.txt").write_bytes((run94.out_dir / "facture.txt").read_bytes())
    result = run_robot(generate_bdd_robot(run94), tmp_path / "gherkin")
    assert result.returncode == 0, result.stdout


def test_parcours_memorise_avec_onglets_et_glisser_deposer(pw_browser, demo_url, tmp_path):
    """Nouvel onglet, retour au premier, fichier, glisser-déposer : tout se rejoue depuis la mémoire, sans le modèle."""
    memory = Memory(tmp_path / "memoire")
    case = demo_case("CT-94_demo_onglets_fichiers.md", demo_url)

    class Gemini(ScriptedProvider):  # la mémoire ne sert qu'aux vrais modèles
        name = "gemini"

    first = run_on_new_page(pw_browser, lambda page: Agent(Gemini(scenario("CT-94")), memory=memory).run(
        page, case, tmp_path / "r1"))
    assert first.status == "PASS" and first.memory_saved
    saved = json.loads(memory.path(case).read_text(encoding="utf-8"))["steps"]
    drag = next(step for step in saved if step["action"] == "drag")
    assert drag["target"]["selector"] == "#termine" and "to" not in drag["args"]
    replay = run_on_new_page(pw_browser, lambda page: Agent(Gemini([]), memory=memory).run(page, case, tmp_path / "r2"))
    assert replay.status == "PASS", replay.summary
    assert replay.model_calls == 0 and replay.memory_replayed == len(saved)


def test_onglets_ouverts_montres_au_modele(pw_page, demo_url):
    pw_page.goto(f"{demo_url}/onglets_fichiers.html")
    with pw_page.context.expect_page() as onglet:
        pw_page.click("#lien-fiche")
    fiche = onglet.value
    fiche.wait_for_load_state()
    observation = observe(fiche)
    assert observation.tab == 2
    assert ("Onglets ouverts : 1. Boutique Démo - Onglets et fichiers ; 2. Boutique Démo - Fiche produit (actif)"
            in observation.to_prompt())


def test_fichier_absent_du_dossier_de_donnees(pw_page, demo_url):
    pw_page.goto(f"{demo_url}/onglets_fichiers.html")
    observation = observe(pw_page)
    field = next(e for e in observation.elements if e.selector == "#piece-jointe")
    result = execute(pw_page, parse_action(json.dumps({"action": "upload", "args": {"ref": field.ref, "file": "absent.pdf"}})),
                     observation)
    assert result == ActionResult(False, "Fichier « absent.pdf » introuvable dans le dossier donnees_de_test", field)


def test_composant_ferme_vu_grace_au_script(pw_browser, demo_url):
    """Sans le script, le champ du composant fermé est invisible pour l'agent ; avec, il est dans la liste."""
    def fields(page, opened):
        if opened:
            open_closed_shadows(page.context)
        page.goto(f"{demo_url}/onglets_fichiers.html")
        observation = observe(page)
        return [e.selector for e in observation.elements if e.shadow], observation.closed_shadows

    assert run_on_new_page(pw_browser, fields, False) == ([], 0)
    assert run_on_new_page(pw_browser, fields, True) == (["#note-interne", "#enregistrer"], 1)
