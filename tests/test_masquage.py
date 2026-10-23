"""Masquage des données personnelles avant l'envoi au modèle."""

import pytest

from agent.analyse import PageSummary, SiteAnalysis
from agent.browser import Element, Observation
from agent.masquage import mask
from agent.prompts import build_user_message
from agent.testcase import parse_test_case

CAS = parse_test_case("""# CT-50 Contact client
URL : https://exemple.fr/contact

## Étapes
1. Saisir « jean.dupont@exemple.fr » dans le champ E-mail, et « 06 12 34 56 78 » dans le champ Téléphone
2. Payer avec la carte de test 4111 1111 1111 1111
""")


@pytest.mark.parametrize("texte, attendu", [
    ("Écrire à marie.curie@labo.org", "Écrire à [e-mail masqué]"),
    ("Tél. 01.23.45.67.89 ou +33 6 98 76 54 32", "Tél. [téléphone masqué] ou [téléphone masqué]"),
    ("Londres : +44 20 7946 0958", "Londres : [téléphone masqué]"),
    ("Carte 4242 4242 4242 4242, expire 12/28", "Carte [carte masquée], expire 12/28"),
    ("IBAN FR76 3000 6000 0112 3456 7890 189", "IBAN [IBAN masqué]"),
    ("IBAN FR7630006000011234567890189.", "IBAN [IBAN masqué]."),
])
def test_donnees_masquees(texte, attendu):
    assert mask(texte) == attendu


@pytest.mark.parametrize("texte", [
    "Commande n° 1234567890123",  # 13 chiffres, mais pas un numéro de carte (contrôle de Luhn)
    "IBAN FR76 3000 6000 0112 3456 7890 188",  # mauvaise clé
    "Total : 29,99 € ; +12.50 ; réf. 0412-55",
    "Code PROMO-7F3A-91C2, GUID 3f2504e0-4f89-11d3-9a0c-0305e82c3301",
    "Logo : logo@2x.png",
])
def test_rien_a_masquer(texte):
    assert mask(texte) == texte


def test_valeurs_du_cas_de_test_gardees():
    """Le testeur a écrit ces valeurs lui-même : l'agent en a besoin pour les saisir et les vérifier."""
    page = ("Client : JEAN.DUPONT@exemple.fr, +33 6 12 34 56 78, carte 4111111111111111. "
            "Autre client : paul@exemple.fr, 07 00 00 00 01")
    assert mask(page, CAS.text) == ("Client : JEAN.DUPONT@exemple.fr, +33 6 12 34 56 78, carte 4111111111111111. "
                                    "Autre client : [e-mail masqué], [téléphone masqué]")


def test_message_au_modele_masque():
    element = Element(1, "input", "email", "", {"aria": "E-mail", "value": "paul@exemple.fr"}, "#email")
    observation = Observation(url="https://exemple.fr/contact", title="Contact",
                              text="Votre conseiller : claire@exemple.fr", elements=[element],
                              frame_texts=[("Paiement", "Carte enregistrée : 4242 4242 4242 4242")])
    message = build_user_message(CAS, [], observation)
    assert "jean.dupont@exemple.fr" in message  # le cas de test est envoyé tel quel
    for secret in ("claire@exemple.fr", "paul@exemple.fr", "4242 4242 4242 4242"):
        assert secret not in message
    assert 'valeur="[e-mail masqué]"' in message and "Carte enregistrée : [carte masquée]" in message


def test_releve_du_site_masque():
    analysis = SiteAnalysis(url="https://exemple.fr", username="client@exemple.fr", pages=[
        PageSummary(url="https://exemple.fr/compte", title="Mon compte",
                    text="Connecté : client@exemple.fr. Contact : support@exemple.fr, 01 23 45 67 89")])
    prompt = analysis.to_prompt()
    assert "identifiant « client@exemple.fr »" in prompt and "Connecté : client@exemple.fr" in prompt
    assert "support@exemple.fr" not in prompt and "01 23 45 67 89" not in prompt
