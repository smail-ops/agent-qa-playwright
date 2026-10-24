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
    # Localisateurs recommandés par Playwright (rôle et nom, texte d'aide), vérifiés dans la page pendant l'exécution
    for line in ("page.get_by_role('textbox', name='Identifiant', exact=True).fill('standard_user')",
                 "page.get_by_placeholder('Mot de passe', exact=True).fill('secret_sauce')",
                 "page.get_by_role('button', name='Connexion', exact=True).click()",
                 # trois boutons identiques : le premier, comme pendant l'exécution
                 "page.get_by_role('button', name='Ajouter au panier', exact=True).nth(0).click()",
                 # la liste n'a pas de nom accessible exact : le sélecteur stable reste
                 "page.locator('[data-test=\"sort\"]').select_option('Prix croissant')"):
        assert line in code, line

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


QUOTA_MINUTE = {"error": "rate_limit", "retry_after": 0.2}


def test_quota_par_minute_l_agent_patiente(pw_page, demo_url, tmp_path):
    """L'API dit « réessaie dans X secondes » : l'agent patiente, le signale, puis reprend."""
    events = []
    scenario = [QUOTA_MINUTE] + LOGIN_OK
    agent = Agent(ScriptedProvider(scenario), on_event=lambda kind, data: events.append((kind, data)))
    result = agent.run(pw_page, make_case(demo_url), tmp_path / "rapport")
    assert result.status == "PASS", result.summary
    waits = [data for kind, data in events if kind == "wait"]
    assert waits == [{"seconds": 0.2, "attempt": 1, "max_attempts": 3}]


def test_quota_du_jour_arret_immediat(pw_page, demo_url, tmp_path):
    events = []
    agent = Agent(ScriptedProvider([{"error": "rate_limit", "daily": True}]), on_event=lambda k, d: events.append(k))
    result = agent.run(pw_page, make_case(demo_url), tmp_path / "rapport")
    assert result.status == "BLOQUE" and "quota du jour épuisé" in result.summary
    assert "wait" not in events  # attendre quelques secondes ne servirait à rien


def test_trop_de_pauses(pw_page, demo_url, tmp_path):
    scenario = [{"error": "rate_limit", "retry_after": 0.05}] * 4
    result = Agent(ScriptedProvider(scenario)).run(pw_page, make_case(demo_url), tmp_path / "rapport")
    assert result.status == "BLOQUE" and "limite de requêtes atteinte" in result.summary


def test_arret_pendant_une_pause_de_quota(pw_page, demo_url, tmp_path):
    stop = threading.Event()
    agent = Agent(ScriptedProvider([{"error": "rate_limit", "retry_after": 30}] + LOGIN_OK),
                  on_event=lambda kind, data: stop.set() if kind == "wait" else None, stop_event=stop)
    started = time.monotonic()
    result = agent.run(pw_page, make_case(demo_url), tmp_path / "rapport")
    assert time.monotonic() - started < 5  # la pause de 30 s est interrompue
    assert result.status == "BLOQUE" and result.summary == STOPPED_SUMMARY


# --- Vérifications plus riches, rejouées par le script généré -------------------------------

VERIFICATIONS_RICHES = [
    {"action": "fill", "target": "#user-name", "args": {"text": "standard_user"}},
    {"action": "check_value", "target": "#user-name", "args": {"value": "standard_user"}},
    {"action": "fill", "target": "#password", "args": {"text": "secret_sauce"}},
    {"action": "click", "target": '[data-test="login-button"]'},
    {"action": "check_count", "args": {"text": "Ajouter au panier", "count": 3}},
    {"action": "select", "target": '[data-test="sort"]', "args": {"value": "Prix croissant"}},
    {"action": "check_value", "target": '[data-test="sort"]', "args": {"value": "Prix croissant"}},
    {"action": "check_order", "args": {"texts": ["Lampe de vélo", "T-shirt", "Sac à dos"]}},
    {"action": "click", "target": 'button:has-text("Ajouter au panier")'},
    {"action": "check_count", "args": {"text": "Retirer", "count": 1}},
    {"action": "click", "target": '[data-test="checkout"]'},
    {"action": "check_state", "target": '[data-test="continuer"]', "args": {"state": "grisé"}},
    {"action": "check_state", "target": '[data-test="cgu"]', "args": {"state": "unchecked"}},
    {"action": "click", "target": '[data-test="cgu"]'},
    {"action": "check_state", "target": '[data-test="cgu"]', "args": {"state": "checked"}},
    {"action": "check_state", "target": '[data-test="continuer"]', "args": {"state": "enabled"}},
    {"action": "check_value", "target": "#code-postal", "args": {"value": ""}},
    {"action": "finish", "args": {"status": "PASS", "summary": "Tri, panier et formulaire conformes"}},
]


def test_verifications_riches_rejouees_sans_ia(pw_page, pw_browser, demo_url, tmp_path):
    result = run(pw_page, demo_url, VERIFICATIONS_RICHES, tmp_path, title="CT-20 Tri et commande")
    assert result.status == "PASS", result.summary
    assert len(result.checks) == 10 and all(step.result.check for step in result.checks)

    code = generate_script(result)
    assert "expect(page.get_by_role('textbox', name='Identifiant', exact=True)).to_have_value('standard_user')" in code
    assert "assert attendre(lambda: compte(page, 'Ajouter au panier') == 3)" in code
    assert "expect(page.locator('[data-test=\"sort\"]')).to_have_value('price')" in code  # valeur réelle de l'option
    assert "re.compile('Lampe\\\\s+de\\\\s+vélo.*T\\\\-shirt.*Sac\\\\s+à\\\\s+dos', re.IGNORECASE | re.DOTALL)" in code
    assert "expect(page.get_by_role('button', name='Continuer', exact=True)).to_be_disabled()" in code
    assert ("expect(page.get_by_role('checkbox', name=\"J'accepte les conditions générales de vente\", exact=True))"
            ".not_to_be_checked()") in code
    assert "expect(page.get_by_role('textbox', name='Code postal', exact=True)).to_have_value('')" in code
    assert "import time" in code and "def compte(" in code

    namespace = {}
    exec(compile(code, "test_genere.py", "exec"), namespace)
    context = pw_browser.new_context()
    try:
        namespace["test_ct_20_tri_et_commande"](context.new_page())
    finally:
        context.close()


def test_script_sans_comptage_reste_simple(pw_page, demo_url, tmp_path):
    """Les outils compte() et attendre() ne sont ajoutés que si le test compte des textes."""
    code = generate_script(run(pw_page, demo_url, LOGIN_OK, tmp_path))
    assert "def compte(" not in code and "import time" not in code


# --- Vision -------------------------------------------------------------------------------

class Regard(ScriptedProvider):
    """Faux modèle qui garde ce qu'il a reçu, pour vérifier ce que la vision lui envoie."""

    def __init__(self, steps):
        super().__init__(steps)
        self.recu = []

    def next_action(self, system, user, observation):
        self.recu.append((system, observation.screenshot))
        return super().next_action(system, user, observation)


def test_vision_envoie_la_capture(pw_page, demo_url, tmp_path):
    regard = Regard([{"action": "check_text", "args": {"text": "Boutique"}},
                     {"action": "finish", "args": {"status": "PASS", "summary": "ok"}}])
    result = Agent(regard, vision=True).run(pw_page, make_case(demo_url), tmp_path / "rapport")
    assert result.status == "PASS" and result.vision
    system, capture = regard.recu[0]
    assert "check_visual" in system and capture.startswith(b"\xff\xd8")  # une image JPEG à chaque tour

    sans = Regard([{"action": "finish", "args": {"status": "BLOQUE", "summary": "stop"}}])
    Agent(sans).run(pw_page, make_case(demo_url), tmp_path / "rapport2")
    assert sans.recu[0][1] is None and "check_visual" not in sans.recu[0][0]


VISUEL = [
    {"action": "check_text", "args": {"text": "Produits"}},
    {"action": "check_visual", "args": {"expected": "Chaque produit a sa propre image",
                                        "seen": "Aucune image de produit n'est affichée", "ok": False}},
    {"action": "finish", "args": {"status": "FAIL", "summary": "Les images des produits manquent."}},
]


def test_verification_visuelle(pw_page, pw_browser, demo_url, tmp_path):
    """L'avis du modèle sur la capture compte comme une vérification, signalée comme un avis de l'IA."""
    pw_page.goto(f"{demo_url}/inventory.html")
    case = parse_test_case(f"# CT-16 Images des produits\nURL : {demo_url}/inventory.html\n\n## Étapes\n1. ...")
    result = Agent(ScriptedProvider(VISUEL), vision=True).run(pw_page, case, tmp_path / "rapport")
    assert result.status == "FAIL"
    visual = result.steps[2].result
    assert visual.check is False and visual.by_ai and visual.detail.startswith("Avis de l'IA sur la capture d'écran (non conforme)")

    code = generate_script(result)
    assert "# À automatiser : vérification visuelle faite par l'IA (non conforme) : Chaque produit a sa propre image" in code
    namespace = {}
    exec(compile(code, "test_genere.py", "exec"), namespace)  # le reste du test se rejoue sans IA
    context = pw_browser.new_context()
    try:
        namespace["test_ct_16_images_des_produits"](context.new_page())
    finally:
        context.close()


def test_verification_visuelle_sans_vision(pw_page, demo_url, tmp_path):
    scenario = [VISUEL[1], {"action": "finish", "args": {"status": "PASS", "summary": "ok"}}]
    result = Agent(ScriptedProvider(scenario)).run(pw_page, make_case(demo_url), tmp_path / "rapport")
    assert not result.steps[1].result.ok and "demande la vision" in result.steps[1].result.detail


def test_pass_seulement_visuel_signale(pw_page, demo_url, tmp_path):
    scenario = [{"action": "check_visual", "args": {"expected": "Le logo s'affiche", "seen": "Le titre est visible", "ok": True}},
                {"action": "finish", "args": {"status": "PASS", "summary": "Page conforme."}}]
    result = Agent(ScriptedProvider(scenario), vision=True).run(pw_page, make_case(demo_url), tmp_path / "rapport")
    assert result.status == "PASS" and result.summary.startswith("PASS fondé uniquement sur l'avis visuel de l'IA")
