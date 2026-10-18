"""Génération du test Robot Framework (SeleniumLibrary).

Les tests qui exécutent le fichier .robot utilisent le vrai Robot Framework,
avec un double de SeleniumLibrary piloté par Playwright (dossier robot_double).
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from agent.codegen_robot import CHROME_PREFS, VARIABLES, generate_robot, robot_arg
from agent.providers import ScriptedProvider
from agent.runner import Agent
from agent.testcase import parse_test_case

DOUBLE_DIR = Path(__file__).parent / "robot_double"

PARCOURS = [
    {"action": "fill", "target": "#user-name", "args": {"text": "standard_user"}},
    {"action": "fill", "target": "#password", "args": {"text": "secret_sauce"}},
    {"action": "click", "target": '[data-test="login-button"]'},
    {"action": "check_url", "args": {"contains": "inventory"}},
    {"action": "check_text", "args": {"text": "produits"}},  # casse différente de la page
    {"action": "click", "target": 'button:has-text("Ajouter au panier")'},
    {"action": "select", "target": '[data-test="sort"]', "args": {"value": "Prix croissant"}},
    {"action": "check_text", "args": {"text": "Panier (1)"}},
    {"action": "check_text", "args": {"text": "Paiement validé", "absent": True}},
    {"action": "finish", "args": {"status": "PASS", "summary": "Parcours réussi"}},
]

VALEURS = ["standard_user", "", "prix ${total}", "deux  espaces", " bord ", "#produit", "text=abc", "C:\\dossier"]


def run_scenario(pw_page, demo_url, tmp_path, scenario):
    case = parse_test_case(f"# CT-01 Connexion et panier\nURL : {demo_url}/index.html\n\n## Étapes\n1. ...")
    return Agent(ScriptedProvider(scenario)).run(pw_page, case, tmp_path / "rapport")


def run_robot(code: str, tmp_path: Path, *options: str) -> subprocess.CompletedProcess:
    """Exécute un test .robot avec Robot Framework et le double de SeleniumLibrary."""
    pytest.importorskip("robot")
    path = tmp_path / "test_genere.robot"
    path.write_text(code, encoding="utf-8")
    command = [sys.executable, "-m", "robot", "--pythonpath", str(DOUBLE_DIR), "--outputdir", str(tmp_path / "robot"),
               "--consolecolors", "off", *options, str(path)]
    return subprocess.run(command, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("standard_user", "standard_user"),
        ("", "${EMPTY}"),
        ("prix ${total}", "prix \\${total}"),
        ("deux  espaces", "deux ${SPACE}espaces"),
        (" bord ", "${SPACE}bord${SPACE}"),
        ("#produit", "\\#produit"),
        ("text=abc", "text\\=abc"),
        ("C:\\dossier", "C:\\\\dossier"),
        ('css:[data-test="login-button"]', 'css:[data-test="login-button"]'),
    ],
)
def test_echappement_robot(value, expected):
    assert robot_arg(value) == expected


def test_echappement_relu_par_robot(tmp_path):
    """Robot Framework relit chaque valeur échappée exactement comme l'originale."""
    (tmp_path / "valeurs.py").write_text(f"VALEURS = {VALEURS!r}\n", encoding="utf-8")
    checks = "\n".join(f"    Should Be Equal    ${{VALEURS}}[{i}]    {robot_arg(v)}" for i, v in enumerate(VALEURS))
    result = run_robot(f"*** Test Cases ***\nÉchappement\n{checks}\n", tmp_path, "--variablefile", str(tmp_path / "valeurs.py"))
    assert result.returncode == 0, result.stdout


def test_test_robot_genere(pw_page, demo_url, tmp_path):
    result = run_scenario(pw_page, demo_url, tmp_path, PARCOURS)
    assert result.status == "PASS", result.summary
    code = generate_robot(result)

    assert "Library           SeleniumLibrary    timeout=${DELAI}    implicit_wait=${DELAI}" in code
    assert f"    Ouvrir le navigateur    {demo_url}/index.html\n    Input Text    css:#user-name    standard_user" in code
    assert code.count("\nOuvrir le navigateur\n") == 1 and "    Set Window Size    1280    800\n" in code
    assert "    Input Text    css:#user-name    standard_user" in code
    assert '    Click Element    css:[data-test="login-button"]' in code
    assert "    Wait Until Location Contains    inventory" in code
    assert "    La page affiche    Produits" in code  # casse exacte relevée sur la page
    assert '    Click Element    xpath:(//button[contains(normalize-space(.), "Ajouter au panier")])[1]' in code
    assert '    Select From List By Value    css:[data-test="sort"]    price' in code  # valeur réelle de l'option
    assert "    La page n'affiche pas    Paiement validé" in code
    assert "Wait Until Page Contains" not in code  # compterait aussi le texte caché
    # Les deux mots-clés de vérification sont écrits une seule fois dans le fichier
    assert code.count("\nLa page affiche\n") == 1 and code.count("\nLa page n'affiche pas\n") == 1


def test_test_robot_rejoue_le_parcours(pw_page, demo_url, tmp_path):
    """Le test généré passe avec Robot Framework, sur une page neuve."""
    code = generate_robot(run_scenario(pw_page, demo_url, tmp_path, PARCOURS))
    result = run_robot(code, tmp_path)
    assert result.returncode == 0, result.stdout
    assert "1 test, 1 passed, 0 failed" in result.stdout


def test_test_robot_cas_negatif(pw_page, demo_url, tmp_path):
    scenario = [
        {"action": "fill", "target": "#user-name", "args": {"text": "standard_user"}},
        {"action": "fill", "target": "#password", "args": {"text": "mauvais"}},
        {"action": "click", "target": '[data-test="login-button"]'},
        {"action": "check_text", "args": {"text": "mot de passe incorrect"}},
        {"action": "check_url", "args": {"contains": "inventory", "absent": True}},
        {"action": "finish", "args": {"status": "PASS", "summary": "Connexion refusée"}},
    ]
    code = generate_robot(run_scenario(pw_page, demo_url, tmp_path, scenario))
    assert "    Wait Until Location Does Not Contain    inventory" in code
    assert "La page n'affiche pas" not in code  # mot-clé inutile ici, donc absent du fichier
    result = run_robot(code, tmp_path)
    assert result.returncode == 0, result.stdout


def test_texte_cache_ne_compte_pas(pw_page, demo_url, tmp_path):
    """Le message d'erreur existe dans la page mais il est caché : le test Robot doit échouer."""
    scenario = [
        {"action": "check_text", "args": {"text": "mot de passe incorrect"}},
        {"action": "finish", "args": {"status": "PASS", "summary": "Message affiché"}},
    ]
    run = run_scenario(pw_page, demo_url, tmp_path, scenario)
    assert run.status == "FAIL"  # l'agent ne voit pas le message caché
    result = run_robot(generate_robot(run), tmp_path, "--variable", "DELAI:1s")
    assert result.returncode == 1, result.stdout
    assert "Le texte « mot de passe incorrect » n'est pas affiché." in result.stdout


def test_test_robot_verifications_riches(pw_page, demo_url, tmp_path):
    """Ordre, nombre, valeur et état : mots-clés générés, puis test rejoué par Robot Framework."""
    from test_agent import VERIFICATIONS_RICHES

    run = run_scenario(pw_page, demo_url, tmp_path, VERIFICATIONS_RICHES)
    assert run.status == "PASS", run.summary
    code = generate_robot(run)
    attente = "    Wait Until Keyword Succeeds    ${DELAI}    200ms    "
    assert attente + "Textfield Value Should Be    css:#user-name    standard_user" in code
    assert "    La page affiche ce nombre de fois    Ajouter au panier    3" in code
    assert attente + 'List Selection Should Be    css:[data-test="sort"]    Prix croissant' in code
    assert "    La page affiche dans l'ordre    Lampe de vélo    T-shirt    Sac à dos" in code
    assert attente + 'Element Should Be Disabled    css:[data-test="continuer"]' in code
    assert attente + 'Checkbox Should Not Be Selected    css:[data-test="cgu"]' in code
    assert attente + 'Checkbox Should Be Selected    css:[data-test="cgu"]' in code
    assert attente + "Textfield Value Should Be    css:#code-postal    ${EMPTY}" in code
    assert code.count("\nLa page affiche dans l'ordre\n") == 1 and code.count("\nLa page affiche ce nombre de fois\n") == 1

    result = run_robot(code, tmp_path)
    assert result.returncode == 0, result.stdout


def test_test_robot_ordre_non_respecte(pw_page, demo_url, tmp_path):
    """Liste non triée : l'agent conclut FAIL, et le test Robot généré échoue aussi, avec un message clair."""
    scenario = [
        {"action": "goto", "args": {"url": f"{demo_url}/inventory.html"}},
        {"action": "check_order", "args": {"texts": ["Lampe de vélo", "T-shirt", "Sac à dos"]}},
        {"action": "finish", "args": {"status": "PASS", "summary": "Liste triée"}},
    ]
    run = run_scenario(pw_page, demo_url, tmp_path, scenario)
    assert run.status == "FAIL"
    result = run_robot(generate_robot(run), tmp_path, "--variable", "DELAI:1s")
    assert result.returncode == 1, result.stdout
    assert "Ordre non respecté : « Sac à dos » n'est pas affiché à la place attendue." in result.stdout


def test_etat_coche_selon_le_type_de_case(tmp_path):
    """Case à cocher, bouton radio ou case dessinée (role=checkbox) : le bon mot-clé SeleniumLibrary."""
    from agent.browser import Element
    from agent.codegen_robot import _keyword, _state_keyword

    cases = {
        "css:#c": Element(1, "input", "checkbox", "", {"id": "c"}, "#c"),
        "css:#r": Element(2, "input", "radio", "", {"id": "r"}, "#r"),
        "css:#d": Element(3, "div", None, "Newsletter", {"id": "d"}, "#d"),
    }
    assert _state_keyword(cases["css:#c"], "checked") == ("Checkbox Should Be Selected",)
    assert _state_keyword(cases["css:#r"], "unchecked") == ("Element Attribute Value Should Be", "checked", "${None}")
    assert _state_keyword(cases["css:#d"], "checked") == ("Element Attribute Value Should Be", "aria-checked", "true")

    page = ("data:text/html,<input type=checkbox id=c checked><input type=radio name=g id=r>"
            "<input type=radio name=g id=r2 checked><div role=checkbox id=d aria-checked=true>Newsletter</div>")
    lines = [_keyword("Open Browser", page, "${BROWSER}")]
    for locator, element in cases.items():
        state = "unchecked" if locator == "css:#r" else "checked"
        lines.append(_keyword("Wait Until Keyword Succeeds", "1s", "200ms", _state_keyword(element, state)[0], locator,
                              *_state_keyword(element, state)[1:]))
    code = "*** Settings ***\nLibrary    SeleniumLibrary\n\n*** Variables ***\n${BROWSER}    chrome\n\n*** Test Cases ***\nCases\n"
    result = run_robot(code + "\n".join(lines) + "\n", tmp_path)
    assert result.returncode == 0, result.stdout


def test_options_chrome_lues_par_seleniumlibrary():
    """Le vrai SeleniumLibrary lit OPTIONS_CHROME : Chrome s'ouvre sans gestionnaire de mots de passe.

    Sans ces préférences, après une connexion avec un mot de passe connu des fuites de données
    (secret_sauce), Chrome affiche une alerte « mot de passe compromis » qui interrompt le test.
    """
    webdrivertools = pytest.importorskip("SeleniumLibrary.keywords.webdrivertools")
    value = re.search(r"^\$\{OPTIONS_CHROME\} {2,}(.+)$", VARIABLES, re.M).group(1)
    for browser in ("chrome", "headless_chrome"):
        options = webdrivertools.SeleniumOptions().create(browser, value)
        assert options.experimental_options["prefs"] == CHROME_PREFS


def _messages(output: Path) -> list[str]:
    """Messages écrits dans le journal Robot (output.xml)."""
    from robot.api import ExecutionResult, ResultVisitor

    class Messages(ResultVisitor):
        def __init__(self):
            self.found = []

        def visit_message(self, message):
            self.found.append(message.message)

    visitor = Messages()
    ExecutionResult(str(output)).visit(visitor)
    return visitor.found


@pytest.mark.parametrize("browser, chrome", [("chrome", True), ("headlesschrome", True), ("Google Chrome", True),
                                             ("firefox", False)])
def test_preferences_passees_seulement_a_chrome(pw_page, demo_url, tmp_path, browser, chrome):
    """« Ouvrir le navigateur » donne OPTIONS_CHROME à Chrome, et rien à Firefox (qui ne les comprendrait pas)."""
    connexion = PARCOURS[:4] + [{"action": "finish", "args": {"status": "PASS", "summary": "Connecté"}}]
    result = run_robot(generate_robot(run_scenario(pw_page, demo_url, tmp_path, connexion)), tmp_path,
                       "--variable", f"BROWSER:{browser}")
    assert result.returncode == 0, result.stdout
    opened = [m for m in _messages(tmp_path / "robot" / "output.xml") if m.startswith("Navigateur : ")]
    expected = f"Navigateur : {browser}, options : " + ('add_experimental_option("prefs", ' if chrome else "None")
    assert len(opened) == 1 and opened[0].startswith(expected), opened


def test_echecs_robot_affiches_sur_la_page_du_run(tmp_path):
    """GitHub Actions : chaque test Robot en échec devient une annotation, lisible sans se connecter."""
    pytest.importorskip("robot")
    suite = tmp_path / "ct_90_demo.robot"
    suite.write_text("*** Test Cases ***\nCT-90 Démo : tri, panier\n    Should Be Equal    a    b    msg=Valeur 100% fausse"
                     "    values=False\n\nRéussi\n    No Operation\n", encoding="utf-8")
    subprocess.run([sys.executable, "-m", "robot", "--outputdir", str(tmp_path / "out"), "--consolecolors", "off", str(suite)],
                   capture_output=True, text=True, timeout=120)
    script = Path(__file__).resolve().parent.parent / ".github" / "robot_echecs.py"
    summary = tmp_path / "resume.md"

    def run(output: Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(script), str(output)], capture_output=True, text=True, timeout=60,
                              env={**os.environ, "GITHUB_STEP_SUMMARY": str(summary)})

    result = run(tmp_path / "out" / "output.xml")
    assert result.returncode == 0, result.stderr
    # Les « : » et « , » du titre et le « % » du message sont échappés, comme le demande GitHub
    assert result.stdout.splitlines() == ["::error title=Robot %3A CT-90 Démo %3A tri%2C panier::ct_90_demo.robot : Valeur 100%25 fausse"]
    assert "| CT-90 Démo : tri, panier | ct_90_demo.robot | Valeur 100% fausse |" in summary.read_text(encoding="utf-8")
    absent = run(tmp_path / "absent.xml")
    assert absent.returncode == 0 and absent.stdout.startswith("::warning title=Robot Framework::")
