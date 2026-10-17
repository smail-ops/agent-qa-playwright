"""Lecture des cas de test et des réponses du modèle."""

import pytest

from agent.actions import parse_action
from agent.testcase import parse_test_case, slugify

CASE = """# CT-01 Connexion réussie
URL : https://www.saucedemo.com/

## Étapes
1. Saisir "standard_user" dans le champ identifiant
"""


def test_cas_de_test_titre_et_url():
    case = parse_test_case(CASE)
    assert case.title == "CT-01 Connexion réussie"
    assert case.url == "https://www.saucedemo.com/"
    assert case.slug == "ct_01_connexion_reussie"


def test_cas_de_test_sans_url():
    with pytest.raises(ValueError, match="URL"):
        parse_test_case("# Titre seul")


def test_slugify_accents_et_symboles():
    assert slugify("Étape n°2 : Panier & paiement") == "etape_n2_panier_paiement"


def test_action_json_simple():
    action = parse_action('{"action": "fill", "args": {"ref": "2", "text": "abc"}, "reason": "champ identifiant"}')
    assert action.name == "fill"
    assert action.args == {"ref": 2, "text": "abc"}  # ref converti en entier
    assert action.reason == "champ identifiant"


def test_action_entouree_de_texte_et_de_balises():
    raw = 'Voici mon action :\n```json\n{"action": "click", "args": {"ref": 3}}\n```'
    assert parse_action(raw).args == {"ref": 3}


def test_finish_normalise_le_statut():
    action = parse_action('{"action": "finish", "args": {"status": "bloqué", "summary": "élément absent"}}')
    assert action.args == {"status": "BLOQUE", "summary": "élément absent"}


def test_verification_negative():
    action = parse_action('{"action": "check_url", "args": {"contains": "inventory", "absent": true}}')
    assert action.args == {"contains": "inventory", "absent": True}


@pytest.mark.parametrize(
    "raw, message",
    [
        ("pas de json", "objet JSON"),
        ('{"action": "click", "args": {"ref": 3}', "JSON invalide"),
        ('{"action": "voler", "args": {}}', "action inconnue"),
        ('{"action": "fill", "args": {"ref": 1}}', "text"),
        ('{"action": "click", "args": {"ref": "trois"}}', "invalide"),
        ('{"action": "finish", "args": {"status": "PEUT-ETRE"}}', "statut"),
        ('{"action": "fill", "args": {"ref": 1, "text": null}}', "text"),  # null ne devient pas « None »
        ('{"action": "click", "args": {"ref": true}}', "ref"),  # true ne devient pas 1
        ('{"action": "click", "args": {"ref": 2.5}}', "ref"),
        ('{"action": "check_text", "args": {"text": "Produits", "absent": "peut-être"}}', "absent"),
    ],
)
def test_reponses_invalides(raw, message):
    with pytest.raises(ValueError, match=message):
        parse_action(raw)


def test_meme_cas_sur_une_autre_adresse():
    """Le champ « Site à tester » et l'option --url : même cas, autre site, fichier inchangé."""
    case = parse_test_case(CASE)
    recette = case.with_url("http://192.168.1.20:8080/")
    assert recette.url == "http://192.168.1.20:8080/"
    assert "URL : http://192.168.1.20:8080/" in recette.text  # le modèle lit la bonne adresse
    assert "saucedemo" not in recette.text
    assert recette.title == case.title and case.url == "https://www.saucedemo.com/"


@pytest.mark.parametrize("url", ["", "www.saucedemo.com", "ftp://serveur/fichier", "http://", "https:// espace.fr"])
def test_adresse_refusee(url):
    with pytest.raises(ValueError, match="http:// ou https://"):
        parse_test_case(CASE).with_url(url)
