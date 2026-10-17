"""Génération du test Robot Framework (SeleniumLibrary).

Les tests qui exécutent le fichier .robot utilisent le vrai Robot Framework,
avec un double de SeleniumLibrary piloté par Playwright (dossier robot_double).
"""

import subprocess
import sys
from pathlib import Path

import pytest

from agent.codegen_robot import generate_robot, robot_arg
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
    assert f"    Open Browser    {demo_url}/index.html    ${{BROWSER}}\n    Set Window Size    1280    800" in code
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
