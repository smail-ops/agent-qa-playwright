"""Recherche dans la documentation officielle : pages choisies, lecture, extraits et liens contrôlés."""

import json

import pytest

from agent import documentation
from agent.documentation import answer, error_pages, page_sections, question_pages, rank, read
from agent.providers import ProviderError, ScriptedProvider

SELENIUMLIBRARY = "https://robotframework.org/SeleniumLibrary/SeleniumLibrary.html"
BUILTIN = "https://robotframework.org/robotframework/latest/libraries/BuiltIn.html"
INPUT = "https://playwright.dev/python/docs/input"

# Une page de playwright.dev (Docusaurus) : menus, sommaire et pied de page autour de l'article
DOCUSAURUS = """<html><head><script>var analytics = 1;</script></head><body>
<nav class="navbar"><a href="/">Playwright</a> Docs API Community</nav>
<aside class="theme-doc-sidebar-container"><ul><li>Frames</li><li>Dialogs</li><li>Input</li></ul></aside>
<main class="docMainContainer"><div class="row"><div class="col"><article>
<nav class="theme-doc-breadcrumbs"><ul><li>Guides</li></ul></nav>
<header><h1>Input</h1></header>
<h2 class="anchor" id="introduction">Introduction<a class="hash-link" href="#introduction">​</a></h2>
<p>Playwright can interact with HTML Input elements such as text inputs, checkboxes, radio buttons and select options.</p>
<h2 class="anchor" id="text-input">Text input<a class="hash-link" href="#text-input">​</a></h2>
<p>Using locator.fill() is the easiest way to fill out the form fields. It focuses the element and triggers an input
event with the entered text. It works for &lt;input&gt;, &lt;textarea&gt; and [contenteditable] elements.</p>
<pre><code>page.get_by_role("textbox").fill("Peter")</code></pre>
<h2 class="anchor" id="hover">Hover<a class="hash-link" href="#hover">​</a></h2>
<p>Use locator.hover() to move the mouse over an element.</p>
<footer class="theme-doc-footer docusaurus-mt-lg"><a href="https://github.com/microsoft/playwright/edit/main/docs/src/input.md">Edit this page</a></footer>
</article></div><div class="col col--3"><div class="tableOfContents"><ul class="table-of-contents">
<li>Introduction</li><li>Text input fill fill fill</li><li>Hover</li></ul></div></div></div></main>
<footer>Copyright Microsoft fill</footer></body></html>"""

# La documentation des mots-clés de SeleniumLibrary : construite en JavaScript à partir de l'objet libdoc
LIBDOC = ('<html><body><div id="root"></div><script type="text/javascript">libdoc = ' + json.dumps({
    "name": "SeleniumLibrary",
    "doc": "<h3>Locating elements</h3><p>All keywords that need to interact with an element take a locator.</p>",
    "keywords": [
        {"name": "Select Frame", "args": [{"name": "locator", "repr": "locator"}],
         "doc": "<p>Sets frame identified by <code>locator</code> as the current frame.</p>"
                "<p>Works both with frames and iframes. Use Unselect Frame to cancel the frame selection.</p>"},
        {"name": "Unselect Frame", "args": [], "doc": "<p>Sets the main frame as the current frame.</p>"},
        {"name": "Handle Alert", "args": [{"repr": "action=ACCEPT"}, {"repr": "timeout=None"}],
         "doc": "<p>Handles the current alert and returns its message.</p>"},
    ],
}) + ";</script></body></html>")

# Le guide de l'utilisateur de Robot Framework (docutils) : sommaire en tête, ancres sur les sections
DOCUTILS = """<html><body><div class="document" id="robot-framework-user-guide">
<h1 class="title">Robot Framework User Guide</h1>
<div class="contents topic" id="table-of-contents"><p class="topic-title">Table of Contents</p>
<ul><li><a href="#for-loops">FOR loops</a></li><li><a href="#if-else">IF/ELSE</a></li></ul></div>
<div class="section" id="for-loops"><h3><a class="toc-backref" href="#id12">FOR loops</a></h3>
<p>Repeating same actions several times is quite a common need in test automation.</p>
<pre class="code robotframework literal-block">FOR    ${animal}    IN    cat    dog</pre></div>
</div></body></html>"""

# Des mots-clés aux titres proches : le classement doit trouver celui qui répond à la question
MOTS_CLES = [
    ("Alert Should Be Present", "Verifies that an alert is present and by default, accepts it. Fails if no alert is present."),
    ("Handle Alert", "Handles the current alert and returns its message. By default, the alert is accepted, but this can "
                     "be controlled with the action argument: ACCEPT, DISMISS or LEAVE."),
    ("Input Password", "Types the given password into the text field identified by locator. The password is not logged."),
    ("Input Text", "Types the given text into the text field identified by locator. When clear is true, the input element "
                   "is cleared before the text is typed into the element."),
    ("Input Text Into Alert", "Types the given text into an input field in an alert. The alert is accepted by default."),
    ("Select Frame", "Sets frame identified by locator as the current frame. Works both with frames and iframes."),
    ("Wait Until Element Is Not Visible", "Waits until the element locator is not visible. Fails if timeout expires before "
                                          "the element is not visible."),
    ("Wait Until Element Is Visible", "Waits until the element locator is visible. Fails if timeout expires before the "
                                      "element is visible."),
]

PAGES_EXEMPLE = {SELENIUMLIBRARY: LIBDOC, INPUT: DOCUSAURUS}


@pytest.fixture
def en_ligne(monkeypatch):
    """La documentation répond, avec les pages d'exemple ci-dessus."""
    lues = []

    def fetch(url, timeout=None):
        lues.append(url)
        if url not in PAGES_EXEMPLE:
            raise OSError("page absente des exemples")
        return PAGES_EXEMPLE[url]

    monkeypatch.setattr(documentation, "fetch", fetch)
    return lues


@pytest.mark.parametrize("question, premiere", [
    ("Alfred, comment on gère une iframe avec Robot Framework ?", "https://robotframework.org/SeleniumLibrary/SeleniumLibrary.html"),
    ("comment accepter une alerte avec Selenium", "https://www.selenium.dev/documentation/webdriver/interactions/alerts/"),
    ("Comment faire une boucle en Robot Framework ?", "https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html"),
    ("comment survoler un menu avec Playwright ?", "https://playwright.dev/python/docs/input"),
    ("pourquoi j'ai une StaleElementReferenceException avec Selenium",
     "https://www.selenium.dev/documentation/webdriver/troubleshooting/errors/"),
    ("comment ouvrir un nouvel onglet avec Playwright", "https://playwright.dev/python/docs/pages"),
    ("comment réessayer un mot-clé qui échoue avec Robot Framework ?", BUILTIN),
    ("comment compter les lignes d'un tableau avec Playwright ?", "https://playwright.dev/python/docs/api/class-locator"),
    # aucun mot ne désigne une page : les références de l'outil, ici les mots-clés de SeleniumLibrary et de BuiltIn
    ("comment taper du texte dans un champ avec Robot Framework ?", SELENIUMLIBRARY),
])
def test_pages_choisies_pour_une_question(question, premiere):
    pages = question_pages(question)
    assert pages and pages[0].url == premiere


@pytest.mark.parametrize("phrase", [
    "quelle heure est-il ?",
    "pourquoi le dernier test a échoué ?",  # une question sur les résultats, pas sur un outil
    "lance le cas 3 avec Playwright",  # une demande, pas une question
    "comment ça va ?",
    "comment ça va avec Playwright ?",  # un outil, mais aucun sujet
])
def test_pas_une_question_sur_un_outil(phrase):
    assert question_pages(phrase) is None


@pytest.mark.parametrize("erreur, page", [
    ("Erreur Playwright : Timeout 5000ms exceeded.", "https://playwright.dev/python/docs/actionability"),
    ("Erreur Playwright : Locator.click: Error: strict mode violation: locator('button') resolved to 3 elements",
     "https://playwright.dev/python/docs/locators"),
    ("Erreur Playwright : Frame was detached", "https://playwright.dev/python/docs/frames"),
    ("Erreur Playwright : Locator.fill: Error: Input of type \"submit\" cannot be filled", "https://playwright.dev/python/docs/input"),
])
def test_pages_pour_une_erreur_de_playwright(erreur, page):
    assert error_pages(erreur)[0].url == page


def test_lecture_d_une_page_de_playwright():
    """Le texte de l'article, découpé par titres avec leur ancre ; ni menus, ni sommaire, ni pied de page."""
    sections = page_sections(DOCUSAURUS)
    assert [(s.titre, s.ancre) for s in sections] == [("Input", ""), ("Introduction", "introduction"),
                                                     ("Text input", "text-input"), ("Hover", "hover")]
    texte = " ".join(s.texte for s in sections)
    assert 'page.get_by_role("textbox").fill("Peter")' in texte and "<input>, <textarea>" in texte
    assert "Copyright" not in texte and "Docs API" not in texte and "fill fill" not in texte and "analytics" not in texte
    assert "Edit this page" not in texte  # le pied de l'article


def test_lecture_du_guide_de_robot_framework():
    """Une page docutils : le sommaire en tête est ignoré, l'ancre d'une partie est celle de sa section."""
    sections = page_sections(DOCUTILS)
    assert [(s.titre, s.ancre) for s in sections] == [("Robot Framework User Guide", "robot-framework-user-guide"),
                                                     ("FOR loops", "for-loops")]
    assert "Table of Contents" not in " ".join(s.texte for s in sections)
    assert sections[1].texte.startswith("Repeating same actions")


def test_lecture_d_une_documentation_de_mots_cles():
    """La page de SeleniumLibrary est construite en JavaScript : on lit l'objet libdoc, un mot-clé par section."""
    sections = page_sections(LIBDOC)
    assert [s.titre for s in sections] == ["SeleniumLibrary", "Select Frame", "Unselect Frame", "Handle Alert"]
    assert sections[1].ancre == "Select Frame" and sections[1].texte.startswith("Arguments : locator\nSets frame identified")
    assert sections[3].texte.startswith("Arguments : action=ACCEPT, timeout=None")


def test_reponse_tiree_des_extraits(en_ligne):
    """Le modèle ne reçoit que des extraits numérotés, et les liens affichés sont ceux des extraits qu'il cite."""
    envoye = {}

    def ask(provider, system, user):
        envoye.update(system=system, user=user)
        return '{"reponse": "Utilisez le mot-clé Select Frame, puis Unselect Frame.", "sources": [1, 9, "2"]}'

    question = "Comment gérer une iframe avec Robot Framework ?"
    reponse = answer(None, question, question_pages(question), ask)
    assert reponse["reponse"] == "Utilisez le mot-clé Select Frame, puis Unselect Frame."
    assert reponse["sources"] == [{"titre": "Robot Framework : SeleniumLibrary : les mots-clés, Select Frame",
                                   "url": SELENIUMLIBRARY + "#Select%20Frame"}]  # 9 n'existe pas, « 2 » n'est pas un numéro
    assert "Réponds uniquement à partir de ces extraits" in envoye["system"]
    assert "[1] Robot Framework : SeleniumLibrary : les mots-clés, Select Frame" in envoye["user"]
    assert "« Comment gérer une iframe avec Robot Framework ? »" in envoye["user"]
    assert en_ligne == [SELENIUMLIBRARY]


def test_page_lue_une_seule_fois(en_ligne):
    question = "Comment gérer une iframe avec Robot Framework ?"
    for _ in range(2):
        answer(None, question, question_pages(question), lambda *args: '{"reponse": "Oui.", "sources": [1]}')
    assert en_ligne == [SELENIUMLIBRARY]


def test_sans_reseau_alfred_donne_les_liens():
    """Pas de réseau : aucune requête au modèle, mais les bonnes pages officielles."""
    def ask(*args):
        raise AssertionError("le modèle ne doit pas être appelé")

    question = "Comment gérer une iframe avec Robot Framework ?"
    reponse = answer(None, question, question_pages(question), ask)
    assert reponse["reponse"].startswith("Je n'ai pas pu consulter la documentation officielle")
    assert reponse["sources"] == [{"titre": "Robot Framework : SeleniumLibrary : les mots-clés", "url": SELENIUMLIBRARY}]


def test_quota_epuise_alfred_donne_les_liens(en_ligne):
    def ask(*args):
        raise ProviderError("quota du jour épuisé")

    question = "Comment gérer une iframe avec Robot Framework ?"
    reponse = answer(None, question, question_pages(question), ask)
    assert reponse["reponse"].startswith("Je ne peux pas consulter l'IA pour l'instant")
    assert reponse["sources"][0]["url"] == SELENIUMLIBRARY


def test_conversation_avec_alfred(en_ligne, tmp_path):
    """Une question sur un outil passe par la documentation : une seule requête, avec d'autres consignes."""
    from alfred.conversation import converse

    class Gemini(ScriptedProvider):
        name, model = "gemini", "faux modèle"

    modele = Gemini([{"raw": '{"reponse": "Avec Select Frame.", "sources": [1]}'}])
    reponse = converse(modele, "comment on gère une iframe avec Robot Framework ?", [], "CONTEXTE", [], tmp_path,
                       "https://www.saucedemo.com/")
    assert reponse["reponse"] == "Avec Select Frame." and reponse["action"] == {"type": "aucune"}
    assert reponse["sources"][0]["url"] == SELENIUMLIBRARY + "#Select%20Frame"
    assert modele.index == 1  # une seule requête


@pytest.mark.parametrize("question, attendu", [
    ("comment taper du texte dans un champ avec Robot Framework ?", "Input Text"),  # pas « Input Text Into Alert »
    ("comment accepter une alerte avec Robot Framework ?", "Handle Alert"),
    ("comment attendre qu'un élément soit visible avec Robot Framework ?", "Wait Until Element Is Visible"),
    ("comment attendre qu'un élément ne soit plus visible avec Robot Framework ?", "Wait Until Element Is Not Visible"),
    ("comment gérer une iframe avec Robot Framework ?", "Select Frame"),
])
def test_classement_des_sections(question, attendu):
    sections = [documentation.Section(nom, nom, texte) for nom, texte in MOTS_CLES]
    assert rank(sections, question)[0][1].titre == attendu


def test_une_page_injoignable_n_empeche_pas_les_autres(en_ligne):
    """Deux pages lues en même temps ; celle qui ne répond pas laisse les extraits de l'autre."""
    question = "comment gérer une iframe avec Playwright et Robot Framework ?"
    lecture = read(question, question_pages(question))
    assert [page.outil for page in lecture.pages] == ["Playwright", "Robot Framework"]
    assert lecture.extraits[0].titre == "Robot Framework : SeleniumLibrary : les mots-clés, Select Frame"
    assert len(lecture.erreurs) == 1 and lecture.erreurs[0].startswith("https://playwright.dev/python/docs/frames : ")


def test_ni_adresse_inventee_ni_reponse_sans_source(en_ligne):
    """Les adresses écrites par le modèle disparaissent ; s'il ne cite rien, les pages consultées restent."""
    def ask(*args):
        return ('{"reponse": "Utilisez [Select Frame](https://exemple.invalid/x) \u2014 voir https://exemple.invalid/y.", '
                '"sources": []}')

    question = "Comment gérer une iframe avec Robot Framework ?"
    reponse = answer(None, question, question_pages(question), ask)
    assert reponse["reponse"] == "Utilisez Select Frame, voir."
    assert reponse["sources"] == [{"titre": "Robot Framework : SeleniumLibrary : les mots-clés", "url": SELENIUMLIBRARY}]


def test_documentation_muette_alfred_repond_comme_d_habitude(monkeypatch, tmp_path):
    """La page lue ne parle pas du sujet : pas d'extrait, la phrase suit le chemin habituel de la conversation."""
    from alfred.conversation import converse

    monkeypatch.setattr(documentation, "fetch", lambda url, timeout=None: "<main><h2 id='c'>Cookies</h2><p>Add Cookie.</p></main>")
    question = "comment gérer une iframe avec Robot Framework ?"
    assert answer(None, question, question_pages(question), lambda *args: "{}") is None

    class Gemini(ScriptedProvider):
        name, model = "gemini", "faux modèle"

    modele = Gemini([{"raw": '{"reponse": "Avec Select Frame, à ma connaissance.", "action": {"type": "aucune"}}'}])
    reponse = converse(modele, question, [], "CONTEXTE", [], tmp_path, "https://www.saucedemo.com/")
    assert reponse == {"reponse": "Avec Select Frame, à ma connaissance.", "action": {"type": "aucune"}}

