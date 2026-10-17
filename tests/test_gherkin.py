"""Génération du scénario Gherkin (.feature) et de sa version exécutable avec Robot Framework."""

import re

from test_agent import LOGIN_OK, VERIFICATIONS_RICHES
from test_robot import run_robot, run_scenario

from agent.browser import Element
from agent.codegen_gherkin import _Names, generate_bdd_robot, generate_feature

# Mots-clés Gherkin en français (dialecte « fr » de Cucumber), pour contrôler la forme du fichier
STEP = re.compile(r"^    (Étant donné que|Quand|Alors|Et) \S")


def check_gherkin(feature: str) -> list[str]:
    """Contrôle la structure du fichier .feature et renvoie ses étapes."""
    lines = feature.splitlines()
    assert lines[0] == "# language: fr"  # sinon Cucumber lirait les mots-clés en anglais
    assert any(line.startswith("Fonctionnalité: ") for line in lines)
    assert sum(line.startswith("  Scénario: ") for line in lines) == 1
    steps = [line for line in lines if line.startswith("    ")]
    assert steps and all(STEP.match(line) for line in steps), steps
    assert steps[0].startswith("    Étant donné que ")
    return [line.strip() for line in steps]


def test_scenario_gherkin_genere(pw_page, demo_url, tmp_path):
    run = run_scenario(pw_page, demo_url, tmp_path, LOGIN_OK)
    feature = generate_feature(run)
    assert "  @CT-01\n  Scénario: CT-01 Connexion et panier" in feature
    assert check_gherkin(feature) == [
        f'Étant donné que je suis sur la page "{demo_url}/index.html"',
        'Quand je saisis "standard_user" dans le champ "Identifiant"',
        'Et je saisis "secret_sauce" dans le champ "Mot de passe"',
        'Et je clique sur le bouton "Connexion"',
        'Alors l\'URL contient "inventory"',
        'Et la page affiche "Produits"',
        'Quand je clique sur le bouton "Ajouter au panier" n°1',
        'Et je choisis "Prix croissant" dans la liste "Trier"',
        'Alors la page affiche "Panier (1)"',
    ]


def test_gherkin_execute_par_robot(pw_page, demo_url, tmp_path):
    """Le même scénario, écrit pour Robot Framework : chaque phrase est un mot-clé qui rejoue l'étape."""
    run = run_scenario(pw_page, demo_url, tmp_path, LOGIN_OK)
    code = generate_bdd_robot(run)
    assert code.startswith("language: fr\n")
    assert '    Quand je saisis "standard_user" dans le champ "Identifiant"' in code
    assert 'je saisis "${texte}" dans le champ "Identifiant"\n    Input Text    css:#user-name    ${texte}' in code
    assert 'je clique sur le bouton "Connexion"\n    Click Element    css:[data-test="login-button"]' in code
    assert code.count('je saisis "${texte}" dans le champ "Identifiant"\n') == 1  # chaque mot-clé une seule fois
    result = run_robot(code, tmp_path)
    assert result.returncode == 0, result.stdout
    assert "1 test, 1 passed, 0 failed" in result.stdout


def test_gherkin_verifications_riches(pw_page, demo_url, tmp_path):
    run = run_scenario(pw_page, demo_url, tmp_path, VERIFICATIONS_RICHES)
    steps = check_gherkin(generate_feature(run))
    for phrase in [
        'Alors le champ "Identifiant" contient "standard_user"',
        'Alors la page affiche 3 fois "Ajouter au panier"',
        'Alors la liste "Trier" indique "Prix croissant"',
        'Et la page affiche dans cet ordre : "Lampe de vélo", "T-shirt" et "Sac à dos"',
        'Alors le bouton "Continuer" est grisé',
        'Et la case "J\'accepte les conditions générales de vente" est décochée',
        'Alors la case "J\'accepte les conditions générales de vente" est cochée',
        'Et le bouton "Continuer" est actif',
        'Et le champ "Code postal" est vide',
    ]:
        assert phrase in steps, phrase
    result = run_robot(generate_bdd_robot(run), tmp_path)
    assert result.returncode == 0, result.stdout


def test_gherkin_echec_reproduit(pw_page, demo_url, tmp_path):
    """Liste non triée : la version Robot du scénario échoue aussi, avec le même message que le test Robot classique."""
    scenario = [
        {"action": "goto", "args": {"url": f"{demo_url}/inventory.html"}},
        {"action": "check_order", "args": {"texts": ["Lampe de vélo", "T-shirt", "Sac à dos"]}},
        {"action": "finish", "args": {"status": "PASS", "summary": "Liste triée"}},
    ]
    run = run_scenario(pw_page, demo_url, tmp_path, scenario)
    assert run.status == "FAIL"
    assert check_gherkin(generate_feature(run))[1] == f'Quand je vais sur la page "{demo_url}/inventory.html"'
    result = run_robot(generate_bdd_robot(run), tmp_path, "--variable", "DELAI:1s")
    assert result.returncode == 1, result.stdout
    assert "Ordre non respecté : « Sac à dos » n'est pas affiché à la place attendue." in result.stdout


def test_gherkin_valeurs_speciales(pw_page, demo_url, tmp_path):
    """${...}, antislash et espaces doublés restent des données de test, rejouées à l'identique."""
    valeur = "prix ${total}  C:\\dossier"
    scenario = [
        {"action": "fill", "target": "#user-name", "args": {"text": valeur}},
        {"action": "check_value", "target": "#user-name", "args": {"value": valeur}},
        {"action": "finish", "args": {"status": "PASS", "summary": "Valeur gardée"}},
    ]
    run = run_scenario(pw_page, demo_url, tmp_path, scenario)
    assert run.status == "PASS", run.summary
    assert f'Quand je saisis "{valeur}" dans le champ "Identifiant"' in generate_feature(run)
    code = generate_bdd_robot(run)
    assert '    Quand je saisis "prix \\${total} ${SPACE}C:\\\\dossier" dans le champ "Identifiant"' in code
    result = run_robot(code, tmp_path)
    assert result.returncode == 0, result.stdout


def test_noms_uniques_pour_robot():
    """Deux éléments différents au même nom : le second est numéroté, sinon Robot ne saurait pas lequel choisir."""
    names = _Names()
    premier = Element(1, "a", None, "Panier", {}, "#panier-haut")
    second = Element(2, "a", None, "PANIER", {}, "#panier-bas")  # Robot ignore la casse
    assert names.get(premier) == ('le lien "Panier"', False)
    assert names.get(second) == ('le lien "PANIER" (2)', False)
    assert names.get(premier) == ('le lien "Panier"', False)  # même élément, même nom
    variable = Element(3, "button", None, "Total ${prix}", {}, "#total")
    assert names.get(variable)[0] == 'le bouton "Total $(prix)"'  # pas de variable Robot dans un nom de mot-clé


def test_verification_visuelle_a_automatiser(pw_page, demo_url, tmp_path):
    """Un avis visuel de l'IA ne se rejoue pas : le scénario le garde, et Robot le signale en avertissement."""
    from test_agent import VISUEL

    from agent.codegen_robot import generate_robot
    from agent.providers import ScriptedProvider
    from agent.runner import Agent
    from agent.testcase import parse_test_case

    case = parse_test_case(f"# CT-16 Images des produits\nURL : {demo_url}/inventory.html\n\n## Étapes\n1. ...")
    run = Agent(ScriptedProvider(VISUEL), vision=True).run(pw_page, case, tmp_path / "rapport")
    assert 'Et l\'écran montre "Chaque produit a sa propre image"' in check_gherkin(generate_feature(run))
    assert "    # À automatiser : vérification visuelle faite par l'IA : Chaque produit a sa propre image" in generate_robot(run)

    result = run_robot(generate_bdd_robot(run), tmp_path)
    assert result.returncode == 0, result.stdout
    assert "[ WARN ] Vérification visuelle faite par l'IA, à automatiser : Chaque produit a sa propre image" in result.stderr
    assert run_robot(generate_robot(run), tmp_path).returncode == 0
