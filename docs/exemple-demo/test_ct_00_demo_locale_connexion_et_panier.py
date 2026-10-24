"""Test généré automatiquement par agent-qa-playwright.

Cas de test : CT-00 Démo locale : connexion et panier
Rejoue les actions réussies de l'agent, sans IA. Lancement : pytest test_ct_00_demo_locale_connexion_et_panier.py
"""

import os
import re

from dotenv import find_dotenv, load_dotenv

from playwright.sync_api import Page, expect

load_dotenv(find_dotenv(usecwd=True))  # les mots de passe de test sont dans le fichier .env du projet


def secret(nom: str) -> str:
    """Un mot de passe de test, lu dans le fichier .env (l'agent l'y a rangé)."""
    valeur = os.environ.get(nom)
    if not valeur:
        raise RuntimeError(f"Ajoute {nom}=... dans le fichier .env du projet : ce test en a besoin.")
    return valeur


def test_ct_00_demo_locale_connexion_et_panier(page: Page):
    page.goto('http://localhost:8000/index.html')
    page.get_by_role('textbox', name='Identifiant', exact=True).fill('standard_user')
    page.get_by_placeholder('Mot de passe', exact=True).fill(secret('MOT_DE_PASSE_LOCALHOST'))
    page.get_by_role('button', name='Connexion', exact=True).click()
    expect(page).to_have_url(re.compile(re.escape('inventory')))
    expect(page.locator('body')).to_contain_text('Produits', ignore_case=True, use_inner_text=True)
    page.get_by_role('button', name='Ajouter au panier', exact=True).nth(0).click()
    expect(page.locator('body')).to_contain_text('Panier (1)', ignore_case=True, use_inner_text=True)
