"""Test généré automatiquement par agent-qa-playwright.

Cas de test : CT-90 Démo locale : tri, panier et commande
Rejoue les actions réussies de l'agent, sans IA. Lancement : pytest test_ct_90_demo_locale_tri_panier_et_commande.py
"""

import os
import re
import time

from dotenv import find_dotenv, load_dotenv

from playwright.sync_api import Page, expect


def compte(page: Page, texte: str) -> int:
    """Nombre de fois où le texte apparaît dans la page visible (casse et espaces ignorés)."""
    visible = " ".join(page.locator("body").inner_text().split()).lower()
    return visible.count(" ".join(texte.split()).lower())


def attendre(condition, delai: float = 5.0) -> bool:
    """Attend qu'une condition devienne vraie, comme le fait expect de Playwright."""
    fin = time.monotonic() + delai
    while not condition():
        if time.monotonic() > fin:
            return False
        time.sleep(0.1)
    return True

load_dotenv(find_dotenv(usecwd=True))  # les mots de passe de test sont dans le fichier .env du projet


def secret(nom: str) -> str:
    """Un mot de passe de test, lu dans le fichier .env (l'agent l'y a rangé)."""
    valeur = os.environ.get(nom)
    if not valeur:
        raise RuntimeError(f"Ajoute {nom}=... dans le fichier .env du projet : ce test en a besoin.")
    return valeur


def test_ct_90_demo_locale_tri_panier_et_commande(page: Page):
    page.goto('http://localhost:8000/index.html')
    page.get_by_role('textbox', name='Identifiant', exact=True).fill('standard_user')
    page.get_by_placeholder('Mot de passe', exact=True).fill(secret('MOT_DE_PASSE_LOCALHOST'))
    page.get_by_role('button', name='Connexion', exact=True).click()
    assert attendre(lambda: compte(page, 'Ajouter au panier') == 3), '« Ajouter au panier » doit apparaître 3 fois'
    page.locator('[data-test="sort"]').select_option('Prix croissant')
    expect(page.locator('body')).to_contain_text(re.compile('Lampe\\s+de\\s+vélo.*T\\-shirt.*Sac\\s+à\\s+dos', re.IGNORECASE | re.DOTALL), use_inner_text=True)
    page.get_by_role('button', name='Ajouter au panier', exact=True).nth(0).click()
    assert attendre(lambda: compte(page, 'Retirer') == 1), '« Retirer » doit apparaître 1 fois'
    page.get_by_role('link', name='Commander', exact=True).click()
    expect(page.get_by_role('button', name='Continuer', exact=True)).to_be_disabled()
    page.get_by_role('checkbox', name="J'accepte les conditions générales de vente", exact=True).click()
    expect(page.get_by_role('checkbox', name="J'accepte les conditions générales de vente", exact=True)).to_be_checked()
    expect(page.get_by_role('button', name='Continuer', exact=True)).to_be_enabled()
    expect(page.get_by_role('textbox', name='Code postal', exact=True)).to_have_value('')
