"""Cadres (iframes), composants web (Shadow DOM), listes « maison », fenêtres JavaScript et attentes.

Ces cinq situations bloquaient l'agent sur les vrais sites (campagne terrain : CT-20, CT-28,
CT-32, CT-34 et CT-36). La page demo_site/atelier.html les réunit.
"""

import re
import time

import pytest

from agent import browser
from agent.actions import Action, parse_action
from agent.browser import execute, observe


@pytest.fixture
def atelier(pw_page, demo_url):
    pw_page.goto(f"{demo_url}/atelier.html")
    return pw_page


def find(observation, selector):
    return next(e for e in observation.elements if e.selector == selector)


def test_observation_entre_dans_le_cadre_et_le_composant(atelier):
    obs = observe(atelier)
    note, code, generer = find(obs, "#note"), find(obs, "#code"), find(obs, "#generer")
    # Éditeur dans un cadre, comme TinyMCE : zone de texte riche (contenteditable)
    assert note.frame == [{"css": "#editeur", "nth": None, "label": "Éditeur de note"}] and note.shadow is None
    assert note.editable() and note.noun() == ("la zone de texte", True) and note.label() == "Note de commande"
    assert "(dans le cadre « Éditeur de note »)" in note.describe() and "éditable" in note.describe()
    # Champ et bouton d'un composant web : le chemin passe par l'hôte du Shadow DOM
    assert code.shadow == [{"css": "generateur-code", "nth": None}] and code.frame is None
    assert "(dans le composant generateur-code)" in code.describe()
    assert generer.label() == "Générer un code"  # le texte n'est qu'une icône (⚙) : on prend l'aria-label
    assert code.xpath is None  # XPath n'entre pas dans un Shadow DOM
    # Texte du cadre, montré à part au modèle
    assert obs.frame_texts == [("Éditeur de note", "Votre note ici.")]
    assert "Texte du cadre « Éditeur de note » :\nVotre note ici." in obs.to_prompt()
    assert obs.frames[note.ref] is not None and code.ref not in obs.frames


def test_saisie_et_verification_dans_le_cadre(atelier):
    obs = observe(atelier)
    note = find(obs, "#note")
    assert execute(atelier, Action("fill", {"ref": note.ref, "text": "Livrer  avant midi"}), obs).ok
    ok = execute(atelier, Action("check_value", {"ref": note.ref, "value": "Livrer avant midi"}), obs)
    assert ok.check is True and ok.detail == "« Note de commande » contient « Livrer avant midi »"
    vu = execute(atelier, Action("check_text", {"text": "livrer avant"}), None)
    assert vu.check is True and vu.frame[0]["css"] == "#editeur" and vu.value == "Livrer avant"
    assert vu.detail == "Texte « livrer avant » présent dans le cadre « Éditeur de note »"
    assert "valeur" not in note.describe()  # une zone de texte riche montre son texte, pas une valeur


def test_composant_web_et_motif(atelier, monkeypatch):
    monkeypatch.setattr(browser, "CHECK_TIMEOUT_MS", 300)
    obs = observe(atelier)
    code = find(obs, "#code")
    motif = r"^PROMO-[0-9A-F]{4}-[0-9A-F]{4}$"
    vide = execute(atelier, Action("check_value", {"ref": code.ref, "value": motif, "regex": True}), obs)
    assert vide.check is False and vide.detail == f"« Code promo » est vide, qui ne correspond pas au motif « {motif} »"
    assert execute(atelier, Action("click", {"ref": find(obs, "#generer").ref}), obs).ok
    ok = execute(atelier, Action("check_value", {"ref": code.ref, "value": motif, "regex": True}), obs)
    assert ok.check is True and ok.value == "PROMO-7F3A-91C2"
    # Le texte du composant n'est pas dans document.body.innerText : l'agent le lit quand même
    texte = execute(atelier, Action("check_text", {"text": "Code prêt à copier"}), None)
    assert texte.check is True and texte.in_shadow and texte.frame is None
    assert 'valeur="PROMO-7F3A-91C2"' in find(observe(atelier), "#code").describe()


def test_liste_maison(atelier):
    """Liste déroulante faite de div, comme dans OrangeHRM : son libellé est posé à côté, sans lien for/id."""
    obs = observe(atelier)
    listes = [e for e in obs.elements if e.text == "-- Choisir --"]
    assert [(e.label(), e.noun()) for e in listes] == [("Rôle", ("le champ", False)), ("Statut", ("le champ", False))]
    role = listes[0]
    # Deux listes identiques : c'est le libellé qui désigne la bonne, en XPath (Playwright et Selenium le comprennent)
    assert role.selector == 'xpath=//label[normalize-space(.)="Rôle"]/following::div[@tabindex][1]'
    assert role.xpath == role.selector.removeprefix("xpath=") and 'label="Rôle"' in role.describe()
    assert not [e for e in obs.elements if e.attrs.get("role") == "option"]  # options cachées : pas encore observées
    execute(atelier, Action("click", {"ref": role.ref}), obs)
    obs = observe(atelier)
    ess = find(obs, 'div[role="option"]:has-text("ESS")')
    assert ess.noun() == ("l'option", True) and ess.nth is None
    assert ess.xpath == '//div[@role="option"][contains(normalize-space(.), "ESS")]'
    execute(atelier, Action("click", {"ref": ess.ref}), obs)
    obs = observe(atelier)
    choisi = execute(atelier, Action("check_value", {"ref": find(obs, role.selector).ref, "value": "ESS"}), obs)
    assert choisi.check is True and choisi.detail == "« Rôle » contient « ESS »"  # le texte affiché de la liste
    assert execute(atelier, Action("check_text", {"text": "Rôle : ESS"}), None).check is True


def test_libelle_d_un_autre_champ_jamais_emprunte(pw_page):
    """Un champ sans libellé ne prend pas celui du champ qui le précède."""
    pw_page.set_content("""
        <div><label>Nom</label><input class="saisie"><input class="saisie-libre"></div>
        <div><label>Ville</label><div><span>aide</span><input class="saisie"></div></div>""")
    elements = observe(pw_page).elements
    assert [(e.attrs.get("label"), e.selector) for e in elements] == [
        ("Nom", 'xpath=//label[normalize-space(.)="Nom"]/following::input[1]'),
        (None, "input.saisie-libre"),  # ni attribut, ni libellé : sa classe, en dernier recours
        ("Ville", 'xpath=//label[normalize-space(.)="Ville"]/following::input[1]')]
    for element in elements:
        assert pw_page.locator(f"xpath={element.xpath}").get_attribute("data-agent-ref") == str(element.ref)


def test_element_cliquable_sans_role(pw_page):
    """Une option sans rôle ni attribut, mais avec le curseur « main », est observée ; son conteneur ne l'est pas."""
    pw_page.set_content("""
        <style>.choix { cursor: pointer; padding: 4px }</style>
        <div class="menu" style="cursor: pointer"><div class="choix">Paris</div><div class="choix">Lyon</div></div>
        <span style="cursor: pointer"></span>""")
    elements = observe(pw_page).elements
    assert [(e.tag, e.text, e.selector, e.nth) for e in elements] == [
        ("div", "Paris Lyon", "div.menu", None)]  # le premier élément « main » englobe les options
    pw_page.set_content("""
        <style>.choix { cursor: pointer; padding: 4px }</style>
        <div class="menu"><div class="choix">Paris</div><div class="choix">Lyon</div></div>""")
    elements = observe(pw_page).elements
    assert [(e.text, e.selector, e.nth) for e in elements] == [("Paris", 'div.choix:has-text("Paris")', None),
                                                              ("Lyon", 'div.choix:has-text("Lyon")', None)]
    for element in elements:  # chaque XPath désigne bien l'élément observé
        assert pw_page.locator(f"xpath={element.xpath}").get_attribute("data-agent-ref") == str(element.ref)


def test_classe_instable_ignoree(pw_page):
    """Une classe d'état (« active ») ou fabriquée (« css-1x2y3z ») ne sert pas de sélecteur."""
    pw_page.set_content("""<div tabindex="0" class="active css-1x2y3z">Menu</div>""")
    assert observe(pw_page).elements[0].selector == 'div:has-text("Menu")'


def test_fenetre_de_confirmation(atelier):
    """Sans consigne, la fenêtre reçoit OK ; avec dialog, la réponse choisie, pour une seule fenêtre."""
    obs = observe(atelier)
    envoyer = find(obs, '[data-test="envoyer"]')
    clic = execute(atelier, Action("click", {"ref": envoyer.ref}), obs)
    assert clic.ok and clic.dialog == {"type": "confirm", "message": "Confirmer l'envoi de la commande ?", "accepted": True,
                                       "text": None}
    assert clic.detail.endswith("Fenêtre de confirmation « Confirmer l'envoi de la commande ? » acceptée (OK).")
    assert execute(atelier, Action("check_text", {"text": "Commande envoyée"}), None).check is True

    consigne = execute(atelier, Action("dialog", {"accept": False}), obs)
    assert consigne.ok and consigne.detail == "Prochaine fenêtre JavaScript : réponse Annuler"
    refus = execute(atelier, Action("click", {"ref": envoyer.ref}), obs)
    assert refus.dialog["accepted"] is False and "refusée (Annuler)" in refus.detail
    assert execute(atelier, Action("check_text", {"text": "Envoi annulé"}), None).check is True
    assert execute(atelier, Action("click", {"ref": envoyer.ref}), obs).dialog["accepted"] is True  # retour à OK


def test_fenetre_de_saisie(pw_page):
    pw_page.set_content("""<button onclick="document.title = prompt('Votre prénom ?') || 'vide'">Saisir</button>""")
    obs = observe(pw_page)
    execute(pw_page, Action("dialog", {"accept": True, "text": "Léa"}), obs)
    result = execute(pw_page, Action("click", {"ref": 1}), obs)
    assert result.dialog["text"] == "Léa" and "acceptée avec la saisie « Léa »" in result.detail
    assert pw_page.title() == "Léa"


def test_attente_d_un_texte(atelier, monkeypatch):
    monkeypatch.setattr(browser, "CHECK_TIMEOUT_MS", 300)
    obs = observe(atelier)
    execute(atelier, Action("click", {"ref": find(obs, '[data-test="charger"]').ref}), obs)
    trop_tot = execute(atelier, Action("check_text", {"text": "Stock chargé"}), None)
    assert trop_tot.check is False  # une vérification n'attend que quelques secondes : d'où l'action wait
    start = time.monotonic()
    attente = execute(atelier, Action("wait", {"seconds": 10, "text": "stock chargé"}), None)
    assert attente.ok and attente.value == "Stock chargé" and time.monotonic() - start < 5
    assert attente.check is None  # une attente n'est pas une vérification


def test_attente_sans_resultat(pw_page):
    pw_page.set_content("<p>Rien</p>")
    start = time.monotonic()
    result = execute(pw_page, Action("wait", {"seconds": 1, "text": "Jamais"}), None)
    assert not result.ok and result.detail == "« Jamais » ne s'est pas affiché en 1 s"
    assert execute(pw_page, Action("wait", {"seconds": 1}), None).detail == "Attente de 1 s"
    assert 1.5 < time.monotonic() - start < 6


def test_texte_absent_cherche_aussi_dans_les_cadres(atelier, monkeypatch):
    monkeypatch.setattr(browser, "CHECK_TIMEOUT_MS", 300)
    present = execute(atelier, Action("check_text", {"text": "Votre note ici", "absent": True}), None)
    assert present.check is False  # le texte est affiché, dans le cadre
    assert execute(atelier, Action("check_text", {"text": "Code prêt", "absent": True}), None).check is True


def test_cadres_de_publicite_ignores(pw_page, demo_url):
    """Un cadre de publicité, ou trop petit pour être vu, ne noie pas les vrais éléments de la page."""
    pw_page.goto(f"{demo_url}/index.html")
    pw_page.evaluate("""() => {
        const pub = document.createElement('iframe');
        pub.src = '/doubleclick.net/pub.html';  // page d'erreur 404, mais avec du texte
        document.body.appendChild(pub);
        const mini = document.createElement('iframe');
        mini.srcdoc = '<button>Invisible</button>'; mini.style.width = '10px'; mini.style.height = '10px';
        document.body.appendChild(mini);
        const vrai = document.createElement('iframe');
        vrai.srcdoc = '<button>Visible</button>';
        document.body.appendChild(vrai);
    }""")
    pw_page.wait_for_timeout(500)
    obs = observe(pw_page)
    framed = [e for e in obs.elements if e.frame]
    assert [(e.text, e.frame[0]["css"], e.frame[0]["nth"]) for e in framed] == [("Visible", "iframe", 2)]
    assert obs.frame_texts == [("iframe", "Visible")]


def test_actions_wait_et_dialog_validees():
    assert parse_action('{"action": "wait", "args": {"seconds": 15, "text": "Data loaded"}}').args == {
        "seconds": 15, "text": "Data loaded"}
    assert parse_action('{"action": "dialog", "args": {"accept": false, "text": "ignoré"}}').args == {"accept": False}
    for raw, message in [
        ('{"action": "wait", "args": {"seconds": 45}}', "de 1 à 30 secondes"),
        ('{"action": "wait", "args": {"seconds": 0}}', "de 1 à 30 secondes"),
        ('{"action": "wait", "args": {"seconds": 5, "text": " "}}', "« text » vide"),
        ('{"action": "dialog", "args": {}}', "« accept » manquant"),
        ('{"action": "check_value", "args": {"ref": 1, "value": "([A-Z]", "regex": true}}', "motif « ([A-Z] » invalide"),
    ]:
        with pytest.raises(ValueError, match=re.escape(message)):
            parse_action(raw)
    regex = parse_action('{"action": "check_value", "args": {"ref": 1, "value": "^\\\\d{4}$", "regex": true}}')
    assert regex.args == {"ref": 1, "value": r"^\d{4}$", "regex": True}
