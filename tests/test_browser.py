"""Observation de la page et exécution des actions sur le site de démo."""

from agent import browser
from agent.actions import Action
from agent.browser import execute, observe


def test_observation_page_de_connexion(pw_page, demo_url):
    pw_page.goto(f"{demo_url}/index.html")
    obs = observe(pw_page)
    selectors = [e.selector for e in obs.elements]
    assert selectors == ["#user-name", "#password", '[data-test="login-button"]']
    assert obs.elements[2].text == "Connexion"
    assert "[1] input" in obs.to_prompt()
    assert "Boutique Démo" in obs.title


def test_elements_identiques_numerotes(pw_page, demo_url):
    """Trois boutons « Ajouter au panier » : le script devra préciser lequel avec nth."""
    pw_page.goto(f"{demo_url}/inventory.html")
    buttons = [e for e in observe(pw_page).elements if e.tag == "button"]
    assert [b.selector for b in buttons] == ['button:has-text("Ajouter au panier")'] * 3
    assert [b.nth for b in buttons] == [0, 1, 2]
    assert buttons[1].xpath == '(//button[contains(normalize-space(.), "Ajouter au panier")])[2]'


def test_chaque_xpath_designe_le_bon_element(pw_page, demo_url):
    """Le XPath calculé pour Selenium doit désigner exactement l'élément observé."""
    for page_name in ("index.html", "inventory.html"):
        pw_page.goto(f"{demo_url}/{page_name}")
        for element in observe(pw_page).elements:
            located = pw_page.locator(f"xpath={element.xpath}")
            assert located.count() == 1, element.xpath
            assert located.get_attribute("data-agent-ref") == str(element.ref)


def test_xpath_avec_guillemets(pw_page):
    """Un texte contenant les deux types de guillemets passe par concat()."""
    pw_page.set_content("""<button>Dire "oui" et l'écrire</button>""")
    element = observe(pw_page).elements[0]
    assert element.xpath.startswith("//button[contains(normalize-space(.), concat(")
    assert pw_page.locator(f"xpath={element.xpath}").count() == 1


def test_saisie_clic_et_verifications(pw_page, demo_url):
    pw_page.goto(f"{demo_url}/index.html")
    obs = observe(pw_page)
    assert execute(pw_page, Action("fill", {"ref": 1, "text": "standard_user"}), obs).ok
    assert execute(pw_page, Action("fill", {"ref": 2, "text": "secret_sauce"}), obs).ok
    click = execute(pw_page, Action("click", {"ref": 3}), obs)
    assert click.ok and click.element.selector == '[data-test="login-button"]'

    assert execute(pw_page, Action("check_url", {"contains": "inventory"}), None).check is True
    assert execute(pw_page, Action("check_text", {"text": "produits"}), None).check is True  # casse ignorée
    assert execute(pw_page, Action("check_text", {"text": "Paiement validé"}), None).check is False


def test_texte_cache_non_pris_en_compte(pw_page, demo_url):
    """Le message d'erreur existe dans le HTML mais il est caché : la vérification doit échouer."""
    pw_page.goto(f"{demo_url}/index.html")
    result = execute(pw_page, Action("check_text", {"text": "mot de passe incorrect"}), None)
    assert result.check is False


def test_reference_inconnue(pw_page, demo_url):
    pw_page.goto(f"{demo_url}/index.html")
    result = execute(pw_page, Action("click", {"ref": 42}), observe(pw_page))
    assert not result.ok and "[42]" in result.detail


def test_verifications_negatives(pw_page, demo_url):
    """Avec « absent », la vérification réussit si le texte ou l'URL n'apparaissent pas."""
    pw_page.goto(f"{demo_url}/index.html")
    hidden_error = execute(pw_page, Action("check_text", {"text": "mot de passe incorrect", "absent": True}), None)
    assert hidden_error.check is True  # message caché : il compte comme absent
    assert execute(pw_page, Action("check_url", {"contains": "inventory", "absent": True}), None).check is True

    visible = execute(pw_page, Action("check_text", {"text": "Boutique", "absent": True}), None)
    assert visible.check is False and "devrait être absent" in visible.detail


# --- Vérifications plus riches : ordre, nombre, valeur, état ----------------------------

def element_ref(observation, selector):
    return next(e.ref for e in observation.elements if e.selector == selector)


def test_verification_de_l_ordre(pw_page, demo_url, monkeypatch):
    """Après le tri « Prix croissant », la lampe passe avant le t-shirt, puis le sac."""
    monkeypatch.setattr(browser, "CHECK_TIMEOUT_MS", 300)  # un échec attendu ne fait pas attendre 3 s
    pw_page.goto(f"{demo_url}/inventory.html")
    tri = Action("check_order", {"texts": ["Lampe de vélo", "T-shirt", "Sac à dos"]})
    avant = execute(pw_page, tri, None)
    assert avant.check is False and avant.detail == "Ordre non respecté : « Sac à dos » n'apparaît pas après « T-shirt »"

    obs = observe(pw_page)
    assert execute(pw_page, Action("select", {"ref": element_ref(obs, '[data-test="sort"]'), "value": "Prix croissant"}), obs).ok
    apres = execute(pw_page, tri, None)
    assert apres.check is True and apres.detail == "Ordre respecté : « Lampe de vélo », puis « T-shirt », puis « Sac à dos »"
    absent = execute(pw_page, Action("check_order", {"texts": ["Lampe de vélo", "Casque"]}), None)
    assert absent.check is False and "« Casque » absent de la page" in absent.detail


def test_verification_du_nombre(pw_page, demo_url, monkeypatch):
    monkeypatch.setattr(browser, "CHECK_TIMEOUT_MS", 300)
    pw_page.goto(f"{demo_url}/inventory.html")
    assert execute(pw_page, Action("check_count", {"text": "ajouter au panier", "count": 3}), None).check is True
    obs = observe(pw_page)
    execute(pw_page, Action("click", {"ref": element_ref(obs, 'button:has-text("Ajouter au panier")')}), obs)
    assert execute(pw_page, Action("check_count", {"text": "Retirer", "count": 1}), None).check is True
    faux = execute(pw_page, Action("check_count", {"text": "Ajouter au panier", "count": 3}), None)
    assert faux.check is False and faux.detail == "« Ajouter au panier » apparaît 2 fois au lieu de 3"
    assert execute(pw_page, Action("check_count", {"text": "Paiement", "count": 0}), None).check is True


def test_verification_de_la_valeur(pw_page, demo_url, monkeypatch):
    monkeypatch.setattr(browser, "CHECK_TIMEOUT_MS", 300)
    pw_page.goto(f"{demo_url}/index.html")
    obs = observe(pw_page)
    execute(pw_page, Action("fill", {"ref": 1, "text": "standard_user"}), obs)
    execute(pw_page, Action("fill", {"ref": 2, "text": "secret_sauce"}), obs)
    ok = execute(pw_page, Action("check_value", {"ref": 1, "value": "standard_user"}), obs)
    assert ok.check is True and ok.detail == "« Identifiant » contient « standard_user »"
    ko = execute(pw_page, Action("check_value", {"ref": 1, "value": "autre"}), obs)
    assert ko.check is False and ko.detail == "« Identifiant » contient « standard_user » au lieu de « autre »"
    secret = execute(pw_page, Action("check_value", {"ref": 2, "value": "mauvais"}), obs)
    assert secret.check is False and "secret_sauce" not in secret.detail and "mauvais" not in secret.detail

    pw_page.goto(f"{demo_url}/commande.html")
    obs = observe(pw_page)
    liste = element_ref(obs, '[data-test="livraison"]')
    execute(pw_page, Action("select", {"ref": liste, "value": "relais"}), obs)
    par_libelle = execute(pw_page, Action("check_value", {"ref": liste, "value": "Point relais"}), obs)
    assert par_libelle.check is True and par_libelle.value == "relais"  # valeur réelle de l'option, pour les tests générés
    assert execute(pw_page, Action("check_value", {"ref": liste, "value": "relais"}), obs).check is True
    assert execute(pw_page, Action("check_value", {"ref": element_ref(obs, "#prenom"), "value": ""}), obs).detail == "« Prénom » est vide"


def test_verification_de_l_etat(pw_page, demo_url, monkeypatch):
    """Le bouton « Continuer » reste grisé tant que les conditions ne sont pas acceptées."""
    monkeypatch.setattr(browser, "CHECK_TIMEOUT_MS", 300)
    pw_page.goto(f"{demo_url}/commande.html")
    obs = observe(pw_page)
    bouton, cgu = element_ref(obs, '[data-test="continuer"]'), element_ref(obs, '[data-test="cgu"]')

    def etat(ref, state):
        return execute(pw_page, Action("check_state", {"ref": ref, "state": state}), obs)

    assert etat(bouton, "disabled").check is True
    actif = etat(bouton, "enabled")
    assert actif.check is False and actif.detail == "« Continuer » n'est pas actif"
    assert etat(cgu, "unchecked").check is True
    execute(pw_page, Action("click", {"ref": cgu}), obs)
    assert etat(cgu, "checked").check is True
    assert etat(bouton, "enabled").detail == "« Continuer » est actif"


def test_verification_attend_le_changement(pw_page):
    """Comme expect de Playwright, la vérification laisse à la page le temps de changer."""
    pw_page.set_content("""
        <button id="envoyer" disabled>Envoyer</button>
        <ul id="liste"><li>B</li><li>A</li></ul>
        <script>
          setTimeout(() => { document.getElementById('envoyer').disabled = false; }, 300);
          setTimeout(() => { document.getElementById('liste').innerHTML = '<li>A</li><li>B</li>'; }, 900);
        </script>""")
    obs = observe(pw_page)
    assert execute(pw_page, Action("check_state", {"ref": 1, "state": "enabled"}), obs).check is True
    assert execute(pw_page, Action("check_order", {"texts": ["A", "B"]}), None).check is True


def test_nom_lu_dans_le_label(pw_page, demo_url):
    """Le texte du <label> associé aide le modèle à reconnaître le champ, sans le contenu de la liste."""
    pw_page.goto(f"{demo_url}/inventory.html")
    tri = next(e for e in observe(pw_page).elements if e.tag == "select")
    assert tri.attrs["label"] == "Trier" and tri.label() == "Trier" and 'label="Trier"' in tri.describe()
    pw_page.goto(f"{demo_url}/commande.html")
    cgu = next(e for e in observe(pw_page).elements if e.type == "checkbox")
    assert cgu.label() == "J'accepte les conditions générales de vente"


def test_case_transparente_observee(pw_page):
    """Une case à cocher transparente posée sur un habillage reste visible pour l'agent."""
    pw_page.set_content("""
        <label style="position: relative; display: inline-block; width: 120px; height: 24px">
          <input type="checkbox" id="newsletter" style="opacity: 0; position: absolute; inset: 0">
          <span>Newsletter</span>
        </label>
        <a href="#" style="opacity: 0">Lien caché</a>""")
    selectors = [e.selector for e in observe(pw_page).elements]
    assert selectors == ["#newsletter"]
