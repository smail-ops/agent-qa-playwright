"""Mots de passe du cas de test (jamais envoyés au modèle ni écrits dans les fichiers) et noms, adresses masqués."""

import json
from pathlib import Path

import pytest
from test_atelier_genere import run_on_new_page
from test_robot import run_robot

from agent import coffre
from agent.coffre import Coffre
from agent.codegen import generate_script
from agent.codegen_gherkin import generate_bdd_robot, generate_feature
from agent.codegen_robot import generate_robot
from agent.masquage import mask
from agent.memoire import Memory
from agent.prompts import build_user_message
from agent.providers import ScriptedProvider
from agent.report import build_report
from agent.runner import Agent
from agent.testcase import parse_test_case

DEMO = Path(__file__).resolve().parent.parent / "cas_de_test" / "demo"

# Comme un vrai modèle : il ne voit que le repère, et le saisit tel quel
CONNEXION = [
    {"action": "fill", "target": "#user-name", "args": {"text": "standard_user"}},
    {"action": "fill", "target": "#password", "args": {"text": "[mot de passe]"}},
    {"action": "click", "target": '[data-test="login-button"]'},
    {"action": "check_url", "args": {"contains": "inventory"}},
    {"action": "finish", "args": {"status": "PASS", "summary": "Connexion réussie"}},
]


def ct00(demo_url):
    text = (DEMO / "CT-00_demo_locale.md").read_text(encoding="utf-8")
    return parse_test_case(text.replace("http://localhost:8000", demo_url))


@pytest.fixture
def connexion(pw_page, demo_url, tmp_path):
    run = Agent(ScriptedProvider(CONNEXION)).run(pw_page, ct00(demo_url), tmp_path / "rapport")
    assert run.status == "PASS", run.summary
    return run


# --- Le coffre : repérer les mots de passe du cas de test ----------------------------------


@pytest.mark.parametrize("texte, attendu", [
    ("1. Saisir « standard_user » dans le champ Identifiant\n2. Saisir « secret_sauce » dans le champ Mot de passe",
     {"[mot de passe]": "secret_sauce"}),
    # Gherkin : le nom du champ est entre guillemets lui aussi, mais il contient le mot « Password »
    ('Quand je saisis "standard_user" dans le champ "Username"\nEt je saisis "secret_sauce" dans le champ "Password"',
     {"[mot de passe]": "secret_sauce"}),
    ("1. Saisir « Admin » dans Username et « admin123 » dans le champ Password", {"[mot de passe]": "admin123"}),
    ("- Le message « Mot de passe incorrect » s'affiche", {}),
    ("1. Saisir « Azerty1 » dans Mot de passe\n2. Saisir « Qwerty2 » dans Confirmation du mot de passe",
     {"[mot de passe 1]": "Azerty1", "[mot de passe 2]": "Qwerty2"}),
    ("1. Saisir « bonjour » dans le champ Recherche", {}),
])
def test_mots_de_passe_du_cas(texte, attendu):
    assert Coffre.from_case(texte).secrets == attendu


def test_repere_en_mot_entier():
    vault = Coffre({"[mot de passe]": "admin"})
    assert vault.hide("Mot de passe admin pour l'administrateur") == "Mot de passe [mot de passe] pour l'administrateur"
    assert vault.reveal("[mot de passe]") == "admin"


# --- Le modèle ne voit jamais le mot de passe ; le rapport et la mémoire non plus ---------


def test_le_modele_ne_voit_que_le_repere(connexion):
    message = build_user_message(connexion.case, connexion.steps, connexion.final_page or _observation())
    assert "secret_sauce" not in message and "Saisir « [mot de passe] » dans le champ Mot de passe" in message
    assert '3. fill [2] "[mot de passe]" -> OK' in message  # l'historique garde le repère


def _observation():
    from agent.browser import Observation

    return Observation(url="https://exemple.fr/", title="Exemple", text="")


def test_le_programme_tape_le_vrai_mot_de_passe(connexion):
    """Le modèle a saisi le repère : la connexion a réussi, donc le programme a tapé le vrai mot de passe."""
    assert connexion.steps[2].action.args["text"] == "[mot de passe]" and connexion.steps[2].result.ok
    assert connexion.checks[0].result.check


def test_rien_dans_le_rapport(connexion):
    report = build_report(connexion)
    assert "secret_sauce" not in report and "`#password` ← « [mot de passe] »" in report


def test_un_vrai_mot_de_passe_saisi_est_note_avec_son_repere(pw_page, demo_url, tmp_path):
    """La démo sans IA tape le vrai mot de passe : l'étape garde quand même le repère."""
    scenario = [dict(CONNEXION[1], args={"text": "secret_sauce"}) if step is CONNEXION[1] else step for step in CONNEXION]
    run = Agent(ScriptedProvider(scenario)).run(pw_page, ct00(demo_url), tmp_path / "rapport")
    assert run.status == "PASS" and run.steps[2].action.args["text"] == "[mot de passe]"


def test_memoire_sans_mot_de_passe(pw_browser, demo_url, tmp_path):
    memory = Memory(tmp_path / "memoire")

    class Gemini(ScriptedProvider):  # la mémoire ne sert qu'aux vrais modèles
        name = "gemini"

    first = run_on_new_page(pw_browser, lambda page: Agent(Gemini(CONNEXION), memory=memory).run(
        page, ct00(demo_url), tmp_path / "r1"))
    assert first.memory_saved
    saved = memory.path(first.case).read_text(encoding="utf-8")
    assert "secret_sauce" not in saved and "[mot de passe]" in saved
    replay = run_on_new_page(pw_browser, lambda page: Agent(Gemini([]), memory=memory).run(
        page, ct00(demo_url), tmp_path / "r2"))
    assert replay.status == "PASS" and replay.model_calls == 0  # le repère rejoué, le vrai mot de passe tapé


# --- Les tests générés lisent le mot de passe dans le fichier .env -------------------------


def test_tests_generes_sans_mot_de_passe(connexion, pw_browser, tmp_path, monkeypatch):
    name = "MOT_DE_PASSE_127_0_0_1"  # le site de démo des tests : 127.0.0.1
    monkeypatch.setenv(name, "secret_sauce")

    code = generate_script(connexion)
    assert "secret_sauce" not in code
    assert f"page.locator('#password').fill(secret({name!r}))" in code
    assert "load_dotenv(find_dotenv(usecwd=True))" in code
    namespace = {}
    exec(compile(code, "test_genere.py", "exec"), namespace)
    run_on_new_page(pw_browser, namespace[f"test_{connexion.case.slug}"])

    robot = generate_robot(connexion)
    assert "secret_sauce" not in robot
    assert f"    Input Password    css:#password    %{{{name}}}\n" in robot
    assert "Suite Setup       Charger les mots de passe\n" in robot and "\nCharger les mots de passe\n" in robot
    result = run_robot(robot, tmp_path / "classique")
    assert result.returncode == 0, result.stdout

    feature = generate_feature(connexion)
    assert "secret_sauce" not in feature
    assert f'    Et je saisis le mot de passe "{name}" dans le champ "Mot de passe"\n' in feature
    bdd = generate_bdd_robot(connexion)
    assert "secret_sauce" not in bdd and "Input Password    css:#password    %{${variable}}" in bdd
    result = run_robot(bdd, tmp_path / "gherkin")
    assert result.returncode == 0, result.stdout
    for journal in (tmp_path / "classique" / "robot" / "output.xml", tmp_path / "gherkin" / "robot" / "output.xml"):
        assert "secret_sauce" not in journal.read_text(encoding="utf-8")  # pas même dans le journal de Robot


def test_variable_absente(connexion, monkeypatch):
    monkeypatch.delenv("MOT_DE_PASSE_127_0_0_1", raising=False)
    namespace = {}
    exec(compile(generate_script(connexion), "test_genere.py", "exec"), namespace)
    with pytest.raises(RuntimeError, match="Ajoute MOT_DE_PASSE_127_0_0_1=... dans le fichier .env"):
        namespace["secret"]("MOT_DE_PASSE_127_0_0_1")


def test_fichier_env(tmp_path, monkeypatch):
    """Un mot de passe garde son nom d'un cas à l'autre ; un autre, sur le même site, prend le suivant."""
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=cle\n", encoding="utf-8")
    for name in ("MOT_DE_PASSE_SAUCEDEMO_COM", "MOT_DE_PASSE_SAUCEDEMO_COM_2"):
        monkeypatch.delenv(name, raising=False)
    url = "https://www.saucedemo.com/"
    first = coffre.store(url, Coffre({"[mot de passe]": "secret_sauce"}), ["[mot de passe]"], env)
    again = coffre.store(url, Coffre({"[mot de passe]": "secret_sauce"}), ["[mot de passe]"], env)
    other = coffre.store(url, Coffre({"[mot de passe]": "mauvais'mot"}), ["[mot de passe]"], env)
    assert first == again == {"[mot de passe]": "MOT_DE_PASSE_SAUCEDEMO_COM"}
    assert other == {"[mot de passe]": "MOT_DE_PASSE_SAUCEDEMO_COM_2"}
    assert env.read_text(encoding="utf-8") == (
        "GEMINI_API_KEY=cle\n# Mots de passe de test, lus par les tests générés (jamais écrits dans les tests)\n"
        "MOT_DE_PASSE_SAUCEDEMO_COM='secret_sauce'\nMOT_DE_PASSE_SAUCEDEMO_COM_2=\"mauvais'mot\"\n")
    from dotenv import dotenv_values

    assert dotenv_values(env)["MOT_DE_PASSE_SAUCEDEMO_COM_2"] == "mauvais'mot"


def test_execution_complete_range_le_mot_de_passe(demo_url, tmp_path, monkeypatch):
    """Une vraie exécution : le .env reçoit le mot de passe, aucun fichier produit ne le contient."""
    from agent.execution import run_case

    import threading

    monkeypatch.delenv("MOT_DE_PASSE_127_0_0_1", raising=False)
    env, results = tmp_path / ".env", []
    # run_case lance son propre Playwright : dans un autre fil, comme dans Alfred
    worker = threading.Thread(target=lambda: results.append(run_case(ct00(demo_url), ScriptedProvider(CONNEXION),
                                                                     tmp_path / "rapport", env_path=env)))
    worker.start()
    worker.join(120)
    run = results[0]
    assert run.status == "PASS" and run.secrets == {"[mot de passe]": "MOT_DE_PASSE_127_0_0_1"}
    assert "MOT_DE_PASSE_127_0_0_1='secret_sauce'" in env.read_text(encoding="utf-8")
    for produced in (tmp_path / "rapport").iterdir():
        if produced.is_file():
            assert "secret_sauce" not in produced.read_text(encoding="utf-8", errors="ignore"), produced.name
    assert json.loads((tmp_path / "rapport" / "resultat.json").read_text(encoding="utf-8"))["statut"] == "PASS"


# --- Noms et adresses ------------------------------------------------------------------


@pytest.mark.parametrize("texte, attendu", [
    ("Bonjour Jean Dupont, votre commande est prête.", "Bonjour [nom masqué], votre commande est prête."),
    ("Logged in as Alfred Campagne", "Logged in as [nom masqué]"),
    ("Livraison : 12 rue de la Paix, 75002 Paris. Merci !", "Livraison : [adresse masquée]. Merci !"),
    ("Adresse : 3 bis avenue Victor Hugo", "Adresse : [adresse masquée]"),
    ("Ship to 221 Baker Street", "Ship to [adresse masquée]"),
    ('[3] input type=text name="lastname" label="Nom" valeur="Dupont"',
     '[3] input type=text name="lastname" label="Nom" valeur="[donnée personnelle masquée]"'),
    ('[5] input type=text label="Code postal" autocomplete="postal-code" valeur="75002"',
     '[5] input type=text label="Code postal" autocomplete="postal-code" valeur="[donnée personnelle masquée]"'),
])
def test_noms_et_adresses_masques(texte, attendu):
    assert mask(texte) == attendu


@pytest.mark.parametrize("texte", [
    "Welcome to our store",
    "Commande 10234 Expédiée le 12 mars",
    '[4] input type=text name="username" label="Username" valeur="standard_user"',
    '[6] input type=text label="Ville" valeur=""',
])
def test_pas_un_nom_ni_une_adresse(texte):
    assert mask(texte) == texte


def test_un_nom_donne_par_le_cas_reste_visible():
    assert mask("Logged in as Alfred Campagne", "Vérifier que « Alfred Campagne » s'affiche") == "Logged in as Alfred Campagne"
    assert mask('[3] input label="Nom" valeur="Dupont"', "Saisir « Dupont » dans le champ Nom") == '[3] input label="Nom" valeur="Dupont"'


def test_champ_d_adresse_signale_au_modele(pw_page):
    from agent.browser import observe

    pw_page.set_content('<label for="cp">Code postal</label><input id="cp" autocomplete="postal-code" value="75002">'
                        '<input id="q" autocomplete="off" aria-label="Recherche">')
    lines = observe(pw_page).to_prompt()
    assert 'autocomplete="postal-code" valeur="75002"' in lines and 'autocomplete="off"' not in lines
    assert 'valeur="[donnée personnelle masquée]"' in mask(lines)
