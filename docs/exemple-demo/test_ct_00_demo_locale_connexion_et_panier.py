"""Test généré automatiquement par agent-qa-playwright.

Cas de test : CT-00 Démo locale : connexion et panier
Rejoue les actions réussies de l'agent, sans IA. Lancement : pytest test_ct_00_demo_locale_connexion_et_panier.py
"""

import re

from playwright.sync_api import Page, expect


def test_ct_00_demo_locale_connexion_et_panier(page: Page):
    page.goto('http://localhost:8000/index.html')
    page.locator('#user-name').fill('standard_user')
    page.locator('#password').fill('secret_sauce')
    page.locator('[data-test="login-button"]').click()
    expect(page).to_have_url(re.compile(re.escape('inventory')))
    expect(page.locator('body')).to_contain_text('Produits', ignore_case=True, use_inner_text=True)
    page.locator('button:has-text("Ajouter au panier")').nth(0).click()
    expect(page.locator('body')).to_contain_text('Panier (1)', ignore_case=True, use_inner_text=True)
