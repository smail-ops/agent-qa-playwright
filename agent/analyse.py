"""Analyse d'un site à partir de son adresse, sans IA et sans rien modifier.

Alfred s'en sert pour proposer des user stories : le modèle reçoit le relevé
de quelques pages (titres, champs, listes, boutons, liens, texte visible) et,
avec la vision, leur capture d'écran. Le relevé est fait par le programme,
pas par l'IA : il ne coûte aucune requête.

Prudence :
- seuls les liens du même site sont suivis, jamais ceux qui pourraient changer
  quelque chose (déconnexion, suppression, paiement...) ;
- aucun bouton n'est cliqué et aucun formulaire n'est envoyé, sauf celui de
  connexion quand un compte de test est fourni ;
- le mot de passe sert seulement à cette connexion : il n'est ni dans le
  relevé, ni envoyé au modèle, ni enregistré.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from .browser import Element, observe
from .execution import VIEWPORT
from .masquage import mask

MAX_PAGES = 5  # pages analysées au plus, connexion comprise
MAX_TEXT = 1200  # caractères de texte visible gardés par page
MAX_SHOTS = 3  # captures envoyées au modèle avec la vision : les premières pages suffisent
MAX_ITEMS = 25  # champs, boutons ou liens gardés par page

# Liens jamais suivis : ils pourraient déconnecter, supprimer, vider un panier, payer...
RISKY = re.compile(
    r"d[ée]connex|logout|log[ -]?out|sign[ -]?out|d[ée]connecter|supprim|delete|remove|retirer|reset|r[ée]initialis|"
    r"vider|annul|cancel|unsubscribe|d[ée]sinscri|d[ée]sabonn|paiement|payer|\bpay\b|checkout|acheter|\bbuy\b",
    re.IGNORECASE,
)
FILES = re.compile(r"\.(pdf|zip|png|jpe?g|gif|svg|webp|mp4|mp3|docx?|xlsx?|pptx?|csv)$", re.IGNORECASE)

# Ce qu'observe() ne relève pas : les titres, les options des listes et l'adresse des liens.
_DETAILS_JS = r"""
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const headings = Array.from(document.querySelectorAll('h1, h2, h3'))
    .filter(visible).map((h) => clean(h.innerText)).filter(Boolean).slice(0, 12);
  const options = {};
  document.querySelectorAll('select[data-agent-ref]').forEach((s) => {
    options[s.getAttribute('data-agent-ref')] = Array.from(s.options).map((o) => clean(o.text)).filter(Boolean).slice(0, 12);
  });
  const links = Array.from(document.querySelectorAll('a[href]')).filter(visible)
    .map((a) => ({text: clean(a.innerText) || clean(a.getAttribute('aria-label')), href: a.href}))
    .filter((l) => l.text).slice(0, 80);
  return {headings, options, links};
}
"""

# Le champ de l'identifiant : le dernier champ de saisie placé avant celui du mot de passe.
_LOGIN_JS = r"""
() => {
  const shown = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const password = Array.from(document.querySelectorAll('input[type=password]')).find(shown);
  if (!password) return 'aucun';
  const skip = ['password', 'hidden', 'submit', 'button', 'reset', 'checkbox', 'radio', 'image', 'file'];
  const before = Array.from(document.querySelectorAll('input')).filter((i) =>
    shown(i) && !skip.includes((i.type || '').toLowerCase()) &&
    (i.compareDocumentPosition(password) & Node.DOCUMENT_POSITION_FOLLOWING));
  password.setAttribute('data-agent-login', 'password');
  if (!before.length) return 'sans-identifiant';
  before[before.length - 1].setAttribute('data-agent-login', 'user');
  return 'ok';
}
"""


class AnalysisError(Exception):
    """Le site ne peut pas être analysé : adresse injoignable, page vide..."""


@dataclass
class PageSummary:
    """Ce qu'un testeur relève d'une page en la découvrant."""

    url: str
    title: str
    headings: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    buttons: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    text: str = ""
    screenshot: bytes | None = None  # capture JPEG, envoyée au modèle avec la vision

    def to_prompt(self, number: int) -> str:
        lines = [f"Page {number} : {self.title or '(sans titre)'}", f"URL : {self.url}"]
        if self.headings:
            lines.append("Titres : " + " ; ".join(self.headings))
        lines.append("Champs et listes : " + (" ; ".join(self.fields) or "aucun"))
        lines.append("Boutons : " + (" ; ".join(self.buttons) or "aucun"))
        lines.append("Liens : " + (" ; ".join(self.links) or "aucun"))
        lines.append("Texte visible (extrait) : " + (self.text or "(vide)"))
        return "\n".join(lines)


@dataclass
class SiteAnalysis:
    """Le relevé du site : ses premières pages, et ce qu'a donné la connexion."""

    url: str
    pages: list[PageSummary] = field(default_factory=list)
    username: str | None = None  # identifiant du compte de test (jamais le mot de passe)
    login: str = ""  # résultat de la connexion, en une phrase
    logged_in: bool = False

    def to_prompt(self) -> str:
        """Le relevé tel que le modèle le reçoit, données personnelles des pages masquées (sauf l'identifiant fourni)."""
        parts = [f"Site analysé : {self.url}", f"Pages analysées : {len(self.pages)}"]
        if self.username:
            parts.append(f"Compte de test : identifiant « {self.username} ». Le mot de passe ne t'est pas communiqué.")
        if self.login:
            parts.append(self.login)
        for number, page in enumerate(self.pages, 1):
            parts += ["", page.to_prompt(number)]
        return mask("\n".join(parts), self.username or "")

    def images(self) -> list[tuple[str, bytes]]:
        return [("image/jpeg", page.screenshot) for page in self.pages if page.screenshot][:MAX_SHOTS]

    def to_dict(self) -> dict:
        """Résumé pour l'interface : les pages vues, sans les captures."""
        return {
            "url": self.url,
            "login": self.login,
            "logged_in": self.logged_in,
            "pages": [{"url": p.url, "title": p.title, "fields": len(p.fields), "buttons": len(p.buttons),
                       "links": len(p.links)} for p in self.pages],
        }


def _key(url: str) -> str:
    """Deux adresses qui ne diffèrent que par l'ancre (#...) ou la barre finale désignent la même page."""
    return urldefrag(url)[0].rstrip("/")


def _same_site(url: str, start: str) -> bool:
    a, b = urlsplit(url), urlsplit(start)
    return a.scheme in ("http", "https") and a.netloc.lower() == b.netloc.lower()


def _q(text: str) -> str:
    return f"« {text} »"


def _describe_field(element: Element, options: list[str]) -> str:
    kind = (element.type or "").lower()
    name = _q(element.label())
    if element.tag == "select":
        shown = f" : {', '.join(options)}" if options else ""
        return f"liste {name}{shown}"
    if element.tag == "textarea" or element.editable():
        return f"zone de texte {name}"
    if kind == "password":
        return f"champ mot de passe {name}"
    if kind == "checkbox":
        return f"case à cocher {name}"
    if kind == "radio":
        return f"bouton radio {name}"
    if kind in ("email", "number", "date", "tel", "search", "url"):
        return f"champ {kind} {name}"
    return f"champ {name}"


def _limit(items: list[str]) -> list[str]:
    """Sans doublons, dans l'ordre de la page : trois boutons « Ajouter au panier » donnent « (3 fois) »."""
    counts = Counter(items)
    unique = [item if counts[item] == 1 else f"{item} ({counts[item]} fois)" for item in dict.fromkeys(items)]
    return unique if len(unique) <= MAX_ITEMS else unique[:MAX_ITEMS] + [f"et {len(unique) - MAX_ITEMS} autres"]


def summarize(page: Page, start: str, screenshot: bool = False) -> tuple[PageSummary, list[str]]:
    """Relevé de la page actuelle, et les adresses des liens à suivre."""
    observation = observe(page, max_elements=120)
    details = page.evaluate(_DETAILS_JS)
    fields, buttons = [], []
    for element in observation.elements:
        kind = (element.type or "").lower()
        if (element.tag in ("input", "textarea", "select") and kind not in ("submit", "button", "reset", "image")) \
                or element.editable():
            fields.append(_describe_field(element, details["options"].get(str(element.ref), [])))
        elif element.tag != "a":
            buttons.append(_q(element.label()))
    links, to_follow = [], []
    for link in details["links"]:
        href = link["href"]
        if not _same_site(href, start):
            links.append(f"{_q(link['text'])} (autre site)")
            continue
        path = urlsplit(href).path or "/"
        links.append(f"{_q(link['text'])} ({path})")
        if not RISKY.search(link["text"]) and not RISKY.search(href) and not FILES.search(urlsplit(href).path):
            to_follow.append(href)
    text = observation.text if len(observation.text) <= MAX_TEXT else observation.text[:MAX_TEXT] + " [...]"
    shot = None
    if screenshot:
        try:
            shot = page.screenshot(type="jpeg", quality=55)
        except PlaywrightError:
            shot = None
    summary = PageSummary(url=page.url, title=observation.title, headings=details["headings"], fields=_limit(fields),
                          buttons=_limit(buttons), links=_limit(links), text=text, screenshot=shot)
    return summary, to_follow


def login(page: Page, username: str, password: str) -> tuple[bool, str]:
    """Remplit le formulaire de connexion de la page et l'envoie. Renvoie (connecté, phrase pour le relevé)."""
    found = page.evaluate(_LOGIN_JS)
    if found == "aucun":
        return False, "Aucun formulaire de connexion sur la première page : le compte de test n'a pas servi."
    if found == "sans-identifiant":
        return False, "Le formulaire de connexion n'a pas de champ d'identifiant reconnaissable : connexion non tentée."
    before = _key(page.url)
    secret = page.locator("[data-agent-login=password]")
    try:
        page.locator("[data-agent-login=user]").fill(username, timeout=5000)
        secret.fill(password, timeout=5000)
        secret.press("Enter", timeout=5000)
        page.wait_for_load_state("load", timeout=10000)
        page.wait_for_timeout(800)  # une application monopage change d'écran sans recharger
    except PlaywrightError:
        pass
    still_there = False
    try:
        still_there = secret.count() > 0 and secret.first.is_visible()
    except PlaywrightError:
        still_there = False
    if still_there:
        try:
            secret.fill("")  # le mot de passe ne reste pas dans la page
        except PlaywrightError:
            pass
        return False, ("La connexion avec le compte de test a échoué (le formulaire est toujours affiché) : "
                       "seules les pages publiques sont analysées.")
    moved = _key(page.url) != before
    return True, ("Connexion avec le compte de test réussie" + (f" : le site a ouvert {page.url}." if moved else ".")
                  + " Les pages suivantes sont celles d'un utilisateur connecté.")


def explore(page: Page, url: str, username: str | None = None, password: str | None = None, max_pages: int = MAX_PAGES,
            vision: bool = False, on_page: Callable[[PageSummary], None] | None = None) -> SiteAnalysis:
    """Parcourt les premières pages du site, en largeur, sans rien y modifier."""
    analysis = SiteAnalysis(url=url, username=(username or "").strip() or None)
    queue, seen = [url], set()

    def add(summary: PageSummary) -> None:
        analysis.pages.append(summary)
        seen.add(_key(summary.url))
        if on_page is not None:
            on_page(summary)

    while queue and len(analysis.pages) < max_pages:
        target = queue.pop(0)
        if _key(target) in seen:
            continue
        seen.add(_key(target))
        try:
            page.goto(target, wait_until="load", timeout=15000)
        except PlaywrightError as exc:
            if not analysis.pages:  # la première page doit s'ouvrir, sinon il n'y a rien à analyser
                detail = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else exc.__class__.__name__
                raise AnalysisError(f"Impossible d'ouvrir {target} : {detail}") from exc
            continue
        if _key(page.url) != _key(target) and _key(page.url) in seen:
            continue  # redirection vers une page déjà vue
        summary, links = summarize(page, url, vision and len(analysis.images()) < MAX_SHOTS)
        add(summary)
        if len(analysis.pages) == 1 and analysis.username and password:
            analysis.logged_in, analysis.login = login(page, analysis.username, password)
            if analysis.logged_in and len(analysis.pages) < max_pages:
                summary, links_after = summarize(page, url, vision and len(analysis.images()) < MAX_SHOTS)
                add(summary)
                links = links_after + links
        queue += [link for link in links if _key(link) not in seen]
    if not any(page.text or page.fields or page.buttons for page in analysis.pages):
        raise AnalysisError("La page est vide : rien à analyser. Vérifiez l'adresse.")
    return analysis


def analyse_site(url: str, username: str | None = None, password: str | None = None, max_pages: int = MAX_PAGES,
                 vision: bool = False, headed: bool = False,
                 on_page: Callable[[PageSummary], None] | None = None) -> SiteAnalysis:
    """Ouvre un navigateur, analyse le site, puis le referme."""
    if not re.fullmatch(r"https?://[^\s/?#]+\S*", url.strip(), flags=re.IGNORECASE):
        raise AnalysisError("L'adresse doit commencer par http:// ou https://, par exemple https://www.saucedemo.com/")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        try:
            page = browser.new_page(viewport=VIEWPORT)
            return explore(page, url.strip(), username, password, max_pages, vision, on_page)
        finally:
            browser.close()
