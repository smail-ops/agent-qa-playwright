"""Recherche dans la documentation officielle de Playwright, Selenium et Robot Framework.

Deux usages :
- une question du testeur à Alfred : « comment gérer une iframe avec Robot Framework ? » ;
- l'analyse d'un échec technique : « Timeout 5000ms exceeded », « strict mode violation ».

Comme pour le reste de l'agent, l'IA propose et le programme vérifie :
- les pages lues viennent d'une liste fermée de pages officielles, choisies par le
  programme d'après les mots de la question ou du message d'erreur ;
- le programme découpe ces pages par titres et garde les parties les plus proches du
  sujet ; le modèle reçoit ces extraits numérotés et ne fait que les résumer ;
- les liens montrés au testeur sont ceux des extraits qu'il a cités : il ne peut ni
  inventer une source, ni citer une page qu'il n'a pas reçue.

Aucune recherche web payante : le programme lit lui-même les pages officielles, et la
réponse coûte une requête au modèle. Sans réseau, Alfred donne au moins les liens.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
import urllib.request
from collections import Counter
from http.client import HTTPException
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import quote

from .actions import _extract_json
from .providers import ProviderError

TIMEOUT_S = 8.0
MAX_BYTES = 6_000_000  # le guide de Robot Framework est une seule grande page
MAX_PAGES = 2  # pages lues par question
MAX_EXTRACT = 1500  # caractères gardés par extrait
MAX_EXTRACTS = 4


@dataclass(frozen=True)
class Page:
    """Une page de documentation officielle, et les mots qui la désignent (en français et en anglais)."""

    outil: str
    titre: str
    url: str
    mots: tuple[str, ...]


PAGES = (
    Page("Playwright", "Frames (iframes)", "https://playwright.dev/python/docs/frames",
         ("iframe", "frame", "cadre", "frame_locator", "content_frame", "frame was detached")),
    Page("Playwright", "Dialogs (alert, confirm, prompt)", "https://playwright.dev/python/docs/dialogs",
         ("dialog", "alert", "confirm", "prompt", "fenetre javascript", "beforeunload")),
    Page("Playwright", "Auto-waiting et actionability", "https://playwright.dev/python/docs/actionability",
         ("timeout", "exceeded", "attente", "attendre", "intercepts pointer events", "not visible", "not enabled",
          "not stable", "waiting for", "actionability", "element is outside of the viewport")),
    Page("Playwright", "Locators", "https://playwright.dev/python/docs/locators",
         ("locator", "selecteur", "selector", "strict mode violation", "resolved to", "get_by", "shadow", "nth", "filter",
          "has_text")),
    Page("Playwright", "Assertions", "https://playwright.dev/python/docs/test-assertions",
         ("expect", "assertion", "to_have_text", "to_contain_text", "to_have_value", "to_be_visible", "verification",
          "verifier")),
    Page("Playwright", "Input : saisie, clavier, fichiers, survol, glisser-déposer", "https://playwright.dev/python/docs/input",
         ("fill", "saisir", "saisie", "keyboard", "clavier", "touche", "press", "upload", "fichier", "hover", "survol",
          "survoler", "drag", "glisser", "deposer", "checkbox", "select_option", "set_input_files", "cocher")),
    Page("Playwright", "Pages et nouveaux onglets", "https://playwright.dev/python/docs/pages",
         ("onglet", "tab", "popup", "new page", "nouvelle page", "expect_popup")),
    Page("Playwright", "Screenshots (captures d'écran)", "https://playwright.dev/python/docs/screenshots",
         ("screenshot", "capture", "ecran", "full_page")),
    Page("Playwright", "API : Locator, toutes les méthodes", "https://playwright.dev/python/docs/api/class-locator",
         ("click", "dblclick", "inner_text", "text_content", "get_attribute", "count", "is_visible", "is_checked",
          "scroll_into_view_if_needed", "wait_for", "all_inner_texts")),
    Page("Playwright", "API : Page, toutes les méthodes", "https://playwright.dev/python/docs/api/class-page",
         ("goto", "reload", "go_back", "set_viewport_size", "evaluate", "route", "wait_for_url", "wait_for_load_state",
          "pdf", "recharger", "naviguer")),
    Page("Selenium", "Working with IFrames and frames", "https://www.selenium.dev/documentation/webdriver/interactions/frames/",
         ("iframe", "frame", "cadre", "switch_to", "nosuchframe", "nosuchframeexception")),
    Page("Selenium", "JavaScript alerts, prompts and confirmations",
         "https://www.selenium.dev/documentation/webdriver/interactions/alerts/",
         ("alert", "confirm", "prompt", "dialog", "fenetre javascript", "unexpectedalertpresent", "noalertpresent",
          "unexpectedalertpresentexception", "noalertpresentexception")),
    Page("Selenium", "Working with windows and tabs", "https://www.selenium.dev/documentation/webdriver/interactions/windows/",
         ("onglet", "tab", "window", "switch_to", "nosuchwindow", "nosuchwindowexception", "nouvelle fenetre")),
    Page("Selenium", "Waiting strategies", "https://www.selenium.dev/documentation/webdriver/waits/",
         ("wait", "attente", "attendre", "implicit", "explicit", "timeout", "timeoutexception", "webdriverwait",
          "expected_conditions")),
    Page("Selenium", "Actions API : clavier et souris", "https://www.selenium.dev/documentation/webdriver/actions_api/",
         ("clavier", "keyboard", "touche", "souris", "mouse", "hover", "survol", "drag", "glisser", "actionchains",
          "double clic", "clic droit")),
    Page("Selenium", "Finding web elements (Shadow DOM compris)", "https://www.selenium.dev/documentation/webdriver/elements/finders/",
         ("find_element", "locator", "selecteur", "selector", "shadow", "css", "xpath")),
    Page("Selenium", "Interacting with web elements", "https://www.selenium.dev/documentation/webdriver/elements/interactions/",
         ("click", "send_keys", "clear", "cliquer", "saisir", "taper", "effacer")),
    Page("Selenium", "Information about web elements", "https://www.selenium.dev/documentation/webdriver/elements/information/",
         ("is_displayed", "is_enabled", "is_selected", "get_attribute", "attribut", "affiche")),
    Page("Selenium", "Understanding common errors", "https://www.selenium.dev/documentation/webdriver/troubleshooting/errors/",
         ("nosuchelement", "staleelementreference", "elementnotinteractable", "elementclickintercepted", "invalidselector",
          "nosuchelementexception", "staleelementreferenceexception", "elementnotinteractableexception",
          "elementclickinterceptedexception", "invalidselectorexception", "exception")),
    Page("Robot Framework", "SeleniumLibrary : les mots-clés", "https://robotframework.org/SeleniumLibrary/SeleniumLibrary.html",
         ("seleniumlibrary", "select frame", "unselect frame", "handle alert", "input text", "click element", "wait until",
          "mot cle", "keyword", "iframe", "frame", "cadre", "alert", "localisateur", "locator", "open browser", "shadow")),
    Page("Robot Framework", "BuiltIn : les mots-clés de base", "https://robotframework.org/robotframework/latest/libraries/BuiltIn.html",
         ("builtin", "should be equal", "should contain", "wait until keyword succeeds", "evaluate", "sleep", "run keyword",
          "set variable", "log", "comparer", "equal", "retry", "succeeds", "fail")),
    Page("Robot Framework", "Guide de l'utilisateur", "https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html",
         ("syntaxe", "syntax", "variable", "boucle", "for", "if", "tag", "setup", "teardown", "gherkin", "bdd", "embedded",
          "argument", "resource", "suite")),
    Page("Robot Framework", "Robot Framework et Selenium : bien démarrer", "https://docs.robotframework.org/docs/different_libraries/selenium",
         ("installation", "installer", "install", "webdriver", "chromedriver", "navigateur", "browser")),
)

# Outils cités dans la question. « SeleniumLibrary » désigne Robot Framework : on le cherche en premier.
OUTILS = (
    ("Robot Framework", ("robot framework", "robotframework", "robot", "seleniumlibrary", "builtin")),
    ("Playwright", ("playwright",)),
    ("Selenium", ("selenium", "webdriver")),
)

# Les pages lues quand aucun mot de la question ne désigne une page : les références de chaque outil
PAR_DEFAUT = {
    "Robot Framework": ("https://robotframework.org/SeleniumLibrary/SeleniumLibrary.html",
                        "https://robotframework.org/robotframework/latest/libraries/BuiltIn.html"),
    "Playwright": ("https://playwright.dev/python/docs/api/class-locator", "https://playwright.dev/python/docs/api/class-page"),
    "Selenium": ("https://www.selenium.dev/documentation/webdriver/elements/finders/",
                 "https://www.selenium.dev/documentation/webdriver/elements/interactions/"),
}

# La documentation est en anglais : les mots français trouvent leur équivalent
_SYNONYMES = {
    "iframe": ("frame",), "cadre": ("frame", "iframe"), "fenetre": ("alert", "window", "dialog"),
    "alerte": ("alert",), "confirmation": ("confirm",), "onglet": ("window", "tab"), "attente": ("wait",),
    "attendre": ("wait",), "clavier": ("keyboard", "key"), "touche": ("key", "press"), "souris": ("mouse",),
    "survol": ("hover", "mouse"), "survoler": ("hover", "mouse"), "glisser": ("drag",), "deposer": ("drop",),
    "fichier": ("file", "upload"), "liste": ("list", "select"), "case": ("checkbox",), "texte": ("text",),
    "bouton": ("button",), "lien": ("link",), "champ": ("field", "input"), "cliquer": ("click",), "clic": ("click",),
    "saisir": ("input", "type", "fill"), "taper": ("type", "input", "press"), "effacer": ("clear",), "vider": ("clear",),
    "valeur": ("value",), "navigateur": ("browser",), "localisateur": ("locator",), "selecteur": ("selector", "locator"),
    "composant": ("shadow",), "accepter": ("accept",), "refuser": ("dismiss",), "annuler": ("dismiss", "cancel"),
    "comparer": ("compare", "equal"), "egal": ("equal",), "egale": ("equal",), "contient": ("contain",),
    "contenir": ("contain",), "verifier": ("verify", "should"), "affiche": ("visible", "displayed"),
    "cocher": ("check", "checkbox", "select"), "decocher": ("uncheck",), "selectionner": ("select",),
    "deroulante": ("select", "dropdown"), "deroulant": ("select", "dropdown"), "capture": ("screenshot",),
    "ecran": ("screenshot",), "telecharger": ("download",), "televerser": ("upload",), "defiler": ("scroll",),
    "recharger": ("reload",), "rafraichir": ("reload", "refresh"), "ouvrir": ("open",), "fermer": ("close",),
    "naviguer": ("navigate", "goto"), "executer": ("execute", "run"), "script": ("javascript", "execute"),
    "boucle": ("for", "loop"), "condition": ("if",), "reessayer": ("retry", "succeeds"), "delai": ("timeout",),
    "nombre": ("count",), "compter": ("count",), "longueur": ("length",), "tableau": ("table",), "ligne": ("row",),
    "colonne": ("column",), "cellule": ("cell",), "attribut": ("attribute",), "titre": ("title",),
    "adresse": ("url", "location"), "droit": ("right", "context"), "entree": ("enter",), "erreur": ("error", "exception"),
    "etiquette": ("tag", "label"), "libelle": ("label",), "nouvel": ("new",), "nouveau": ("new",), "nouvelle": ("new",),
    "dictionnaire": ("dictionary",), "parametre": ("argument", "parameter"), "bibliotheque": ("library",),
    "ressource": ("resource",), "importer": ("import", "library"), "installer": ("install",), "charger": ("load",),
    "chargement": ("load",), "cache": ("hidden",), "cachee": ("hidden",), "cle": ("keyword",), "echoue": ("fail",),
    "echec": ("fail",),
}
# Mots français qui ont un autre sens en anglais : on ne cherche que leur traduction (« case » à cocher)
_FAUX_AMIS = {"case"}
# Mots qui ne disent rien du sujet : on ne les cherche pas dans les pages
_VIDES = set("""
comment quel quelle quels quelles que qui quoi est sont faire fait gerer gere utiliser utilise avec dans pour par sur une
des les le la un du de et ou on il elle je tu vous nous en au aux ce cet cette ces son sa ses mon ma mes ton ta tes
how what which the a an and or of to in on with for is are do does can use using pourquoi peut peux veux
robot framework robotframework playwright selenium webdriver python alfred stp s'il plait merci
""".split())
# Une négation dans la question : « Element Should Not Be Visible » peut alors être la bonne réponse
_NEGATIONS = ("pas", "plus", "jamais", "not", "absent", "absente", "disparu", "disparait", "disparaisse", "cache",
              "cachee", "hidden", "invisible")

_WORD = re.compile(r"[a-z0-9_]+")


def normalize(text: str) -> str:
    """Minuscules, sans accents ni ponctuation : « Gérer une iFrame ! » devient « gerer une iframe »."""
    plain = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9_:]+", " ", plain).split())


def _has(text: str, phrase: str) -> bool:
    """La phrase apparaît dans le texte normalisé, en mots entiers (« frame » ne trouve pas « framework »)."""
    return re.search(rf"(?<![a-z0-9_]){re.escape(phrase)}(?![a-z0-9_])", text) is not None


def tools_in(text: str) -> list[str]:
    """Les outils cités : Robot Framework, Playwright, Selenium."""
    plain = normalize(text)
    found = []
    for outil, noms in OUTILS:
        if any(_has(plain, nom) for nom in noms):
            if outil == "Selenium" and "Robot Framework" in found and not _has(plain, "selenium"):
                continue  # « webdriver » dans une question sur Robot Framework
            found.append(outil)
    if "Robot Framework" in found and _has(plain, "seleniumlibrary") and "Selenium" in found:
        found.remove("Selenium")  # « SeleniumLibrary » n'est pas une question sur Selenium seul
    return found


def find_pages(text: str, outils: list[str] | None = None, limit: int = MAX_PAGES, candidates=PAGES) -> list[Page]:
    """Les pages officielles qui parlent du sujet, les plus pertinentes d'abord."""
    plain = normalize(text)
    scored = []
    for index, page in enumerate(candidates):
        if outils and page.outil not in outils:
            continue
        score = sum(1 for mot in page.mots if _has(plain, normalize(mot)))
        if score:
            scored.append((-score, index, page))
    return [page for _, _, page in sorted(scored)[:limit]]


def terms_of(text: str) -> list[str]:
    """Les mots du sujet, et leur équivalent anglais, à chercher dans les pages (« valeurs » cherche aussi value)."""
    terms = []
    for word in _WORD.findall(normalize(text)):
        if len(word) < 3 or word in _VIDES or word.isdigit():
            continue
        base = word[:-1] if word not in _SYNONYMES and word.endswith(("s", "x")) and word[:-1] in _SYNONYMES else word
        for term in ((word,) if word not in _FAUX_AMIS else ()) + _SYNONYMES.get(base, ()):
            if term not in terms:
                terms.append(term)
    return terms


# --- Lecture des pages ----------------------------------------------------------------


@dataclass
class Section:
    """Une partie d'une page : son titre, son ancre et son texte."""

    titre: str
    ancre: str = ""
    texte: str = ""
    mots: Counter | None = field(default=None, repr=False, compare=False)  # calculés à la première recherche


class _Sections(HTMLParser):
    """Texte d'une page de documentation, découpé par titres. Menus, sommaires, scripts et styles ignorés."""

    SKIP = {"script", "style", "nav", "aside", "noscript", "svg", "button", "form", "template", "footer"}
    SITE = {"header"}  # l'en-tête du site ; dans un article, l'en-tête porte le titre
    SKIP_CLASSES = ("table-of-contents", "tableofcontents", "breadcrumb", "pagination", "sidebar", "navbar")
    SKIP_IDS = {"toc", "table-of-contents", "tableofcontents", "contents"}
    HEADINGS = {"h1", "h2", "h3", "h4"}
    BLOCKS = {"p", "li", "pre", "tr", "div", "dd", "dt", "table", "br", "blockquote"}
    VOID = {"br", "img", "input", "meta", "link", "hr", "source", "wbr", "area", "base", "col", "embed", "param", "track"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool]] = []  # (balise, ignorée, contenu principal)
        self.skip = 0
        self.main = 0  # profondeur dans <main> ou <article>
        self.saw_main = False
        self.heading: list[str] | None = None
        self.heading_id = ""
        self.last_id = ""
        self.all: list[Section] = [Section("")]
        self.inside: list[Section] = [Section("")]

    def _ignored(self, tag: str, attrs: dict) -> bool:
        if tag in self.SKIP or (tag in self.SITE and not self.main):
            return True
        if (attrs.get("id") or "").lower() in self.SKIP_IDS:
            return True
        classes = (attrs.get("class") or "").lower().split()
        return any(any(word in name for word in self.SKIP_CLASSES) or name in ("toc", "contents") for name in classes)

    def handle_starttag(self, tag, attrs):
        attrs = {name: value or "" for name, value in attrs}
        ignored, main = self._ignored(tag, attrs), tag in ("main", "article")
        if tag not in self.VOID:
            self.stack.append((tag, ignored, main))
            self.skip += ignored
            self.main += main
            self.saw_main = self.saw_main or main
        if self.skip:
            return
        if attrs.get("id"):
            self.last_id = attrs["id"]
        if tag in self.HEADINGS:
            self.heading, self.heading_id = [], attrs.get("id", "")
        elif tag in self.BLOCKS:
            self._write("\n")

    def handle_endtag(self, tag):
        if tag in self.HEADINGS and self.heading is not None and not self.skip:
            title = " ".join("".join(self.heading).replace("​", "").split())
            anchor, self.heading, self.last_id = self.heading_id or self.last_id, None, ""
            for sections in (self.all, self.inside) if self.main else (self.all,):
                sections.append(Section(title, anchor))
        if not any(name == tag for name, _, _ in self.stack):
            return  # balise fermante sans ouvrante : HTML approximatif
        while self.stack:
            name, ignored, main = self.stack.pop()
            self.skip -= ignored
            self.main -= main
            if name == tag:
                break

    def handle_data(self, data):
        if self.skip:
            return
        if self.heading is not None:
            self.heading.append(data)
        else:
            self._write(data)

    def _write(self, text: str) -> None:
        self.all[-1].texte += text
        if self.main:
            self.inside[-1].texte += text

    def sections(self) -> list[Section]:
        found = self.inside if self.saw_main else self.all
        for section in found:
            section.texte = "\n".join(" ".join(line.split()) for line in section.texte.splitlines() if line.strip())
        return [section for section in found if section.texte or section.titre]


def _html_text(html: str) -> str:
    parser = _Sections()
    parser.feed(html)
    return "\n".join(f"{s.titre}\n{s.texte}" if s.titre else s.texte for s in parser.sections())


def libdoc_sections(html: str) -> list[Section] | None:
    """La documentation d'une bibliothèque Robot Framework (libdoc) : un mot-clé par section, ancre comprise.

    La page est construite en JavaScript à partir d'un objet libdoc = {...} : le texte n'est pas dans le HTML.
    """
    found = re.search(r"libdoc\s*=\s*\{", html)
    if found is None:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(html, found.end() - 1)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    sections = [Section(data.get("name", "Introduction"), "Introduction", _html_text(str(data.get("doc", ""))))]
    for keyword in data.get("keywords", []):
        name = str(keyword.get("name", ""))
        args = ", ".join(str(arg.get("repr", arg.get("name", ""))) if isinstance(arg, dict) else str(arg)
                         for arg in keyword.get("args", []))
        text = (f"Arguments : {args}\n" if args else "") + _html_text(str(keyword.get("doc", "")))
        sections.append(Section(name, name, text))
    return sections


def page_sections(html: str) -> list[Section]:
    """Les sections d'une page : mots-clés d'une documentation libdoc, sinon parties du texte, par titre."""
    found = libdoc_sections(html)
    if found is not None:
        return found
    parser = _Sections()
    parser.feed(html)
    return parser.sections()


def fetch(url: str, timeout: float = TIMEOUT_S) -> str:
    """Le HTML d'une page officielle. Lève OSError sans réseau."""
    request = urllib.request.Request(url, headers={"User-Agent": "agent-qa-playwright (documentation officielle)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (adresses de la liste PAGES)
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read(MAX_BYTES).decode(charset, errors="replace")


_CACHE: dict[str, list[Section]] = {}


def sections_of(page: Page) -> list[Section]:
    """Les sections de la page, lues une fois par lancement d'Alfred ou de l'agent."""
    if page.url not in _CACHE:
        _CACHE[page.url] = page_sections(fetch(page.url))
    return _CACHE[page.url]


# --- Choix des extraits ----------------------------------------------------------------

TITLE_BONUS = 1.5  # un titre qui dit exactement le sujet (« Input Text ») passe avant un titre plus large
RELEVANCE = 0.35  # part du meilleur score en dessous de laquelle une section ne parle plus vraiment du sujet
_FORMS = ("", "s", "es", "ed", "d", "ing", "er", "ers")  # frames, accepted, waiting : la famille du mot cherché


def _words(section: Section) -> Counter:
    if section.mots is None:
        section.mots = Counter(_WORD.findall(normalize(f"{section.titre} {section.texte}")))
    return section.mots


def _found(counts: Counter, term: str) -> int:
    return sum(counts.get(term + end, 0) for end in _FORMS)


def rank(sections: list[Section], text: str) -> list[tuple[float, Section]]:
    """Les sections qui parlent du sujet, avec leur score, les plus proches d'abord.

    Le classement est celui des moteurs de recherche (BM25) : un mot rare dans la page compte plus
    qu'un mot présent partout. S'y ajoute la part du titre que les mots de la question couvrent :
    « Input Text » passe avant « Input Text Into Alert ». Sans négation dans la question,
    « Wait Until Element Is Visible » passe avant « Wait Until Element Is Not Visible ».
    """
    terms, useful = terms_of(text), [section for section in sections if section.texte]
    if not terms or not useful:
        return []
    counts = [_words(section) for section in useful]
    lengths = [sum(c.values()) for c in counts]
    average = sum(lengths) / len(lengths)
    found = {term: [_found(c, term) for c in counts] for term in terms}
    idf = {term: math.log(1 + (len(useful) - df + 0.5) / (df + 0.5))
           for term, values in found.items() for df in [sum(1 for value in values if value)]}
    negation = any(_has(normalize(text), word) for word in _NEGATIONS)
    scored = []
    for i, section in enumerate(useful):
        score = 0.0
        for term, values in found.items():
            if values[i]:
                score += idf[term] * values[i] * 2.2 / (values[i] + 1.2 * (0.5 + 0.5 * lengths[i] / average))
        title = Counter(_WORD.findall(normalize(section.titre)))
        if score and title:
            covered = sum(idf[term] for term in terms if _found(title, term))
            score += TITLE_BONUS * covered / sum(title.values())
        if score and "not" in title:  # « n'est pas visible » : Element Should Not Be Visible ; sinon, le contraire
            score = score * 1.5 if negation else score / 2
        if score:
            scored.append((score, i, section))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [(score, section) for score, _, section in scored]


# --- Extraits envoyés au modèle --------------------------------------------------------


@dataclass
class Extrait:
    numero: int
    page: Page
    section: Section

    @property
    def url(self) -> str:
        return f"{self.page.url}#{quote(self.section.ancre)}" if self.section.ancre else self.page.url

    @property
    def titre(self) -> str:
        nom = self.section.titre
        return f"{self.page.outil} : {self.page.titre}" + (f", {nom}" if nom and nom != self.page.titre else "")

    def to_prompt(self) -> str:
        texte = self.section.texte if len(self.section.texte) <= MAX_EXTRACT else self.section.texte[:MAX_EXTRACT] + " [...]"
        return f"[{self.numero}] {self.titre} ({self.url})\n{texte}"


@dataclass
class Lecture:
    """Ce que le programme a trouvé : les pages choisies, celles qu'il a pu lire, et les extraits retenus."""

    pages: list[Page] = field(default_factory=list)
    extraits: list[Extrait] = field(default_factory=list)
    erreurs: list[str] = field(default_factory=list)


def read(text: str, pages: list[Page]) -> Lecture:
    """Lit les pages (en même temps) et garde les sections les plus proches du sujet, la meilleure d'abord."""
    lecture = Lecture(pages=list(pages))
    if not pages:
        return lecture
    with ThreadPoolExecutor(max_workers=len(pages)) as pool:
        lues = [(page, pool.submit(sections_of, page)) for page in pages]
    candidates = []
    for order, (page, future) in enumerate(lues):
        try:
            sections = future.result()
        except (OSError, ValueError, HTTPException) as exc:  # pas de réseau, page déplacée : on garde le lien
            lecture.erreurs.append(f"{page.url} : {exc}")
            continue
        candidates += [(score, order, page, section) for score, section in rank(sections, text)[:MAX_EXTRACTS]]
    if not candidates:
        return lecture
    best = max(score for score, *_ in candidates)
    for score, _, page, section in sorted(candidates, key=lambda item: (-item[0], item[1])):
        if score >= RELEVANCE * best and len(lecture.extraits) < MAX_EXTRACTS:
            lecture.extraits.append(Extrait(len(lecture.extraits) + 1, page, section))
    return lecture


def cited(lecture: Lecture, numeros: object) -> list[dict]:
    """Les sources citées par le modèle, parmi les extraits reçus : titre et lien, sans doublon."""
    if not isinstance(numeros, list):
        return []
    found, seen = [], set()
    for numero in numeros:
        extrait = next((e for e in lecture.extraits if isinstance(numero, int) and e.numero == numero), None)
        if extrait is not None and extrait.url not in seen:
            seen.add(extrait.url)
            found.append({"titre": extrait.titre, "url": extrait.url})
    return found


def links(pages: list[Page]) -> list[dict]:
    return [{"titre": f"{page.outil} : {page.titre}", "url": page.url} for page in pages]


# --- Réponse à une question du testeur -------------------------------------------------

QUESTION_PROMPT = """Tu es Alfred, l'assistant vocal d'un testeur QA. Il te pose une question technique sur un outil de test.
Tu reçois des extraits numérotés de la documentation officielle. Réponds uniquement à partir de ces extraits.

Réponds uniquement avec un objet JSON : {"reponse": "...", "sources": [1, 2]}
- « reponse » : deux à quatre phrases courtes (400 caractères au plus), en français, avec le ton courtois d'un majordome (vouvoiement), sans tiret cadratin, sans liste ni bloc de code : elle sera lue à voix haute. Cite les mots-clés et les méthodes par leur nom exact (Select Frame, frame_locator).
- N'écris aucune adresse web : le programme affiche lui-même les liens des extraits que tu cites.
- « sources » : les numéros des extraits qui appuient ta réponse.
- Si les extraits ne répondent pas à la question, dis-le simplement et donne "sources": [].
"""

_HOW = ("comment", "how", "quel mot cle", "quelle methode", "quelle commande", "quelle fonction", "syntaxe", "exemple",
        "gerer", "gere", "utiliser", "faire", "difference", "pourquoi", "erreur", "possible", "existe", "marche")


def question_pages(text: str) -> list[Page] | None:
    """Une question technique sur un outil de test, et les pages qui y répondent ; None si ce n'en est pas une.

    Les pages sont choisies d'après les mots de la question ; si aucun ne désigne une page,
    ce sont les références de l'outil (les mots-clés de SeleniumLibrary et de BuiltIn, par exemple).
    """
    outils = tools_in(text)
    if not outils or not any(_has(normalize(text), how) for how in _HOW):
        return None
    terms = terms_of(text)
    if not terms:  # « comment ça va avec Playwright ? »
        return None
    # Les mots de la question (« should be equal », « select frame »), sans les mots vides, puis leurs équivalents
    words = ["-" if word in _VIDES else word for word in _WORD.findall(normalize(text))]
    pages = find_pages(f"{' '.join(words)} {' '.join(terms)}", outils)
    if not pages:
        urls = [url for outil in outils for url in PAR_DEFAUT[outil]][:MAX_PAGES]
        pages = [page for url in urls for page in PAGES if page.url == url]
    return pages or None


def _spoken(text: str) -> str:
    """Une réponse à lire à voix haute : sans adresse web (les liens viennent du programme), sans tiret cadratin."""
    text = re.sub(r"\[([^\]]*)\]\(\s*https?://[^)]*\)", r"\1", str(text))
    text = re.sub(r"\(?\s*https?://[^\s)]*[^\s).,;:!?]\)?", "", text)
    text = re.sub(r"\s+([.,])", r"\1", " ".join(text.replace("\u2014", ",").split()))
    return text.strip()


def answer(provider, text: str, pages: list[Page], ask) -> dict | None:
    """La réponse d'Alfred, tirée des extraits, et les liens des sources qu'il a citées.

    ask : la fonction qui interroge le modèle (consignes, message), pour les quotas et les essais.
    None quand les pages lues ne parlent pas du sujet : Alfred répond alors comme d'habitude.
    """
    lecture = read(text, pages)
    if not lecture.extraits:
        if not lecture.erreurs:
            return None
        # rien de lu : pas de requête au modèle, mais les bonnes pages
        return {"reponse": "Je n'ai pas pu consulter la documentation officielle pour l'instant. "
                           "Voici les pages qui en parlent.", "action": {"type": "aucune"}, "sources": links(pages)}
    consulted = links(list(dict.fromkeys(extrait.page for extrait in lecture.extraits)))
    message = ("## Extraits de la documentation officielle\n\n" + "\n\n".join(e.to_prompt() for e in lecture.extraits)
               + f"\n\n## Question du testeur\n« {text} »\n\nRéponds uniquement en JSON.")
    try:
        raw = ask(provider, QUESTION_PROMPT, message)
    except ProviderError:  # quota, réseau : les bonnes pages restent utiles
        return {"reponse": "Je ne peux pas consulter l'IA pour l'instant. Voici les pages de la documentation officielle "
                           "qui en parlent.", "action": {"type": "aucune"}, "sources": consulted}
    try:
        data = json.loads(_extract_json(raw))
    except (ValueError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    reply = _spoken(data.get("reponse") or "")
    if not reply:
        return {"reponse": "Voici les pages de la documentation officielle qui en parlent.", "action": {"type": "aucune"},
                "sources": consulted}
    # Rien de cité : les extraits ne répondaient pas ; les pages consultées restent un point de départ
    return {"reponse": reply, "action": {"type": "aucune"}, "sources": cited(lecture, data.get("sources")) or consulted}


# --- Analyse d'un échec technique --------------------------------------------------------


def error_pages(error: str) -> list[Page]:
    """Les pages de Playwright qui parlent d'une erreur de l'agent (il pilote le navigateur avec Playwright).

    « Locator.fill: » commence presque chaque erreur : seul le nom de la méthode (fill) dit quelque chose.
    Les références de l'API (toutes les méthodes) ne servent qu'aux questions du testeur.
    """
    guides = [page for page in PAGES if "/docs/api/" not in page.url]
    plain = re.sub(r"\b(?:Locator|Page|Frame|ElementHandle|Keyboard|Mouse)\.(\w+)", r"\1", error)
    return find_pages(plain, ["Playwright"], candidates=guides)
