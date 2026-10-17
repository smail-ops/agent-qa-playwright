"""User stories et scénarios Gherkin : contrôle des propositions, fichiers .feature, scénarios lancés comme des cas."""

import json

import pytest

from agent.user_stories import feature_cases, normalize_step, parse_stories, story_from_dict

STORY = {
    "titre": "Tri des produits",
    "en_tant_que": "En tant que client connecté",
    "je_veux": "je veux trier les produits par prix",
    "afin_de": "afin d'acheter le moins cher",
    "priorite": "élevée",
    "criteres": [
        {"scenario": "Tri par prix croissant", "etapes": [
            "Étant donné que je suis connecté sur « https://www.saucedemo.com/ »",
            "Quand je choisis « Price (low to high) » dans la liste de tri",
            "Alors « Sauce Labs Onesie » apparaît avant « Sauce Labs Bike Light »",
        ]},
        {"scenario": "Commence par Et", "etapes": ["Et je clique", "Alors rien"]},  # premier mot-clé invalide
        {"scenario": "Pas du Gherkin", "etapes": ["Étant donné que je suis là", "Je clique sur Trier"]},
    ],
}


def test_user_story_controlee():
    story = story_from_dict(STORY)
    # Le début répété par le modèle (« En tant que », « je veux », « afin d' ») est retiré une seule fois
    assert story.sentence() == ("En tant que client connecté, je veux trier les produits par prix, "
                                "afin d'acheter le moins cher.")
    assert story.priority == "haute"  # « élevée » est compris
    assert [c.scenario for c in story.criteria] == ["Tri par prix croissant"]  # les critères mal formés sont écartés


def test_reponse_du_modele_triee():
    """Une story inutilisable est écartée, pas la réponse entière."""
    raw = "Voici mes propositions :\n```json\n" + json.dumps({"stories": [STORY, {"titre": "Sans critère"}]}) + "\n```"
    assert [s.title for s in parse_stories(raw)] == ["Tri des produits"]
    with pytest.raises(ValueError, match="aucune user story"):
        parse_stories(json.dumps({"stories": [{"titre": "Sans critère"}]}))
    with pytest.raises(ValueError):
        parse_stories("Je ne sais pas.")


@pytest.mark.parametrize("step, expected", [
    ("étant donné que je suis sur la page", "Étant donné que je suis sur la page"),
    ("Lorsqu'il clique sur « OK »", "Lorsqu'il clique sur « OK »"),
    ("Lorsqu’il clique", "Lorsqu’il clique"),  # apostrophe typographique
    ("Et  le   panier  affiche « 1 »", "Et le panier affiche « 1 »"),
    ("Alors", "Alors"),
    ("Etapes : cliquer", None),  # « Et » doit être un mot entier
    ("Cliquer sur Login", None),
])
def test_mots_cles_gherkin(step, expected):
    assert normalize_step(step) == expected


def test_fichier_feature_et_scenarios():
    story = story_from_dict(STORY)
    feature = story.to_feature("US-03", "https://www.saucedemo.com/")
    lines = feature.splitlines()
    assert lines[:5] == ["# language: fr", "# URL : https://www.saucedemo.com/",
                         "# User story proposée par agent-qa-playwright à partir de l'analyse du site : à relire avant usage.",
                         "@US-03 @priorite-haute", "Fonctionnalité: US-03 Tri des produits"]
    assert "  Afin d'acheter le moins cher" in lines
    assert "  Scénario: Tri par prix croissant" in lines

    cases = feature_cases(feature)
    assert len(cases) == 1
    case = cases[0]
    assert case.title == "US-03 Tri des produits : Tri par prix croissant"
    assert case.url == "https://www.saucedemo.com/"
    assert "## Scénario Gherkin" in case.text and "Quand je choisis « Price (low to high) » dans la liste de tri" in case.text
    assert case.with_url("https://recette.exemple.fr/").url == "https://recette.exemple.fr/"


def test_feature_ecrit_a_la_main():
    """Un .feature écrit par un testeur : contexte commun, plusieurs scénarios, adresse prise dans les étapes."""
    text = """# language: fr
@CT-40
Fonctionnalité: Connexion
  Contexte:
    Étant donné que j'ouvre « https://www.saucedemo.com/ »

  Scénario: Utilisateur valide
    Quand je me connecte avec « standard_user »
    Alors la page « Products » s'affiche

  Plan du scénario: Plusieurs utilisateurs
    Quand je me connecte avec « <nom> »

  Scénario: Utilisateur bloqué
    Quand je me connecte avec « locked_out_user »
    Alors un message d'erreur s'affiche
"""
    cases = feature_cases(text)
    assert [c.title for c in cases] == ["CT-40 Connexion : Utilisateur valide", "CT-40 Connexion : Utilisateur bloqué"]
    assert all(c.url == "https://www.saucedemo.com/" for c in cases)
    assert cases[0].text.count("Étant donné que j'ouvre") == 1  # le contexte est repris dans chaque scénario
    assert "<nom>" not in cases[1].text  # les plans de scénario ne sont pas repris
    assert feature_cases("Fonctionnalité: Sans adresse\n  Scénario: X\n    Quand je clique\n") == []
