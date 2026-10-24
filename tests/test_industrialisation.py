"""Industrialisation : données uniques, sessions réutilisées, localisateurs recommandés par Playwright,
Firefox, WebKit et mobile, campagne en une commande et relance des tests en échec avec Alfred."""

import importlib.metadata
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect
from test_alfred import alfred, read_events, request  # noqa: F401 (fixture partagée)
from test_atelier_genere import run_on_new_page
from test_robot import run_robot

from agent import campagne, navigateur
from agent.__main__ import main as agent_main
from agent.actions import Action
from agent.bilan import Execution, comparer, lire_campagne
from agent.browser import Element, implicit_role, observe, recommend
from agent.codegen import generate_script, locator_code
from agent.codegen_gherkin import generate_bdd_robot, generate_feature
from agent.codegen_robot import generate_robot
from agent.donnees import contient, masquer, morceaux, remplacer, remplacer_action, valeur_unique
from agent.execution import run_case
from agent.memoire import Memory
from agent.navigateur import NavigateurAbsent, choisir, lancer
from agent.providers import ScriptedProvider
from agent.runner import Agent
from agent.testcase import parse_test_case
from agent.user_stories import feature_cases
from alfred import narration
from alfred.conversation import check_action
from alfred.missions import failed_cases

DEMO = Path(__file__).resolve().parent.parent / "cas_de_test" / "demo"


def demo_text(code: str, demo_url: str) -> str:
    return next(DEMO.glob(f"{code}_*.md")).read_text(encoding="utf-8").replace("http://localhost:8000", demo_url)


def demo_case(code: str, demo_url: str):
    return parse_test_case(demo_text(code, demo_url))


def scenario(code: str) -> ScriptedProvider:
    return ScriptedProvider.from_file(DEMO / f"scenario_{code}.json")


def in_thread(function):
    """run_case lance son propre Playwright : dans un autre fil que celui des tests, comme dans Alfred."""
    results, errors = [], []

    def target():
        try:
            results.append(function())
        except BaseException as exc:  # l'erreur remonte dans le test
            errors.append(exc)

    worker = threading.Thread(target=target)
    worker.start()
    worker.join(180)
    if errors:
        raise errors[0]
    return results[0]


class FauxGemini(ScriptedProvider):
    """Un « vrai » modèle pour l'agent (la mémoire ne sert pas à la démo sans IA), qui rejoue un scénario."""

    name = "gemini"
    model = "faux modèle"


# --- {unique} : une donnée nouvelle à chaque exécution --------------------------------------


def test_valeur_unique_et_remplacements():
    assert valeur_unique(datetime(2026, 10, 23, 14, 30, 5)) == "1023143005"
    assert contient("alfred.{unique}@exemple.fr") and contient("{ Unique }") and not contient("unique")
    assert remplacer("alfred.{unique}@exemple.fr", "1023143005") == "alfred.1023143005@exemple.fr"
    assert remplacer("rien à remplacer", None) == "rien à remplacer"
    assert masquer("alfred.1023143005@exemple.fr", "1023143005") == "alfred.{unique}@exemple.fr"
    assert morceaux("a42b42", "42") == [(False, "a"), (True, ""), (False, "b"), (True, "")]
    assert morceaux("sans valeur", "42") == [(False, "sans valeur")]
    action = Action("check_order", {"texts": ["{unique}", "b"]}, "")
    assert remplacer_action(action, "42").args == {"texts": ["42", "b"]}
    assert remplacer_action(action, None) is action


@pytest.fixture
def compte(demo_url, tmp_path):
    """CT-95 : un compte créé avec une adresse unique ; la session « demo » est enregistrée."""
    return in_thread(lambda: run_case(demo_case("CT-95", demo_url), scenario("CT-95"), tmp_path / "ct95",
                                      sessions_dir=tmp_path / "sessions", env_path=tmp_path / ".env"))


def test_adresse_unique_pendant_l_execution(compte, tmp_path):
    assert compte.status == "PASS", compte.summary
    assert re.fullmatch(r"\d{10}", compte.unique)
    email = f"alfred.{compte.unique}@exemple.fr"
    typed = [step.action.args["text"] for step in compte.steps if step.action and step.action.name == "fill"]
    assert typed == ["Alfred", email]  # le scénario a écrit {unique} : l'agent a tapé la valeur de l'exécution
    report = compte.report_path.read_text(encoding="utf-8")
    assert f"| Valeur unique | `{compte.unique}` : remplace {{unique}} du cas de test" in report
    assert "| Session enregistrée | « demo »" in report and "| Navigateur | Chromium " in report
    resultat = json.loads((compte.out_dir / "resultat.json").read_text(encoding="utf-8"))
    assert resultat["valeur_unique"] == compte.unique and resultat["navigateur"] == "chromium"
    assert resultat["interruption"] is None
    session = json.loads((tmp_path / "sessions" / "demo.json").read_text(encoding="utf-8"))
    assert [cookie["name"] for cookie in session["cookies"]] == ["session_demo"]


def test_tests_generes_creent_leur_propre_valeur(compte, pw_browser, tmp_path):
    code = compte.script_path.read_text(encoding="utf-8")
    assert compte.unique not in code
    assert "unique = datetime.now().strftime('%m%d%H%M%S')" in code and "from datetime import datetime" in code
    assert ".fill(f\"alfred.{unique}@exemple.fr\")" in code
    assert "to_contain_text(f\"Compte créé pour alfred.{unique}@exemple.fr\", ignore_case=True" in code
    assert "page.context.storage_state(path=SESSIONS / 'demo.json')" in code
    namespace = {}
    exec(compile(code, "test_genere.py", "exec"), namespace)
    namespace["SESSIONS"] = tmp_path / "rejeu"  # au lieu du dossier sessions du projet
    run_on_new_page(pw_browser, namespace[f"test_{compte.case.slug}"])
    assert (tmp_path / "rejeu" / "demo.json").is_file()

    robot = generate_robot(compte)
    assert compte.unique not in robot
    assert "Test Setup        Créer la valeur unique\n" in robot
    for line in ("Input Text    css:#email    alfred.${UNIQUE}@exemple.fr",
                 "La page affiche    Compte créé pour alfred.${UNIQUE}@exemple.fr", "Enregistrer la session    demo"):
        assert f"    {line}\n" in robot, line
    result = run_robot(robot, tmp_path / "classique", cwd=tmp_path / "classique")
    assert result.returncode == 0, result.stdout
    saved = json.loads((tmp_path / "classique" / "sessions" / "demo.json").read_text(encoding="utf-8"))
    assert saved["cookies"][0]["name"] == "session_demo" and saved["origins"][0]["localStorage"]

    feature = generate_feature(compte)
    assert compte.unique not in feature and "# {unique} : une valeur nouvelle à chaque lancement" in feature
    assert '    Et je saisis "alfred.{unique}@exemple.fr" dans le champ "E-mail"\n' in feature
    assert '    Quand j\'enregistre la session "demo"\n' in feature
    bdd = generate_bdd_robot(compte)
    assert '    ${texte}=    Evaluate    $texte.replace("{unique}", $UNIQUE)\n' in bdd
    result = run_robot(bdd, tmp_path / "gherkin", cwd=tmp_path / "gherkin")
    assert result.returncode == 0, result.stdout


def test_la_memoire_garde_unique(pw_browser, demo_url, tmp_path, monkeypatch):
    """Le parcours mémorisé garde {unique} : le rejeu, sans IA, crée sa propre valeur."""
    valeurs = iter(["1111111111", "2222222222"])
    monkeypatch.setattr("agent.runner.valeur_unique", lambda: next(valeurs))
    memory = Memory(tmp_path / "memoire")
    steps = json.loads((DEMO / "scenario_CT-95.json").read_text(encoding="utf-8"))
    first = run_on_new_page(pw_browser, lambda page: Agent(FauxGemini(steps), memory=memory).run(
        page, demo_case("CT-95", demo_url), tmp_path / "r1"))
    assert first.status == "PASS" and first.memory_saved and first.unique == "1111111111"
    saved = memory.path(first.case).read_text(encoding="utf-8")
    assert "alfred.{unique}@exemple.fr" in saved and "1111111111" not in saved
    replay = run_on_new_page(pw_browser, lambda page: Agent(FauxGemini([]), memory=memory).run(
        page, demo_case("CT-95", demo_url), tmp_path / "r2"))
    assert replay.status == "PASS" and replay.model_calls == 0
    typed = [step.action.args["text"] for step in replay.steps if step.action and step.action.name == "fill"]
    assert typed[-1] == "alfred.2222222222@exemple.fr"


# --- Sessions : ne se connecter qu'une fois -------------------------------------------------


def test_lignes_de_session():
    case = parse_test_case("# CT-50 Cas\nURL : https://a.fr\nSession : saucedemo\nEnregistrer la session : autre\n")
    assert (case.session, case.save_session) == ("saucedemo", "autre")
    assert case.with_url("https://b.fr").session == "saucedemo"
    assert parse_test_case("# CT-51 Cas\nURL : https://a.fr\n").session is None
    with pytest.raises(ValueError, match="Nom de session invalide"):
        parse_test_case("# CT-52 Cas\nURL : https://a.fr\nSession : ../../etc\n")
    feature = ("# language: fr\n# URL : https://a.fr\n# Session : demo\nFonctionnalité: F\n\n  Scénario: S\n"
               "    Quand je clique sur Valider\n    Alors la page affiche \"OK\"\n")
    scenarios = feature_cases(feature)
    assert scenarios[0].session == "demo" and "\nSession : demo\n" in scenarios[0].text


def test_session_absente(demo_url, tmp_path):
    """Sans la session, le cas s'arrête dès l'ouverture : à relancer, sans fiche d'anomalie."""
    result = in_thread(lambda: run_case(demo_case("CT-96", demo_url), scenario("CT-96"), tmp_path / "ct96",
                                        sessions_dir=tmp_path / "vide", env_path=tmp_path / ".env"))
    assert result.status == "BLOQUE" and result.interruption == "session"
    assert "lance d'abord le cas qui contient la ligne « Enregistrer la session : demo »" in result.summary
    assert result.anomaly_path is None and len(result.steps) == 1
    resultat = json.loads((result.out_dir / "resultat.json").read_text(encoding="utf-8"))
    assert resultat["interruption"] == "session"
    execution = Execution(dossier="x", code="CT-96", statut="BLOQUE", conclusion=result.summary, interruption="session")
    assert execution.interrompue  # dans le bilan de campagne : à relancer, pas un verdict de l'agent


def test_session_reprise(compte, demo_url, tmp_path):
    espace = in_thread(lambda: run_case(demo_case("CT-96", demo_url), scenario("CT-96"), tmp_path / "ct96",
                                        sessions_dir=tmp_path / "sessions", env_path=tmp_path / ".env"))
    assert espace.status == "PASS", espace.summary
    assert "| Session | « demo » reprise : le test commence déjà connecté |" in espace.report_path.read_text(encoding="utf-8")

    code = generate_script(espace)
    assert "def browser_context_args(browser_context_args):" in code
    assert "    session = SESSIONS / 'demo.json'\n" in code and '"storage_state": str(session)' in code
    assert "pytest.skip(\"Session « demo » absente du dossier sessions" in code

    robot = generate_robot(espace)
    assert "    Ouvrir le navigateur    " in robot and "    Reprendre la session    demo\n" in robot
    feature = generate_feature(espace)
    assert "    Et je reprends la session \"demo\"\n" in feature
    sans_session = run_robot(robot, tmp_path / "sans", cwd=tmp_path / "sans")
    assert sans_session.returncode == 0 and "1 skipped" in sans_session.stdout  # pas de session : test passé
    for folder in ("classique", "gherkin"):
        (tmp_path / folder / "sessions").mkdir(parents=True)
        shutil.copy(tmp_path / "sessions" / "demo.json", tmp_path / folder / "sessions")
    result = run_robot(robot, tmp_path / "classique", cwd=tmp_path / "classique")
    assert result.returncode == 0 and "1 passed" in result.stdout, result.stdout
    result = run_robot(generate_bdd_robot(espace), tmp_path / "gherkin", cwd=tmp_path / "gherkin")
    assert result.returncode == 0 and "1 passed" in result.stdout, result.stdout


def _pytest_plugins() -> list[str]:
    """Options pour charger pytest-playwright dans un autre processus, s'il n'est pas déclaré à l'installation."""
    declared = {entry.value for entry in importlib.metadata.entry_points(group="pytest11")}
    if "pytest_playwright.pytest_playwright" in declared:
        return []
    return ["-p", "pytest_base_url.plugin", "-p", "pytest_playwright.pytest_playwright"]


def test_tests_pytest_avec_pytest_playwright(compte, demo_url, tmp_path):
    """Les tests générés, lancés comme le testeur les lance : pytest et pytest-playwright, depuis la racine du projet."""
    if importlib.util.find_spec("pytest_playwright") is None:
        pytest.skip("pytest-playwright n'est pas installé ici (il l'est dans GitHub Actions)")
    espace = in_thread(lambda: run_case(demo_case("CT-96", demo_url), scenario("CT-96"), tmp_path / "ct96",
                                        sessions_dir=tmp_path / "sessions", env_path=tmp_path / ".env"))
    projet = tmp_path / "projet"
    projet.mkdir()
    shutil.copy(compte.script_path, projet)
    shutil.copy(espace.script_path, projet)
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *_pytest_plugins(), "-rs", "."]
    result = subprocess.run(command, cwd=projet, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0 and "2 passed" in result.stdout, result.stdout + result.stderr  # CT-95, puis CT-96
    assert (projet / "sessions" / "demo.json").is_file()


# --- Localisateurs recommandés par Playwright -----------------------------------------------


def element(tag, type_=None, text="", **attrs):
    return Element(ref=1, tag=tag, type=type_, text=text, attrs=attrs, selector="#x")


def test_role_implicite():
    assert implicit_role(element("input")) == "textbox"
    assert implicit_role(element("input", "password")) is None  # un mot de passe n'a pas de rôle ARIA
    assert implicit_role(element("input", "submit")) == "button"
    assert implicit_role(element("a")) == "link"
    assert implicit_role(element("select")) == "combobox"
    assert implicit_role(element("div", role="option")) == "option"
    assert implicit_role(element("div", role="presentation")) is None
    assert implicit_role(element("div")) is None


def test_localisateurs_verifies_dans_la_page(pw_page, demo_url):
    pw_page.goto(f"{demo_url}/index.html")
    elements = {e.selector: e for e in observe(pw_page).elements}
    assert recommend(pw_page, elements["#user-name"]) == "get_by_role('textbox', name='Identifiant', exact=True)"
    assert recommend(pw_page, elements["#password"]) == "get_by_placeholder('Mot de passe', exact=True)"
    assert recommend(pw_page, elements['[data-test="login-button"]']) == "get_by_role('button', name='Connexion', exact=True)"
    pw_page.goto(f"{demo_url}/inventory.html")
    buttons = [e for e in observe(pw_page).elements if e.text == "Ajouter au panier"]
    assert recommend(pw_page, buttons[1]) == "get_by_role('button', name='Ajouter au panier', exact=True).nth(1)"


def test_attribut_de_test_plutot_qu_un_rang(pw_page, demo_url):
    """Trois boutons « Voir le détail » : le deuxième garde son attribut de test, pas son rang, qui change avec la liste."""
    pw_page.goto(f"{demo_url}/catalogue.html")
    expect(pw_page.locator("#etat")).to_have_text("3 produits")
    details = [e for e in observe(pw_page).elements if e.text == "Voir le détail"]
    assert [e.selector for e in details] == [f'[data-test="detail-{numero}"]' for numero in (1, 2, 3)]
    assert recommend(pw_page, details[1]) is None
    assert locator_code(details[1]) == "page.locator('[data-test=\"detail-2\"]')"


def test_sans_localisateur_recommande_le_selecteur_reste():
    champ = Element(ref=1, tag="div", type=None, text="", attrs={"field": True}, selector="div.liste", nth=2)
    assert locator_code(champ) == "page.locator('div.liste').nth(2)"
    champ.playwright = "get_by_role('combobox', name='Rôle', exact=True)"
    champ.frame = [{"css": "#cadre", "nth": None, "label": "cadre"}]
    assert locator_code(champ) == "page.frame_locator('#cadre').get_by_role('combobox', name='Rôle', exact=True)"


# --- Firefox, WebKit et appareils mobiles ---------------------------------------------------


def test_choix_du_navigateur_et_de_l_appareil():
    assert choisir(None, None) == ("chromium", None)
    assert choisir("WebKit", "iphone  15") == ("webkit", "iPhone 15")
    assert choisir("firefox", "Desktop Firefox") == ("firefox", "Desktop Firefox")
    with pytest.raises(ValueError, match="navigateur inconnu"):
        choisir("edge", None)
    with pytest.raises(ValueError, match="appareil inconnu .* Exemples"):
        choisir("chromium", "Nokia 3310")
    with pytest.raises(ValueError, match="Firefox ne sait pas émuler un téléphone"):
        choisir("firefox", "Pixel 7")


def test_liste_des_appareils_demandee_a_playwright(monkeypatch):
    """Une version de Playwright qui range sa liste d'appareils ailleurs : Playwright la donne lui-même."""
    monkeypatch.setattr(navigateur, "_from_package", lambda: {})
    navigateur.appareils.cache_clear()
    try:
        assert navigateur.appareils()["iPhone 15"]["is_mobile"] is True
        assert choisir("webkit", "iphone  15") == ("webkit", "iPhone 15")
        with pytest.raises(ValueError, match="appareil inconnu"):
            choisir("chromium", "Nokia 3310")
    finally:
        navigateur.appareils.cache_clear()


def test_navigateur_non_installe():
    class Absent:
        def launch(self, headless):
            raise PlaywrightError("BrowserType.launch: Executable doesn't exist at /ms-playwright/firefox/firefox")

    with pytest.raises(NavigateurAbsent, match="python -m playwright install firefox"):
        lancer(SimpleNamespace(firefox=Absent()), "firefox")


def test_ligne_de_commande_refuse_un_appareil_inconnu(tmp_path, capsys, demo_url):
    cas = tmp_path / "cas.md"
    cas.write_text(demo_text("CT-00", demo_url), encoding="utf-8")
    code = agent_main([str(cas), "--provider", "scripted", "--scenario", str(DEMO / "scenario_CT-00.json"),
                       "--appareil", "Nokia 3310"])
    assert code == 3 and "appareil inconnu : « Nokia 3310 »" in capsys.readouterr().err


def test_iphone_emule(demo_url, tmp_path):
    result = in_thread(lambda: run_case(demo_case("CT-00", demo_url), scenario("CT-00"), tmp_path / "iphone",
                                        appareil="iPhone 15", sessions_dir=tmp_path / "sessions",
                                        env_path=tmp_path / ".env"))
    assert result.status == "PASS", result.summary
    assert result.appareil == "iPhone 15" and result.browser.startswith("Chromium ") and result.browser.endswith(
        ", iPhone 15 émulé")
    capture = (result.out_dir / "captures" / "etape-01.png").read_bytes()
    assert int.from_bytes(capture[16:20], "big") == 393 * 3  # largeur de l'écran de l'iPhone 15, en pixels réels
    code = generate_script(result)
    assert "def browser_context_args(browser_context_args, playwright):" in code
    assert "return {**browser_context_args, **playwright.devices['iPhone 15']}" in code
    robot = generate_robot(result)
    assert "${BROWSER}        chrome" in robot and 'add_experimental_option("mobileEmulation", ' in robot
    assert "'deviceMetrics': {'width': 393, 'height': 659, 'pixelRatio': 3.0, 'touch': True}" in robot
    assert "    Set Window Size    1280    800\n" not in robot  # la taille vient de l'appareil émulé
    result.navigateur, result.appareil = "firefox", None
    robot = generate_robot(result)
    assert "# Même navigateur que l'agent : Firefox.\n${BROWSER}        firefox" in robot
    assert "Lancement : pytest test_" in generate_script(result) and "--browser firefox" in generate_script(result)


# --- Campagne en une commande, avec reprise après le quota ----------------------------------


def _mini_campagne(root: Path, demo_url: str) -> None:
    cas = root / "cas"
    cas.mkdir()
    for code in ("CT-00", "CT-95", "CT-96"):
        source = next(DEMO.glob(f"{code}_*.md"))
        (cas / source.name).write_text(demo_text(code, demo_url), encoding="utf-8")
        shutil.copy(DEMO / f"scenario_{code}.json", cas)
    (cas / "CT-97_quota.md").write_text(f"# CT-97 Quota épuisé au milieu\nURL : {demo_url}/index.html\n\n## Étapes\n"
                                        "1. Vérifier que « Boutique Démo » s'affiche\n", encoding="utf-8")
    (cas / "scenario_CT-97.json").write_text(json.dumps([{"error": "rate_limit", "daily": True}]), encoding="utf-8")
    lignes = ["| Cas | Site | Type | Vision | Verdict attendu | Raison |", "|---|---|---|---|---|---|"]
    for code, name in (("CT-00", "CT-00_demo_locale.md"), ("CT-95", "CT-95_demo_compte_adresse_unique.md"),
                       ("CT-97", "CT-97_quota.md"), ("CT-96", "CT-96_demo_espace_client_sans_connexion.md")):
        lignes.append(f"| [{code}](cas/{name}) | Démo | Parcours normal | | PASS | Raison. |")
    (root / "campagne.md").write_text("# Mini campagne\n\n" + "\n".join(lignes) + "\n", encoding="utf-8")


def test_campagne_en_une_commande(demo_url, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # comme le testeur : depuis la racine du projet (rapports, sessions)
    _mini_campagne(tmp_path, demo_url)
    options = ["--campagne", "campagne.md", "--provider", "scripted"]

    assert in_thread(lambda: campagne.main(options)) == campagne.EXIT_QUOTA
    out = capsys.readouterr().out
    assert "Début       : campagne commencée le" in out and "0 cas déjà faits, 4 à lancer" in out
    assert "[1/4] CT-00 Démo" in out and "PASS (attendu PASS) ✅ juste, démo sans IA" in out
    assert "Quota du jour épuisé : la campagne s'arrête ici. Elle reprendra à CT-97" in out
    assert "CT-96  Démo  attendu PASS    obtenu -" in out  # pas encore lancé
    assert (tmp_path / "rapports" / campagne.ETAT).is_file()

    (tmp_path / "cas" / "scenario_CT-97.json").write_text(json.dumps([  # le quota est revenu
        {"action": "check_text", "args": {"text": "Boutique Démo"}},
        {"action": "finish", "args": {"status": "PASS", "summary": "La page s'affiche."}}]), encoding="utf-8")
    assert in_thread(lambda: campagne.main(options)) == 0
    out = capsys.readouterr().out
    assert "Reprise     : campagne commencée le" in out and "2 cas déjà faits, 2 à lancer" in out
    assert "[1/4] CT-00 Démo : déjà fait dans cette campagne (PASS" in out
    assert "[4/4] CT-96 Démo" in out  # la session enregistrée par CT-95 au premier lancement sert ici
    assert "Verdicts justes  : 4 sur 4 (100 %)" in out
    assert not (tmp_path / "rapports" / campagne.ETAT).exists()  # campagne terminée : la suivante repartira de zéro
    assert "| Cas exécutés | 4 sur 4 |" in (tmp_path / "rapports" / "bilan_campagne.md").read_text(encoding="utf-8")


def test_campagne_attend_le_retour_du_quota(demo_url, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _mini_campagne(tmp_path, demo_url)
    reponses = iter([SimpleNamespace(interruption="quota_jour", status="BLOQUE", summary="quota", model_calls=0,
                                     duration_s=0.0, provider="gemini")]
                    + [SimpleNamespace(interruption=None, status="PASS", summary="ok", model_calls=3, duration_s=2.0,
                                       provider="gemini")] * 4)
    appels = []
    monkeypatch.setattr(campagne, "lancer_cas", lambda args, cas, path, nav, app: appels.append(cas.code) or next(reponses))
    monkeypatch.setattr(campagne, "seconds_until_reset", lambda: 0)
    monkeypatch.setattr(campagne.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(campagne, "make_provider", lambda *args, **kwargs: None)
    assert campagne.main(["--campagne", "campagne.md", "--provider", "gemini", "--attendre"]) == 0
    assert appels == ["CT-00", "CT-00", "CT-95", "CT-97", "CT-96"]  # CT-00 relancé après le retour du quota
    assert "Quota du jour épuisé : reprise automatique" in capsys.readouterr().out


def test_debut_de_campagne(tmp_path):
    rapports, fichier = tmp_path / "rapports", Path("campagne.md")
    debut, reprise = campagne.debut_campagne(rapports, fichier, None, maintenant=datetime(2026, 10, 23, 9, 0, 1))
    assert (debut, reprise) == (datetime(2026, 10, 23, 9, 0, 1), False)
    assert campagne.debut_campagne(rapports, fichier, None) == (debut, True)  # même campagne : reprise
    autre, reprise = campagne.debut_campagne(rapports, fichier, "parabank")  # un autre site : une autre campagne
    assert not reprise and autre != debut
    nouvelle, reprise = campagne.debut_campagne(rapports, fichier, "parabank", nouvelle=True)
    assert not reprise
    assert campagne.seconds_until_reset(datetime(2026, 10, 23, 6, 59, 0, tzinfo=campagne.timezone.utc)) == 60 + 120


def test_bilan_depuis_le_debut_de_la_campagne():
    texte = "| Cas | Site | Type | Verdict attendu | Raison |\n|---|---|---|---|---|\n| CT-17 | A | Normal | PASS | R |\n"
    executions = [Execution("a", "CT-17", "FAIL", debut=datetime(2026, 10, 23, 8, 0)),
                  Execution("b", "CT-17", "PASS", debut=datetime(2026, 10, 23, 10, 0))]
    lignes = comparer(lire_campagne(texte), executions, depuis=datetime(2026, 10, 23, 9, 0))
    assert lignes[0].execution.dossier == "b" and lignes[0].executions == 1


# --- Relance des tests en échec avec Alfred -------------------------------------------------


def test_cas_en_echec():
    cases = [{"title": t} for t in ("CT-01 A", "CT-02 B", "CT-03 C", "CT-04 D", "CT-05 E")]
    results = [  # du plus récent au plus ancien
        {"titre": "CT-01 A", "statut": "PASS"}, {"titre": "CT-01 A", "statut": "FAIL"},
        {"titre": "CT-02 B", "statut": "FAIL"},
        {"titre": "CT-03 C", "statut": "BLOQUE", "interruption": "quota_jour"},
        {"titre": "CT-04 D", "statut": "BLOQUE", "interruption": "arret"},
    ]
    assert [c["title"] for c in failed_cases(cases, results)] == ["CT-02 B", "CT-03 C"]


def test_phrases_de_la_relance():
    assert narration.relaunch_speech(["CT-06 Un", "CT-07 Deux"]) == \
        "Je relance les 2 tests en échec, l'un après l'autre : CT-06 et CT-07."
    assert narration.relaunch_speech(["CT-06 Un"], skipped=1) == \
        "Je relance le test en échec : CT-06. 1 autre n'a pas de démo sans IA."
    assert narration.series_done_speech([("CT-06 Un", "FAIL"), ("CT-07 Deux", "PASS")], 2) == \
        "Relance terminée. 1 réussi sur 2. Toujours en échec : CT-06."
    assert narration.series_done_speech([("CT-06 Un", "PASS")], 3, stopped=True) == \
        "Relance arrêtée après 1 test sur 3. 1 réussi sur 1."


def test_alfred_comprend_relance_et_navigateur(tmp_path):
    cases = [{"id": "CT-01_a.md", "code": "CT-01", "title": "CT-01 A"}]
    assert check_action({"type": "relancer_echecs"}, cases, tmp_path, "https://a.fr") == ({"type": "relancer_echecs"}, "")
    action, correction = check_action({"type": "lancer", "cas": "CT-01", "navigateur": "Safari", "appareil": "iphone 15"},
                                      cases, tmp_path, "https://a.fr")
    assert action == {"type": "lancer", "cas": "CT-01_a.md", "code": "CT-01", "navigateur": "webkit", "appareil": "iPhone 15"}
    action, correction = check_action({"type": "lancer", "cas": "CT-01", "appareil": "Nokia 3310"}, cases, tmp_path, "x")
    assert action == {"type": "aucune"} and "Nokia 3310" in correction


ECHEC = [{"action": "check_text", "args": {"text": "Texte absent de la page"}},
         {"action": "finish", "args": {"status": "FAIL", "summary": "Le texte attendu manque"}}]


def _cas_en_echec(alfred, demo_url) -> None:
    """Deux cas qui échouent (CT-06 et CT-07), lancés une fois chacun, et CT-00 qui réussit."""
    for code in ("CT-06", "CT-07"):
        (alfred.cases_dir / f"{code}_echec.md").write_text(f"# {code} Échec\nURL : {demo_url}/index.html\n", encoding="utf-8")
        (alfred.cases_dir / f"scenario_{code}.json").write_text(json.dumps(ECHEC), encoding="utf-8")
    for case in ("CT-06_echec.md", "CT-07_echec.md", "demo/CT-00_demo_locale.md"):
        data = request(alfred, "POST", "/api/missions", {"case": case, "provider": "scripted"})[1]
        read_events(alfred, data["id"])


def test_relance_des_tests_en_echec(alfred, demo_url):
    status, data = request(alfred, "POST", "/api/relance", {"provider": "scripted"})
    assert status == 200 and data["cas"] == [] and data["say"].startswith("Aucun test en échec")
    _cas_en_echec(alfred, demo_url)

    status, data = request(alfred, "POST", "/api/relance", {"provider": "scripted"})
    assert status == 201, data
    assert data["cas"] == ["CT-06 Échec", "CT-07 Échec"]
    assert data["say"] == "Je relance les 2 tests en échec, l'un après l'autre : CT-06 et CT-07."
    first = read_events(alfred, data["id"])
    assert first[0]["serie"] == {"position": 1, "total": 2} and first[0]["say"].startswith("Test 1 sur 2.")
    done = first[-1]
    assert done["status"] == "FAIL" and done["serie"]["suivante"] and done["say"].endswith(
        "Je passe au test suivant, 2 sur 2.")
    second = read_events(alfred, done["serie"]["suivante"])
    assert second[0]["title"] == "CT-07 Échec"
    end = second[-1]
    assert end["serie"]["suivante"] is None and end["serie"]["bilan"] == {"lances": 2, "reussis": 0}
    assert end["say"].endswith("Relance terminée. 0 réussi sur 2. Toujours en échec : CT-06 et CT-07.")


def test_relance_arretee(alfred, demo_url):
    _cas_en_echec(alfred, demo_url)
    data = request(alfred, "POST", "/api/relance", {"provider": "scripted", "pause": 3})[1]

    def stop(event):
        if event["type"] == "step" and event["number"] == 1:
            assert request(alfred, "POST", f"/api/missions/{data['id']}/stop", {})[0] == 202

    done = read_events(alfred, data["id"], on_event=stop)[-1]
    assert done["stopped"] and done["serie"]["suivante"] is None
    assert "Relance arrêtée après 1 test sur 2." in done["say"]


@pytest.mark.parametrize("corps, message", [
    ({"navigateur": "edge"}, "Navigateur inconnu"),
    ({"appareil": "Nokia 3310"}, "Appareil inconnu"),
    ({"navigateur": "firefox", "appareil": "iPhone 15"}, "Firefox ne sait pas émuler"),
])
def test_reglages_de_mission_refuses(alfred, corps, message):
    status, data = request(alfred, "POST", "/api/missions", {"case": "demo/CT-00_demo_locale.md", "provider": "scripted",
                                                              **corps})
    assert status == 400 and message in data["error"]


def test_interface_relance_et_navigateur(alfred, pw_page, demo_url):
    _cas_en_echec(alfred, demo_url)
    pw_page.goto(alfred.url)
    expect(pw_page.locator(".carte-cas")).to_have_count(5)
    for phrase in ("Alfred, relance les tests en échec", "rejoue les tests qui ont échoué",
                   "relance tous les tests en échec d'aujourd'hui sur la recette"):
        assert pw_page.evaluate("(t) => Alfred.comprendre(t)", phrase) == {"type": "relancer_echecs"}, phrase
    assert pw_page.evaluate("(t) => Alfred.comprendre(t)", "relance le cas 3") == {"type": "lancer", "code": "CT-03"}

    expect(pw_page.locator("#option-navigateur option")).to_have_count(3)
    pw_page.evaluate("() => Alfred.executerAction({type: 'selectionner', cas: 'CT-06_echec.md', navigateur: 'firefox', "
                     "appareil: 'iPhone 13'})")
    expect(pw_page.locator("#option-navigateur")).to_have_value("firefox")
    expect(pw_page.locator("#option-appareil")).to_have_value("iPhone 13")  # ajouté à la liste
    pw_page.select_option("#option-navigateur", "chromium")
    pw_page.select_option("#option-appareil", "")

    pw_page.check('input[name="moteur"][value="scripted"]')
    pw_page.fill("#option-pause", "0")
    pw_page.evaluate("() => Alfred.executerAction({type: 'relancer_echecs'})")
    expect(pw_page.locator(".note-journal").first).to_contain_text("test 2 sur 2", timeout=30_000)
    expect(pw_page.locator(".verdict-mot")).to_have_text("Échec", timeout=30_000)
    expect(pw_page.locator("#voix")).to_contain_text("Relance terminée", timeout=10_000)
