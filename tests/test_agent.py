"""Exécutions complètes de l'agent avec le faux modèle, sur le site de démo."""

import threading
import time

from agent.codegen import generate_script, write_script
from agent.providers import ScriptedProvider
from agent.report import write_report
from agent.runner import STOPPED_SUMMARY, Agent
from agent.testcase import parse_test_case

LOGIN_OK = [
    {"action": "fill", "target": "#user-name", "args": {"text": "standard_user"}},
    {"action": "fill", "target": "#password", "args": {"text": "secret_sauce"}},
    {"action": "click", "target": '[data-test="login-button"]'},
    {"action": "check_url", "args": {"contains": "inventory"}},
    {"action": "check_text", "args": {"text": "Produits"}},
    {"action": "click", "target": 'button:has-text("Ajouter au panier")'},
    {"action": "select", "target": '[data-test="sort"]', "args": {"value": "Prix croissant"}},
    {"action": "check_text", "args": {"text": "Panier (1)"}},
    {"action": "finish", "args": {"status": "PASS", "summary": "Connexion et ajout au panier réussis"}},
]


def make_case(demo_url, title="CT-01 Connexion et panier"):
    return parse_test_case(f"# {title}\nURL : {demo_url}/index.html\n\n## Étapes\n1. ...")


def run(pw_page, demo_url, scenario, tmp_path, title="CT-01 Connexion et panier"):
    agent = Agent(ScriptedProvider(scenario))
    return agent.run(pw_page, make_case(demo_url, title), tmp_path / "rapport")


def test_parcours_reussi(pw_page, demo_url, tmp_path):
    result = run(pw_page, demo_url, LOGIN_OK, tmp_path)
    assert result.status == "PASS", result.summary
    assert len(result.steps) == 9  # ouverture de l'URL + 8 actions du scénario
    assert all(step.result.ok for step in result.steps)
    assert (result.out_dir / "captures" / "etape-01.png").exists()

    write_script(result)
    write_report(result)
    report = result.report_path.read_text(encoding="utf-8")
    assert "✅ PASS" in report and "etape-03.png" in report and result.script_path.name in report


def test_script_genere_rejoue_le_parcours_sans_ia(pw_page, pw_browser, demo_url, tmp_path):
    """Le script pytest généré doit passer tout seul, sur une page neuve."""
    result = run(pw_page, demo_url, LOGIN_OK, tmp_path)
    code = generate_script(result)
    assert "page.locator('button:has-text(\"Ajouter au panier\")').nth(0).click()" in code

    namespace = {}
    exec(compile(code, "test_genere.py", "exec"), namespace)
    test_function = namespace["test_ct_01_connexion_et_panier"]
    context = pw_browser.new_context()
    try:
        test_function(context.new_page())  # lève AssertionError si le script ne rejoue pas le test
    finally:
        context.close()


def test_mauvais_mot_de_passe_donne_fail(pw_page, demo_url, tmp_path):
    scenario = [
        {"action": "fill", "target": "#user-name", "args": {"text": "standard_user"}},
        {"action": "fill", "target": "#password", "args": {"text": "mauvais"}},
        {"action": "click", "target": '[data-test="login-button"]'},
        {"action": "check_text", "args": {"text": "Produits"}},
        {"action": "finish", "args": {"status": "PASS", "summary": "Le modèle se trompe et conclut PASS"}},
    ]
    result = run(pw_page, demo_url, scenario, tmp_path, title="CT-02 Mot de passe incorrect")
    assert result.status == "FAIL"  # la vérification échouée l'emporte sur l'avis du modèle
    assert "vérification a échoué" in result.summary


def test_pass_sans_verification_refuse(pw_page, demo_url, tmp_path):
    scenario = [{"action": "finish", "args": {"status": "PASS", "summary": "Tout va bien"}}]
    result = run(pw_page, demo_url, scenario, tmp_path)
    assert result.status == "BLOQUE"
    assert "sans aucune vérification" in result.summary


def test_erreurs_a_la_suite_bloquent(pw_page, demo_url, tmp_path):
    scenario = [
        {"raw": "Je ne sais pas quoi faire."},
        {"action": "click", "target": "#bouton-inexistant"},
        {"raw": '{"action": "voler"}'},
    ]
    result = run(pw_page, demo_url, scenario, tmp_path)
    assert result.status == "BLOQUE"
    assert "erreurs de suite" in result.summary
    assert result.steps[1].action is None  # réponse illisible enregistrée telle quelle


def test_limite_du_nombre_d_actions(pw_page, demo_url, tmp_path):
    scenario = [{"action": "check_text", "args": {"text": "Boutique"}}] * 10
    agent = Agent(ScriptedProvider(scenario), max_steps=4)
    result = agent.run(pw_page, make_case(demo_url), tmp_path / "rapport")
    assert result.status == "BLOQUE"
    assert len(result.steps) == 4


def test_cas_negatif_connexion_refusee(pw_page, pw_browser, demo_url, tmp_path):
    """Résultat attendu négatif : l'utilisateur reste sur la page de connexion."""
    scenario = [
        {"action": "fill", "target": "#user-name", "args": {"text": "standard_user"}},
        {"action": "fill", "target": "#password", "args": {"text": "mauvais"}},
        {"action": "click", "target": '[data-test="login-button"]'},
        {"action": "check_text", "args": {"text": "mot de passe incorrect"}},
        {"action": "check_url", "args": {"contains": "inventory", "absent": True}},
        {"action": "finish", "args": {"status": "PASS", "summary": "Connexion refusée, comme attendu"}},
    ]
    result = run(pw_page, demo_url, scenario, tmp_path, title="CT-02 Connexion refusée")
    assert result.status == "PASS", result.summary

    code = generate_script(result)
    assert "not_to_have_url" in code
    namespace = {}
    exec(compile(code, "test_genere.py", "exec"), namespace)
    context = pw_browser.new_context()
    try:
        namespace["test_ct_02_connexion_refusee"](context.new_page())
    finally:
        context.close()


def test_evenements_pour_une_interface(pw_page, demo_url, tmp_path):
    """on_event reçoit, dans l'ordre, les phases de chaque tour puis l'étape réalisée."""
    events = []
    agent = Agent(ScriptedProvider(LOGIN_OK[:1] + [LOGIN_OK[-1]]), on_event=lambda kind, data: events.append((kind, data)))
    result = agent.run(pw_page, make_case(demo_url), tmp_path / "rapport")

    kinds = [kind if kind == "step" else data["phase"] for kind, data in events]
    # ouverture de l'URL, puis un tour complet pour la saisie, puis le tour du finish
    assert kinds == ["act", "step", "observe", "think", "act", "step", "observe", "think"]
    steps = [data["step"] for kind, data in events if kind == "step"]
    assert steps == result.steps
    act = next(data for kind, data in events if kind == "phase" and data["phase"] == "act" and data["number"] == 2)
    assert act["action"].name == "fill" and act["observation"] is not None


def test_arret_demande(pw_page, demo_url, tmp_path):
    """Un arrêt demandé pendant la mission la termine proprement, au statut BLOQUÉ."""
    stop = threading.Event()

    def on_event(kind, data):
        if kind == "step" and data["step"].number == 2:
            stop.set()  # l'utilisateur clique sur « Arrêter » après la première saisie

    agent = Agent(ScriptedProvider(LOGIN_OK), pause_s=5, on_event=on_event, stop_event=stop)
    started = time.monotonic()
    result = agent.run(pw_page, make_case(demo_url), tmp_path / "rapport")
    assert time.monotonic() - started < 4  # la pause de 5 s est interrompue
    assert result.status == "BLOQUE" and result.summary == STOPPED_SUMMARY
    assert len(result.steps) == 2
