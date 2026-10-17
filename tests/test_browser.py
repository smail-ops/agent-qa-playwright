"""Observation de la page et exécution des actions sur le site de démo."""

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
