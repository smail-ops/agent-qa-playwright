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


def test_verifications_plus_riches():
    """Ordre, nombre, valeur et état : les arguments sont contrôlés et normalisés."""
    order = parse_action('{"action": "check_order", "args": {"texts": [" Lampe de vélo ", "T-shirt", 29.99]}}')
    assert order.args == {"texts": ["Lampe de vélo", "T-shirt", "29.99"]}
    assert parse_action('{"action": "check_count", "args": {"text": "Retirer", "count": "2"}}').args["count"] == 2
    assert parse_action('{"action": "check_value", "args": {"ref": 1, "value": ""}}').args == {"ref": 1, "value": ""}
    for state, expected in (("Disabled", "disabled"), ("grisé", "disabled"), ("coché", "checked"), ("décoché", "unchecked")):
        assert parse_action(f'{{"action": "check_state", "args": {{"ref": 2, "state": "{state}"}}}}').args["state"] == expected


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
        ('{"action": "check_order", "args": {"texts": ["Seul"]}}', "texts"),  # un ordre demande deux textes
        ('{"action": "check_order", "args": {"texts": "A puis B"}}', "texts"),
        ('{"action": "check_order", "args": {"texts": ["A", " "]}}', "texts"),
        ('{"action": "check_count", "args": {"text": "Remove", "count": -1}}', "check_count"),
        ('{"action": "check_count", "args": {"text": " ", "count": 2}}', "check_count"),
        ('{"action": "check_count", "args": {"text": "Remove", "count": 1.5}}', "count"),
        ('{"action": "check_value", "args": {"ref": 4}}', "value"),
        ('{"action": "check_state", "args": {"ref": 4, "state": "joli"}}', "état"),
        ('{"action": "check_state", "args": {"ref": 4, "state": "visible"}}', "état"),  # la visibilité passe par check_text
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


def test_tous_les_cas_de_test_sont_valides():
    """Chaque fichier de cas_de_test se lit, a un identifiant unique et une adresse https ou locale."""
    from pathlib import Path

    from alfred.server import case_code

    dossier = Path(__file__).resolve().parent.parent / "cas_de_test"
    # Seule exception au https : UI Testing Playground, publié en http par Inflectra (campagne terrain)
    adresses = ("https://", "http://localhost", "http://uitestingplayground.com/")
    codes = []
    for fichier in sorted(dossier.rglob("CT-*.md")):
        case = parse_test_case(fichier.read_text(encoding="utf-8"))
        assert fichier.name.startswith(case_code(case.title) + "_"), fichier.name  # nom du fichier = identifiant
        assert case.url.startswith(adresses), case.url
        assert "## Étapes" in case.text and "## Résultats attendus" in case.text, fichier.name
        codes.append(case_code(case.title))
    assert len(codes) >= 14 and len(codes) == len(set(codes))
