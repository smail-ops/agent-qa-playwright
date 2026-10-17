"""Analyse d'un site sans IA, puis propositions du modèle : user stories et cas de test."""

import json
import threading
from pathlib import Path

import pytest

from agent import propositions
from agent.__main__ import main as agent_main
from agent.analyse import AnalysisError, explore
from agent.propositions import (
    ask,
    case_from_dict,
    next_codes,
    parse_cases,
    propose_cases,
    propose_stories,
    save_cases,
    save_stories,
    story_text,
)
from agent.providers import RateLimitError, ScriptedProvider
from agent.runner import Agent
from agent.testcase import parse_test_case
from agent.user_stories import feature_cases

DEMO = json.loads((Path(__file__).resolve().parent.parent / "cas_de_test" / "demo" / "propositions_demo.json")
                  .read_text(encoding="utf-8"))


def in_thread(function, *args):
    """Lance une fonction qui ouvre son propre navigateur, hors de la boucle du navigateur des tests."""
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("value", function(*args)))
    thread.start()
    thread.join(180)
    return result["value"]


class Fake:
    """Faux modèle qui garde ce qu'on lui envoie."""

    name, model = "fake", "faux modèle"

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def complete(self, system, user, images=(), max_tokens=None):
        self.calls.append({"system": system, "user": user, "images": list(images)})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


# --- Analyse du site, sans IA ----------------------------------------------------------------


def test_analyse_avec_le_compte_de_test(pw_page, demo_url):
    analysis = explore(pw_page, f"{demo_url}/index.html", "standard_user", "secret_sauce", vision=True)
    assert analysis.logged_in and "réussie" in analysis.login
    assert [p.title for p in analysis.pages] == ["Boutique Démo", "Boutique Démo - Produits", "Boutique Démo - Commande"]
    login, products, order = analysis.pages
    assert login.fields == ["champ « Identifiant »", "champ mot de passe « Mot de passe »"]
    assert products.fields == ["liste « Trier » : Nom (A à Z), Prix croissant"]
    assert products.buttons == ["« Ajouter au panier » (3 fois)"]
    assert "« Déconnexion » (/index.html)" in products.links  # relevé, mais jamais suivi
    assert "case à cocher « J'accepte les conditions générales de vente »" in order.fields
    assert len(analysis.images()) == 3  # vision : une capture par page

    prompt = analysis.to_prompt()
    assert "identifiant « standard_user »" in prompt
    assert "secret_sauce" not in prompt  # le mot de passe ne part jamais vers le modèle


def test_connexion_refusee_et_pages_publiques(pw_page, demo_url):
    analysis = explore(pw_page, f"{demo_url}/index.html", "standard_user", "mauvais")
    assert not analysis.logged_in and "échoué" in analysis.login
    assert len(analysis.pages) == 1
    assert pw_page.input_value("#password") == ""  # le mot de passe ne reste pas dans la page
    assert analysis.images() == []  # sans la vision, aucune capture


def test_adresse_injoignable(pw_page):
    with pytest.raises(AnalysisError, match="Impossible d'ouvrir"):
        explore(pw_page, "http://127.0.0.1:9/")


# --- Propositions du modèle ------------------------------------------------------------------


def test_user_stories_proposees(pw_page, demo_url):
    analysis = explore(pw_page, f"{demo_url}/index.html", "standard_user", "secret_sauce", vision=True)
    model = Fake(DEMO["stories"])
    stories = propose_stories(analysis, model, vision=True)
    assert [s.title for s in stories] == ["Connexion à la boutique", "Ajout d'un produit au panier",
                                         "Tri des produits par prix", "Conditions générales de vente"]
    sent = model.calls[0]
    assert "Page 2 : Boutique Démo - Produits" in sent["user"] and "secret_sauce" not in sent["user"]
    assert len(sent["images"]) == 3
    assert propose_stories(analysis, Fake(DEMO["stories"])) and model.calls  # sans la vision : aucune image
    text = story_text(stories[0])
    assert text.startswith("En tant que client de la boutique, je veux me connecter")
    assert "Scénario : Mot de passe incorrect" in text


def test_cas_de_test_proposes():
    model = Fake(DEMO["cas"]["Connexion à la boutique"])
    cases = propose_cases("En tant que client, je veux me connecter.", "http://localhost:8000/index.html", model,
                          site="Page 1 : Boutique Démo")
    assert [c.title for c in cases] == ["Connexion avec un utilisateur valide", "Connexion refusée avec un mot de passe incorrect",
                                        "Connexion refusée sans identifiant"]
    assert "Relevé du site" in model.calls[0]["user"]
    markdown = cases[0].markdown("CT-21", "http://localhost:8000/index.html")
    case = parse_test_case(markdown)
    assert case.title == "CT-21 Connexion avec un utilisateur valide" and case.url == "http://localhost:8000/index.html"
    assert "## Étapes\n1. Saisir « standard_user » dans le champ Identifiant\n" in markdown
    assert "## Résultats attendus\n- La page « Produits » s'affiche\n" in markdown


def test_cas_mal_formes_ecartes():
    cleaned = case_from_dict({"titre": "CT-?? : Panier vide", "etapes": ["1. Ouvrir le panier"], "resultats": "- Le panier est vide"})
    assert (cleaned.title, cleaned.steps, cleaned.expected) == ("Panier vide", ["Ouvrir le panier"], ["Le panier est vide"])
    raw = json.dumps({"cas": [{"titre": "Sans étapes", "resultats": ["x"]}, {"titre": "Bon", "etapes": ["a"], "resultats": ["b"]}]})
    assert [c.title for c in parse_cases(raw)] == ["Bon"]
    with pytest.raises(ValueError):
        parse_cases('{"cas": []}')


def test_quota_par_minute_une_seule_nouvelle_tentative():
    pauses = []
    model = Fake(RateLimitError("quota", retry_after=5), "{}")
    assert ask(model, "s", "u", sleep=pauses.append) == "{}"
    assert pauses == [5]
    with pytest.raises(RateLimitError):  # quota du jour : inutile d'attendre
        ask(Fake(RateLimitError("quota", daily=True)), "s", "u", sleep=pauses.append)
    assert pauses == [5]


# --- Enregistrement --------------------------------------------------------------------------


def test_numeros_et_fichiers(tmp_path):
    cases_dir = tmp_path / "cas_de_test"
    (cases_dir / "demo").mkdir(parents=True)
    (cases_dir / "CT-03_connexion.md").write_text("# CT-03 Connexion\nURL : https://x.fr/\n", encoding="utf-8")
    (cases_dir / "demo" / "CT-90_demo.md").write_text("# CT-90 Démo\nURL : http://localhost:8000/\n", encoding="utf-8")
    assert next_codes(cases_dir, "CT", 2) == ["CT-04", "CT-05"]  # les numéros des démos (90 et plus) ne comptent pas

    stories = propose_stories(type("A", (), {"to_prompt": lambda self: "", "images": lambda self: []})(), Fake(DEMO["stories"]))
    paths = save_stories(stories[:2], "http://localhost:8000/index.html", cases_dir)
    assert [p.name for p in paths] == ["US-01_connexion_a_la_boutique.feature", "US-02_ajout_d_un_produit_au_panier.feature"]
    scenarios = feature_cases(paths[0].read_text(encoding="utf-8"))
    assert [s.title for s in scenarios] == ["US-01 Connexion à la boutique : Connexion réussie",
                                            "US-01 Connexion à la boutique : Mot de passe incorrect"]

    cases = parse_cases(json.dumps(DEMO["cas"]["Connexion à la boutique"]))
    elsewhere = tmp_path / "propositions"
    written = save_cases(cases[:1], "http://localhost:8000/index.html", elsewhere, cases_dir)
    assert [p.name for p in written] == ["CT-04_connexion_avec_un_utilisateur_valide.md"]  # numéro libre dans cas_de_test aussi
    assert next_codes(elsewhere, "CT", 1, cases_dir) == ["CT-05"]


def test_un_scenario_gherkin_se_lance_comme_un_cas(pw_page, demo_url, tmp_path):
    feature = save_stories(propose_stories(type("A", (), {"to_prompt": lambda self: "", "images": lambda self: []})(),
                                           Fake(json.dumps(DEMO["stories"]).replace("http://localhost:8000", demo_url))),
                           f"{demo_url}/index.html", tmp_path)[0]
    case = feature_cases(feature.read_text(encoding="utf-8"))[1]  # « Mot de passe incorrect »
    scenario = [
        {"action": "fill", "target": "#user-name", "args": {"text": "standard_user"}},
        {"action": "fill", "target": "#password", "args": {"text": "mauvais"}},
        {"action": "click", "target": '[data-test="login-button"]'},
        {"action": "check_text", "args": {"text": "Identifiant ou mot de passe incorrect"}},
        {"action": "check_url", "args": {"contains": "inventory", "absent": True}},
        {"action": "finish", "args": {"status": "PASS", "summary": "Connexion refusée"}},
    ]
    result = Agent(ScriptedProvider(scenario)).run(pw_page, case, tmp_path / "rapport")
    assert result.status == "PASS", result.summary
    assert result.steps[0].action.args["url"] == f"{demo_url}/index.html"


def test_ligne_de_commande_avec_un_fichier_feature(demo_url, tmp_path, capsys):
    feature = tmp_path / "US-01_connexion.feature"
    feature.write_text(f"""# language: fr
# URL : {demo_url}/index.html
Fonctionnalité: US-01 Connexion
  Scénario: Page de connexion
    Étant donné que j'ouvre la page de connexion
    Alors la page « Boutique Démo » s'affiche

  Scénario: Bouton de connexion
    Étant donné que j'ouvre la page de connexion
    Alors le bouton « Connexion » s'affiche
""", encoding="utf-8")
    scenario = tmp_path / "scenario.json"
    scenario.write_text(json.dumps([{"action": "check_text", "args": {"text": "Boutique Démo"}},
                                    {"action": "finish", "args": {"status": "PASS", "summary": "OK"}}]), encoding="utf-8")
    options = ["--provider", "scripted", "--scenario", str(scenario), "--out", str(tmp_path / "rapports")]
    assert in_thread(agent_main, [str(feature), *options]) == 0
    output = capsys.readouterr().out
    assert "Scénario 2 sur 2" in output and "Bilan       : 2 scénario(s) réussi(s) sur 2" in output
    assert agent_main([str(feature), "--numero", "3", *options]) == 3
    assert "choisissez --numero entre 1 et 2" in capsys.readouterr().err


def test_ligne_de_commande_des_propositions(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(propositions, "make_provider", lambda name, model=None: Fake(DEMO["cas"]["Tri des produits par prix"]))
    code = propositions.main(["http://localhost:8000/index.html", "--user-story", "En tant que client, je veux trier."])
    assert code == 0
    files = sorted(p.name for p in (tmp_path / "propositions").iterdir())
    assert files == ["CT-01_tri_des_produits_par_prix_croissant.md", "CT-02_retour_au_tri_par_nom.md"]
    assert "déplacez celles que vous gardez dans le dossier cas_de_test" in capsys.readouterr().out
