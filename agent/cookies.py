"""Bandeaux de cookies : acceptés par le programme, comme le ferait un testeur.

Sur un site européen, un bandeau de consentement (RGPD) couvre souvent la page à
la première visite, et bloque les clics tant qu'on n'y a pas répondu. Ce n'est
pas l'objet du test : le testeur l'accepte et passe à la suite. L'agent fait de
même, sans appel au modèle, avant de regarder la page :

- les bandeaux des outils de consentement courants (Google, OneTrust, Didomi,
  Axeptio, Cookiebot...) sont reconnus à leur bouton « Accepter » ;
- à défaut, un bandeau fait par le site est reconnu à son texte (cookies,
  consentement, traceurs) et à son bouton (« Tout accepter », « J'accepte »,
  « Accept all »). Un bouton qui n'est pas dans un tel bandeau n'est jamais cliqué.

L'outil de consentement s'affiche souvent juste après la page : quand la page en
charge un, l'agent attend son bandeau quelques secondes. Sinon, il ne fait que
regarder, sans rien attendre.

Certains outils (Sourcepoint, TrustArc) affichent leur bandeau dans un cadre
(iframe) posé sur la page : l'agent y cherche aussi le bouton, quand le cadre
se présente comme celui du consentement (son nom, son titre ou son adresse).

L'acceptation est une étape du test, marquée « sans IA » dans le rapport, et les
tests générés font de même : ils acceptent le bandeau s'il s'affiche.

Un cas de test qui porte sur le bandeau garde la main : son titre ou ses
résultats attendus parlent du bandeau (« le bandeau de cookies s'affiche »), ou
il demande de refuser ou de personnaliser les cookies. Le bandeau est alors un
élément comme un autre, que le modèle manie selon les étapes. Une étape qui
demande seulement de l'accepter (« si une fenêtre de consentement aux cookies
s'affiche, cliquer sur Consent ») est faite par le programme.
"""

from __future__ import annotations

import itertools
import re
import time
import unicodedata
from dataclasses import dataclass, field

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from .browser import ActionResult, _frame_path

WAIT_S = 5.0  # attente du bandeau quand la page charge un outil de consentement
MAX_PER_RUN = 3  # un bandeau qui revient sans cesse est laissé au modèle
CLICK_TIMEOUT_MS = 3000
HIDE_TIMEOUT_MS = 3000
MARK = "data-agent-cookies"  # posé sur le bouton trouvé, le temps de cliquer dessus


@dataclass(frozen=True)
class Outil:
    """Un outil de consentement, et son bouton qui accepte tous les cookies."""

    nom: str
    bouton: str  # sélecteur CSS ; hote : l'élément dont le Shadow DOM contient le bouton
    hote: str = ""
    robot: str = ""  # localisateur SeleniumLibrary, quand css: ne suffit pas (Shadow DOM)

    def robot_locator(self) -> str:
        return self.robot or f"css:{self.bouton}"


OUTILS = (
    Outil("Google", "button.fc-cta-consent"),
    Outil("OneTrust", "#onetrust-accept-btn-handler"),
    Outil("Didomi", "#didomi-notice-agree-button"),
    Outil("Axeptio", "#axeptio_btn_acceptAll"),
    Outil("Cookiebot", "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"),
    Outil("Cookiebot", "#CybotCookiebotDialogBodyButtonAccept"),
    Outil("Quantcast", ".qc-cmp2-summary-buttons button[mode='primary']"),
    Outil("tarteaucitron", "#tarteaucitronPersonalize2"),
    Outil("CookieYes", "button.cky-btn-accept"),
    Outil("Complianz", "button.cmplz-accept"),
    Outil("iubenda", "button.iubenda-cs-accept-btn"),
    Outil("Osano", "button.osano-cm-accept-all"),
    Outil("TrustArc", "#truste-consent-button"),
    Outil("Sourcepoint", "button.sp_choice_type_11"),  # dans son cadre (iframe)
    Outil("Usercentrics", "[data-testid='uc-accept-all-button']", "#usercentrics-root",
          "dom:document.querySelector('#usercentrics-root').shadowRoot"
          ".querySelector('[data-testid=\"uc-accept-all-button\"]')"),
)

# Libellés d'un bouton qui accepte, sans accents ni ponctuation : le libellé entier doit y figurer
_ACCEPTE = (
    "accepter", "tout accepter", "accepter tout", "accepter et fermer", "accepter et continuer", "accepter les cookies",
    "accepter tous les cookies", "j'accepte", "j'accepte tout", "j'accepte les cookies", "autoriser", "tout autoriser",
    "autoriser tous les cookies", "autoriser les cookies", "ok", "d'accord", "j'ai compris", "compris", "continuer",
    "accept", "accept all", "accept all cookies", "accept cookies", "allow", "allow all", "allow all cookies",
    "allow cookies", "i accept", "i agree", "agree", "agree and close", "agree and continue", "agree and proceed",
    "consent", "got it", "okay", "yes, i agree", "yes i agree", "understood",
)
_BANDEAU = "cookie|traceur|consent|rgpd|gdpr"  # le bandeau doit en parler

# Un cas de test sur le bandeau lui-même. Dans son titre ou ses résultats attendus : le bandeau, le consentement.
_SUJET = re.compile(r"consentement|\bconsent\b|\brgpd\b|\bgdpr\b|\btraceurs?\b|"
                    r"(bandeau|banniere|fenetre|popup|pop up|message) (de |des |aux |sur les )?cookies?|cookies? (banner|consent)")
# Dans ses étapes : refuser ou personnaliser, ce que le programme ne fait jamais
_REFUS = re.compile(r"(refuser|rejeter|decliner|personnaliser|parametrer|gerer|configurer) (les |tous les |mes |vos )?"
                    r"(cookies?|traceurs|options|preferences|choix)|continuer sans accepter|tout refuser|"
                    r"ne pas (autoriser|accepter|consentir)|do not consent|reject all|manage options|retirer (mon |son |le )?consentement")
_HEADING = re.compile(r"^\s*#+\s*(.*)$")
# Un cadre (iframe) de consentement : son id, son nom, son titre ou son adresse le disent
_CADRE = re.compile(r"cookie|consent|\bcmp\b|privacy|gdpr|rgpd|sourcepoint|sp_message|trustarc|truste|didomi|onetrust|"
                    r"quantcast|usercentrics|cookiebot|consentmanager", re.IGNORECASE)

_FIND_JS = r"""
({outils, labels, words, mark, id, cadre}) => {
  const visible = (el) => {
    const box = el.getBoundingClientRect(), style = getComputedStyle(el);
    return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none'
      && Number(style.opacity || 1) > 0.05;
  };
  const norm = (t) => (t || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase()
    .replace(/[’`´]/g, "'").replace(/[^a-z0-9', ]+/g, ' ').replace(/\s+/g, ' ').trim();
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const shown = (el) => clean(el.tagName === 'INPUT' ? el.value : el.textContent);
  const found = (el, outil) => {
    el.setAttribute(mark, id);
    const aria = clean(el.getAttribute('aria-label'));
    return {outil, texte: aria || shown(el), shown: shown(el), aria, tag: el.tagName.toLowerCase(), id: el.id || ''};
  };
  for (const [index, outil] of outils.entries()) {
    const root = outil.hote ? (document.querySelector(outil.hote) || {}).shadowRoot : document;
    const el = root ? root.querySelector(outil.bouton) : null;
    if (el && visible(el)) return found(el, index);
  }
  // Un bandeau fait par le site : un bouton « Accepter » dans un bloc fixe ou une fenêtre qui parle de cookies
  const accept = new Set(labels), about = new RegExp(words), named = /cookie|consent|gdpr|rgpd|privacy|cmp/i;
  for (const el of document.querySelectorAll('button, a, [role=button], input[type=button], input[type=submit]')) {
    if (!(accept.has(norm(shown(el))) || accept.has(norm(el.getAttribute('aria-label')))) || !visible(el)) continue;
    let box = el.parentElement, banner = null;
    for (let depth = 0; box && box !== document.body && box !== document.documentElement && depth < 15;
         depth++, box = box.parentElement) {
      const style = getComputedStyle(box);
      const name = `${box.id} ${typeof box.className === 'string' ? box.className : ''} ${box.getAttribute('aria-label') || ''}`;
      if (named.test(name) || style.position === 'fixed' || style.position === 'sticky' || box.tagName === 'DIALOG'
          || ['dialog', 'alertdialog'].includes(box.getAttribute('role'))) { banner = box; break; }
    }
    if (!banner && !cadre) continue;  // dans un cadre de consentement, le cadre est le bandeau
    const text = norm(banner ? banner.innerText || banner.textContent : document.body.innerText);
    if (text.length <= 5000 && about.test(text)) return found(el, -1);
  }
  return null;
}
"""

# La page charge-t-elle un outil de consentement ? Son bandeau s'affiche alors souvent après la page.
_TOOL_JS = r"""
() => {
  const w = window;
  if (typeof w.__tcfapi === 'function' || w.Didomi || w.OneTrust || w.Optanon || w.Cookiebot || w.UC_UI
      || w.tarteaucitron || w.axeptioSettings || w._iub || w.CookieConsent || w.__cmp) return true;
  return Array.from(document.scripts).some((s) => /fundingchoices|cookielaw|onetrust|privacy-center|didomi|cookiebot|axept|quantcast|tarteaucitron|cookieyes|complianz|iubenda|osano|trustarc|truste|usercentrics|consentmanager|sourcepoint|cookie/i.test(s.src || ''));
}
"""

_marks = itertools.count(1)


@dataclass
class Bandeau:
    """Un bandeau de cookies affiché, et comment désigner son bouton dans les tests générés."""

    outil: str  # outil de consentement reconnu, "" pour un bandeau fait par le site
    texte: str  # nom du bouton (« Autoriser »)
    playwright: str  # code Playwright qui désigne le bouton, repris par le test pytest généré
    robot: str  # localisateur SeleniumLibrary, repris par les tests Robot Framework générés
    marque: str = ""  # le bouton dans la page, le temps de cliquer dessus
    cadre: list | None = None  # le cadre (iframe) du bandeau : [{"css", "nth", "label"}], comme pour les éléments
    frame: object | None = field(default=None, repr=False, compare=False)  # ce cadre, pour Playwright

    @property
    def nom(self) -> str:
        return f"Bandeau de cookies ({self.outil})" if self.outil else "Bandeau de cookies du site"

    def args(self) -> dict:
        args = {"outil": self.outil, "bouton": self.texte, "playwright": self.playwright, "robot": self.robot}
        if self.cadre:
            args["cadre"] = self.cadre
        return args


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _subject(case_text: str) -> str:
    """Le titre du cas et ses résultats attendus (section « Résultats attendus », lignes Alors d'un scénario Gherkin)."""
    lines, kept, in_results = case_text.strip().splitlines(), [], False
    for index, line in enumerate(lines):
        heading = _HEADING.match(line)
        if heading:
            title = _plain(heading.group(1))
            in_results = title.startswith("resultat")
            if index == 0:
                kept.append(line)
            continue
        if in_results or re.match(r"\s*(alors|then)\b", _plain(line)):
            kept.append(line)
    return _plain(" ".join(kept))


def wanted(case_text: str) -> bool:
    """False quand le cas de test porte sur le bandeau lui-même : il est alors laissé au modèle."""
    return _SUJET.search(_subject(case_text)) is None and _REFUS.search(_plain(case_text)) is None


def _css_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _xpath_string(text: str) -> str:
    if '"' not in text:
        return f'"{text}"'
    if "'" not in text:
        return f"'{text}'"
    return "concat(" + ", '\"', ".join(f'"{part}"' for part in text.split('"')) + ")"


def _selectors(found: dict) -> tuple[str, str]:
    """Code Playwright et localisateur SeleniumLibrary du bouton d'un bandeau fait par le site.

    Playwright le désigne par son rôle et son nom, comme un utilisateur ; Selenium par son texte.
    """
    tag, name, ident = found["tag"], found["texte"], found["id"]
    if re.fullmatch(r"[A-Za-z][\w-]*", ident) and not re.search(r"\d{4}|[0-9a-f]{8}", ident):  # id stable
        return f"page.locator({'#' + ident!r})", f"css:#{ident}"
    if tag == "input":
        css = f"input[value={_css_string(found['shown'])}]"
        return f"page.locator({css!r})", f"css:{css}"
    code = f"page.get_by_role({'link' if tag == 'a' else 'button'!r}, name={name!r}, exact=True)"
    base = tag if tag in ("button", "a") else "[role=button]"
    if found["aria"]:  # le nom vient de aria-label, pas du texte affiché
        return code, f"css:{base}[aria-label={_css_string(found['aria'])}]"
    xpath = f"//{tag}" if tag in ("button", "a") else '//*[@role="button"]'
    return code, f"xpath:{xpath}[normalize-space(.)={_xpath_string(found['shown'])}]"


def _consent_frames(page: Page) -> list[tuple]:
    """Les cadres (iframes) de consentement de la page : (cadre Playwright, chemin), un seul niveau."""
    found = []
    for frame in page.frames:
        if frame.parent_frame is not page.main_frame or frame.is_detached():
            continue
        path = _frame_path(frame)
        if path is None:
            continue  # cadre caché ou en cours de chargement
        hop = path[-1]
        if _CADRE.search(" ".join((hop.get("css") or "", hop.get("label") or "", frame.name or "", frame.url or ""))):
            found.append((frame, path))
    return found


def _search(page: Page, mark: str) -> tuple[dict, object, list | None] | None:
    """Le bouton qui accepte : dans la page, sinon dans un cadre de consentement. (bouton, cadre, chemin du cadre)."""
    options = {"outils": [{"bouton": o.bouton, "hote": o.hote} for o in OUTILS], "labels": list(_ACCEPTE),
               "words": _BANDEAU, "mark": MARK, "id": mark, "cadre": False}
    try:
        found = page.evaluate(_FIND_JS, options)
    except PlaywrightError:  # page en cours de navigation
        found = None
    if found:
        return found, None, None
    for frame, path in _consent_frames(page):
        try:
            found = frame.evaluate(_FIND_JS, {**options, "cadre": True})
        except PlaywrightError:
            continue
        if found:
            return found, frame, path
    return None


def _in_frame(code: str, path: list | None) -> str:
    """Le code Playwright du bouton, dans son cadre : page.frame_locator('iframe[title="..."]').get_by_role(...)."""
    if not path:
        return code
    scope = "page" + "".join(f".frame_locator({hop['css']!r})" + (f".nth({hop['nth']})" if hop["nth"] is not None else "")
                             for hop in path)
    return scope + code[len("page"):]


def find(page: Page, wait_s: float = 0.0) -> Bandeau | None:
    """Le bandeau de cookies affiché, s'il y en a un (dans la page, ou dans un cadre de consentement).

    Avec wait_s, attend qu'il s'affiche quand la page charge un outil de consentement ;
    sans outil de consentement, ne fait que regarder, sans rien attendre.
    """
    deadline, loaded = time.monotonic() + wait_s, None
    while True:
        mark = str(next(_marks))
        search = _search(page, mark)
        if search:
            break
        if loaded is None:
            try:
                loaded = wait_s > 0 and (bool(page.evaluate(_TOOL_JS)) or bool(_consent_frames(page)))
            except PlaywrightError:
                loaded = wait_s > 0
        if not loaded or time.monotonic() >= deadline:
            return None
        page.wait_for_timeout(200)
    found, frame, path = search
    if found["outil"] >= 0:
        outil = OUTILS[found["outil"]]
        code, robot, name = f"page.locator({outil.bouton!r})", outil.robot_locator(), outil.nom
    else:
        (code, robot), name = _selectors(found), ""
    return Bandeau(name, found["texte"] or "Accepter", _in_frame(code, path), robot, f'[{MARK}="{mark}"]', path, frame)


def accept(page: Page, banner: Bandeau) -> ActionResult:
    """Clique sur le bouton qui accepte, puis attend que le bandeau disparaisse."""
    button = (banner.frame or page).locator(banner.marque).first  # dans son cadre, s'il en a un
    try:
        try:
            button.click(timeout=CLICK_TIMEOUT_MS)
        except PlaywrightError:  # encore en train d'apparaître, ou recouvert : le clic JavaScript suffit
            button.evaluate("(el) => el.click()")
        try:
            button.wait_for(state="hidden", timeout=HIDE_TIMEOUT_MS)
        except PlaywrightError:
            if banner.frame is None or not banner.frame.is_detached():  # un cadre retiré de la page : bandeau fermé
                raise
    except PlaywrightError as exc:
        reason = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else exc.__class__.__name__
        return ActionResult(False, f"{banner.nom} : le bouton « {banner.texte} » n'a pas fermé le bandeau ({reason}).")
    try:
        page.wait_for_load_state("load", timeout=5000)  # certains outils rechargent la page après le choix
    except PlaywrightError:
        pass
    return ActionResult(True, f"{banner.nom} accepté par le programme, sans IA : « {banner.texte} ».")


def dismiss(page: Page, wait_s: float = 0.0) -> Bandeau | None:
    """Trouve et accepte le bandeau, s'il y en a un. Renvoie le bandeau accepté."""
    banner = find(page, wait_s)
    if banner is None:
        return None
    return banner if accept(page, banner).ok else None
