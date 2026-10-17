"""Test généré automatiquement par agent-qa-playwright.

Cas de test : CT-90 Démo locale : tri, panier et commande
Rejoue les actions réussies de l'agent, sans IA. Lancement : pytest test_ct_90_demo_locale_tri_panier_et_commande.py
"""

import re
import time

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


def test_ct_90_demo_locale_tri_panier_et_commande(page: Page):
    page.goto('http://localhost:8000/index.html')
    page.locator('#user-name').fill('standard_user')
    page.locator('#password').fill('secret_sauce')
    page.locator('[data-test="login-button"]').click()
    assert attendre(lambda: compte(page, 'Ajouter au panier') == 3), '« Ajouter au panier » doit apparaître 3 fois'
    page.locator('[data-test="sort"]').select_option('Prix croissant')
    expect(page.locator('body')).to_contain_text(re.compile('Lampe\\s+de\\s+vélo.*T\\-shirt.*Sac\\s+à\\s+dos', re.IGNORECASE | re.DOTALL), use_inner_text=True)
    page.locator('button:has-text("Ajouter au panier")').nth(0).click()
    assert attendre(lambda: compte(page, 'Retirer') == 1), '« Retirer » doit apparaître 1 fois'
    page.locator('[data-test="checkout"]').click()
    expect(page.locator('[data-test="continuer"]')).to_be_disabled()
    page.locator('[data-test="cgu"]').click()
    expect(page.locator('[data-test="cgu"]')).to_be_checked()
    expect(page.locator('[data-test="continuer"]')).to_be_enabled()
    expect(page.locator('#code-postal')).to_have_value('')
