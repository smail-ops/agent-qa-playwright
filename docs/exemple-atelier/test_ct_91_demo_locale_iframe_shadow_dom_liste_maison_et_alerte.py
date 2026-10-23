"""Test généré automatiquement par agent-qa-playwright.

Cas de test : CT-91 Démo locale : iframe, Shadow DOM, liste maison et alerte
Rejoue les actions réussies de l'agent, sans IA. Lancement : pytest test_ct_91_demo_locale_iframe_shadow_dom_liste_maison_et_alerte.py
"""

import re

from playwright.sync_api import Page, expect


def test_ct_91_demo_locale_iframe_shadow_dom_liste_maison_et_alerte(page: Page):
    page.goto('http://localhost:8000/atelier.html')
    page.frame_locator('#editeur').locator('#note').fill('Livrer avant midi')
    expect(page.frame_locator('#editeur').locator('#note')).to_have_text('Livrer avant midi', use_inner_text=True)
    page.locator('generateur-code').locator('#generer').click()
    expect(page.locator('generateur-code').locator('#code')).to_have_value(re.compile('^PROMO-[0-9A-F]{4}-[0-9A-F]{4}$'))
    expect(page.get_by_text(re.compile(re.escape('Code prêt à copier'), re.IGNORECASE)).first).to_be_visible()
    page.locator('xpath=//label[normalize-space(.)="Rôle"]/following::div[@tabindex][1]').click()
    page.locator('div[role="option"]:has-text("ESS")').click()
    expect(page.locator('xpath=//label[normalize-space(.)="Rôle"]/following::div[@tabindex][1]')).to_have_text('ESS', use_inner_text=True)
    expect(page.locator('body')).to_contain_text('Rôle : ESS', ignore_case=True, use_inner_text=True)
    # Fenêtre « Confirmer l'envoi de la commande ? » : réponse OK
    page.once('dialog', lambda dialog: dialog.accept())
    page.locator('[data-test="envoyer"]').click()
    expect(page.locator('body')).to_contain_text('Commande envoyée', ignore_case=True, use_inner_text=True)
    page.locator('[data-test="charger"]').click()
    expect(page.locator('body')).to_contain_text('Stock chargé', ignore_case=True, use_inner_text=True, timeout=10000)
    expect(page.locator('body')).to_contain_text('Stock chargé : 12 articles', ignore_case=True, use_inner_text=True)
