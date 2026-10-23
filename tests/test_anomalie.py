"""Analyse des échecs : fiche d'anomalie toujours rédigée, diagnostic du modèle quand il y en a un."""

import json

import pytest
from test_agent import make_case

from agent import browser
from agent.anomalie import build_anomaly, diagnose, expected_results, needs_analysis, parse_diagnosis, write_anomaly
from agent.providers import ProviderError, ScriptedProvider
from agent.report import build_report
from agent.runner import STOPPED_SUMMARY, Agent
from agent.testcase import parse_test_case

CASE = """# CT-04 Connexion avec un mot de passe incorrect
URL : {url}/index.html

## Étapes
1. Saisir « standard_user » dans le champ Identifiant
2. Saisir « mauvais » dans le champ Mot de passe
3. Cliquer sur Connexion

## Résultats attendus
- La page des produits s'affiche : le texte « Produits » est visible
- L'URL contient « inventory »
"""

ECHEC = [
    {"action": "fill", "target": "#user-name", "args": {"text": "standard_user"}},
    {"action": "fill", "target": "#password", "args": {"text": "mauvais"}},
    {"action": "click", "target": "#bouton-absent"},  # tentative ratée, corrigée ensuite
    {"action": "click", "target": '[data-test="login-button"]'},
    {"action": "check_text", "args": {"text": "Produits"}},
    {"action": "finish", "args": {"status": "FAIL", "summary": "La page des produits ne s'affiche pas."}},
]

DIAGNOSTIC = {"nature": "bug", "gravite": "Majeure", "titre": "La connexion échoue avec un utilisateur valide",
              "explication": "Le message d'erreur s'affiche après le clic sur Connexion.",
              "suggestion": "Vérifier le mot de passe de test, puis rejouer le test."}


class FauxModele:
    """Un vrai modèle pour l'analyse (la démo sans IA n'en demande pas), qui répond ce qu'on lui donne."""

    name, model = "gemini", "faux modèle"

    def __init__(self, reponse):
        self.reponse, self.recu = reponse, None

    def complete(self, system, user, images=(), max_tokens=None):
        self.recu, self.images = (system, user), list(images)
        if isinstance(self.reponse, Exception):
            raise self.reponse
        return self.reponse


@pytest.fixture
def echec(pw_page, demo_url, tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "CHECK_TIMEOUT_MS", 300)  # la vérification qui échoue n'attend pas 3 s
    case = parse_test_case(CASE.format(url=demo_url))
    return Agent(ScriptedProvider(ECHEC)).run(pw_page, case, tmp_path / "rapport")


def test_fiche_d_anomalie_sans_ia(echec, demo_url):
    """Démo sans IA : la fiche est complète, seul le diagnostic manque, et elle le dit."""
    assert echec.status == "FAIL" and needs_analysis(echec)
    diagnosis, missing = diagnose(echec, ScriptedProvider([]))
    assert diagnosis is None and "démo sans IA" in missing
    fiche = build_anomaly(echec, diagnosis, missing)

    assert fiche.startswith("# Fiche d'anomalie : Texte « Produits » absent de la page\n")
    assert "| Gravité proposée | À qualifier |" in fiche
    reproduire = fiche.split("## Étapes pour reproduire\n\n")[1].split("\n\n")[0]
    assert reproduire.splitlines() == [
        f"1. Ouvrir {demo_url}/index.html",
        "2. Saisir « standard_user » dans le champ « Identifiant »",
        "3. Saisir « mauvais » dans le champ « Mot de passe »",
        "4. Cliquer sur le bouton « Connexion »",  # la tentative ratée sur #bouton-absent n'y figure pas
    ]
    assert "- Texte « Produits » absent de la page" in fiche
    assert f"- Page affichée à la fin : {demo_url}/index.html" in fiche
    assert "- La page des produits s'affiche : le texte « Produits » est visible" in fiche  # résultat attendu du cas
    assert "![Étape 6](captures/etape-06.png)" in fiche
    assert "Pas de diagnostic : démo sans IA" in fiche


def test_diagnostic_du_modele(echec, tmp_path):
    modele = FauxModele(json.dumps(DIAGNOSTIC, ensure_ascii=False))
    calls = echec.model_calls
    diagnosis, missing = diagnose(echec, modele)
    assert missing == "" and diagnosis["nature"] == "bug" and diagnosis["gravite"] == "majeure"
    assert echec.model_calls == calls + 1
    system, user = modele.recu
    assert "N'invente aucun fait" in system
    assert "Texte « Produits » absent de la page" in user and "## Page à la fin du test" in user
    assert modele.images == []  # sans la vision, le modèle ne reçoit que du texte

    write_anomaly(echec, diagnosis)
    fiche = echec.anomaly_path.read_text(encoding="utf-8")
    assert fiche.startswith("# Fiche d'anomalie : La connexion échoue avec un utilisateur valide\n")
    assert "| Nature probable | Bug de l'application (avis de l'IA) |" in fiche
    assert "**Bug de l'application**, gravité majeure." in fiche and "à confirmer par un testeur" in fiche
    report = build_report(echec)
    assert "## Analyse de l'échec" in report and "[Fiche d'anomalie](anomalie.md)" in report


@pytest.mark.parametrize("reponse, raison", [
    ("ce n'est pas du JSON", "illisible"),
    (json.dumps({**DIAGNOSTIC, "nature": "météo"}), "nature"),
    (json.dumps({**DIAGNOSTIC, "titre": " "}), "titre"),
    (ProviderError("quota du jour épuisé"), "n'a pas pu répondre"),
])
def test_diagnostic_inutilisable(echec, reponse, raison):
    diagnosis, missing = diagnose(echec, FauxModele(reponse))
    assert diagnosis is None and raison in missing
    assert "Pas de diagnostic" in build_anomaly(echec, diagnosis, missing)


def test_analyse_d_un_blocage(pw_page, demo_url, tmp_path):
    """Trois erreurs de suite : l'action qui bloque figure dans les étapes, avec son erreur."""
    scenario = [{"action": "click", "target": "#bouton-absent"}] * 3
    run = Agent(ScriptedProvider(scenario)).run(pw_page, make_case(demo_url), tmp_path / "rapport")
    assert run.status == "BLOQUE" and needs_analysis(run)
    fiche = build_anomaly(run, None, "non demandé")
    assert fiche.startswith("# Analyse du blocage : 3 erreurs de suite")
    assert "Cliquer sur l'élément [-1] (l'action échoue : Aucun élément [-1] sur la page actuelle)" in fiche


def test_pas_de_fiche_quand_ce_n_est_pas_un_echec(echec):
    for status, summary in (("PASS", "ok"), ("BLOQUE", STOPPED_SUMMARY), ("BLOQUE", "Le modèle n'a pas pu répondre : quota")):
        echec.status, echec.summary = status, summary
        assert not needs_analysis(echec), summary


def test_resultats_attendus_du_cas():
    assert expected_results("# CT\nURL : x\n## Étapes\n1. a\n## Résultat attendu\n- b\n2. c\n## Notes\n- d") == ["b", "c"]


def test_diagnostic_normalise():
    data = parse_diagnosis('Voici : ```json\n{"nature": "Application", "gravité": "majeur", "titre": "T", '
                           '"explication": "E", "suggestion": "S"}\n```')
    assert data == {"nature": "bug", "gravite": "majeure", "titre": "T", "explication": "E", "suggestion": "S", "sources": []}


def test_diagnostic_avec_la_vision(echec):
    """Avec la vision, le modèle voit aussi la capture de l'étape qui échoue."""
    echec.vision = True
    modele = FauxModele(json.dumps(DIAGNOSTIC, ensure_ascii=False))
    diagnose(echec, modele)
    assert [mime for mime, _ in modele.images] == ["image/png"]
    assert modele.images[0][1].startswith(b"\x89PNG")


def test_etapes_pour_reproduire_avec_cadre_fenetre_et_attente():
    """La réponse à une fenêtre accompagne l'action qui l'ouvre ; un élément d'un cadre cite son cadre."""
    from agent.actions import Action
    from agent.anomalie import reproduction_step
    from agent.browser import ActionResult, Element
    from agent.runner import Step

    note = Element(1, "body", None, "", {"aria": "Note", "editable": True}, "#note",
                   frame=[{"css": "#editeur", "nth": None, "label": "Éditeur"}])
    saisie = Step(2, Action("fill", {"ref": 1, "text": "Bonjour"}), ActionResult(True, "ok", note))
    assert reproduction_step(saisie) == "Saisir « Bonjour » dans la zone de texte « Note » du cadre « Éditeur »"
    fenetre = {"type": "confirm", "message": "Sûr ?", "accepted": False, "text": None}
    clic = Step(3, Action("click", {"ref": 2}), ActionResult(True, "ok", Element(2, "button", None, "Envoyer", {}, "#e"), dialog=fenetre))
    assert reproduction_step(clic) == "Cliquer sur le bouton « Envoyer », puis répondre Annuler à la fenêtre « Sûr ? »"
    assert reproduction_step(Step(4, Action("dialog", {"accept": True}), ActionResult(True, "ok"))) is None
    attente = Step(5, Action("wait", {"seconds": 10, "text": "Chargé"}), ActionResult(True, "ok"))
    assert reproduction_step(attente) == "Attendre l'affichage de « Chargé » (10 s au plus)"


def test_erreur_technique_eclairee_par_la_documentation(pw_page, demo_url, tmp_path, monkeypatch):
    """Une erreur de Playwright : le programme lit la page officielle qui en parle, le modèle n'en cite que des extraits."""
    from agent import documentation
    from test_documentation import DOCUSAURUS, INPUT

    monkeypatch.setattr(documentation, "fetch", lambda url, timeout=None: DOCUSAURUS if url == INPUT else "")
    scenario = [
        {"action": "fill", "target": '[data-test="login-button"]', "args": {"text": "standard_user"}},  # un bouton, pas un champ
        {"action": "finish", "args": {"status": "BLOQUE", "summary": "Impossible de remplir le champ"}},
    ]
    run = Agent(ScriptedProvider(scenario)).run(pw_page, make_case(demo_url), tmp_path / "rapport")
    assert run.status == "BLOQUE" and "Erreur Playwright" in run.steps[1].result.detail

    modele = FauxModele(json.dumps({**DIAGNOSTIC, "nature": "test", "sources": [1, 5]}, ensure_ascii=False))
    diagnosis, missing = diagnose(run, modele)
    assert missing == "" and diagnosis["sources"] == [
        {"titre": "Playwright : Input : saisie, clavier, fichiers, survol, glisser-déposer, Text input", "url": INPUT + "#text-input"}]
    user = modele.recu[1]
    assert "## Documentation officielle de Playwright (extraits numérotés)" in user
    assert "[1] Playwright : Input : saisie, clavier, fichiers, survol, glisser-déposer, Text input" in user

    fiche = build_anomaly(run, diagnosis)
    assert "**Dans la documentation officielle :**" in fiche and f"({INPUT}#text-input)" in fiche
    run.anomaly_path, run.diagnosis = tmp_path / "anomalie.md", diagnosis
    assert f"Documentation officielle : [Playwright : Input" in build_report(run)


def test_echec_fonctionnel_sans_documentation(echec):
    """Une vérification en échec n'est pas une erreur technique : pas de documentation, pas de lien."""
    from agent.anomalie import read_documentation

    assert read_documentation(echec) is None
    modele = FauxModele(json.dumps({**DIAGNOSTIC, "sources": [1]}, ensure_ascii=False))
    diagnosis, _ = diagnose(echec, modele)
    assert diagnosis["sources"] == [] and "Documentation officielle" not in modele.recu[1]
    assert "Dans la documentation officielle" not in build_anomaly(echec, diagnosis)
