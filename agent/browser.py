"""Tout ce qui touche au navigateur : observer la page et exécuter les actions.

Observer : on liste les éléments interactifs visibles (champs, boutons, liens)
et on les numérote. Le modèle désigne ensuite un élément par son numéro
(« ref ») au lieu d'inventer un sélecteur, ce qui évite les hallucinations.

L'observation entre aussi dans les composants web (Shadow DOM ouvert) et dans
les cadres (iframes), et repère les éléments cliquables sans rôle, comme les
options d'une liste déroulante « maison » faite de div. Un éditeur de texte
dans une iframe ou un générateur dans un composant restent visibles pour
l'agent.

Pour chaque élément, on calcule aussi un sélecteur lisible et stable
(data-test, id, name...), son équivalent XPath et le chemin des cadres et des
composants qui le contiennent. Ils ne servent pas à l'agent, mais aux tests
générés à la fin (pytest et Robot Framework), qui doivent pouvoir rejouer le
parcours sans IA.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect

from .actions import STATE_LABELS, Action, key_label

REF_ATTR = "data-agent-ref"  # attribut temporaire posé sur chaque élément observé
DATA_DIR = Path(__file__).resolve().parent.parent / "donnees_de_test"  # fichiers que l'agent peut joindre (upload)
POPUP_WAIT_MS = 300  # après un clic, délai laissé à un nouvel onglet pour s'ouvrir
CHECK_TIMEOUT_MS = 3000  # délai laissé à la page pour atteindre l'état attendu par une vérification
MAX_FRAMES = 6  # cadres observés au plus : au-delà, ce sont surtout des publicités et des traceurs
MAX_FRAME_DEPTH = 3
# Cadres de publicité ou de mesure d'audience : ignorés, ils noieraient les vrais éléments de la page
_AD_FRAMES = re.compile(r"doubleclick|googlesyndication|googleadservices|adservice|amazon-adsystem|criteo|taboola|"
                        r"outbrain|adnxs|facebook\.com/plugins", re.IGNORECASE)
_AD_FRAME_NAMES = re.compile(r"^(aswift_|google_ads_iframe|google_esf|__\w+Locator$)")  # cadres de Google AdSense, du consentement

# Composants web fermés (Shadow DOM « closed ») : ce script, exécuté avant ceux de la page, les crée ouverts.
# Le composant fonctionne de la même façon, mais l'agent et Playwright peuvent y entrer. Le nombre de composants
# ouverts ainsi est noté, pour le rapport et pour les tests générés, qui font de même.
OPEN_SHADOW_JS = (
    "(() => { const original = Element.prototype.attachShadow; if (original.agentOpen) return; "
    "const ouvrir = function (options) { if (options && options.mode === 'closed') { "
    "window.agentClosedShadows = (window.agentClosedShadows || 0) + 1; "
    "return original.call(this, Object.assign({}, options, {mode: 'open'})); } "
    "return original.call(this, options); }; ouvrir.agentOpen = true; Element.prototype.attachShadow = ouvrir; })();"
)

# Script exécuté dans la page (ou dans un cadre) pour lister les éléments interactifs.
_OBSERVE_JS = r"""
({refAttr, max, start}) => {
  // Une liste ou une case à cocher transparente, posée sur un habillage personnalisé,
  // reste utilisable : seuls les autres éléments transparents sont ignorés.
  const control = (el) => el.matches('select, input[type=checkbox], input[type=radio], input[type=file]');
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const s = window.getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && (s.opacity !== '0' || control(el));
  };
  const q = (v) => String(v).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  // Champs, natifs ou « maison » : ce qu'un libellé peut désigner
  const FIELD_ROLES = ['combobox', 'listbox', 'textbox', 'searchbox', 'spinbutton'];
  const FIELDS = 'input:not([type=hidden]), select, textarea, [contenteditable=""], [contenteditable=true], ' +
    '[role=combobox], [role=listbox], [role=textbox], [role=searchbox], [role=spinbutton], [tabindex]:not([tabindex="-1"])';
  // Libellé posé à côté d'un champ, sans lien for/id (fréquent dans les applications, comme OrangeHRM) :
  // le dernier <label> qui le précède dans son groupe, si aucun autre champ ne s'intercale.
  const nearLabel = (el) => {
    let node = el.parentElement;
    for (let depth = 0; node && depth < 5; depth++, node = node.parentElement) {
      const labels = Array.from(node.querySelectorAll('label'))
        .filter((l) => (!l.control || l.control === el) && (l.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING));
      if (!labels.length) continue;
      const label = labels[labels.length - 1];
      const next = Array.from(node.querySelectorAll(FIELDS))
        .find((f) => label.compareDocumentPosition(f) & Node.DOCUMENT_POSITION_FOLLOWING);
      return next && (next === el || next.contains(el) || el.contains(next)) ? label : null;
    }
    return null;
  };
  // Texte des éléments cités par aria-labelledby
  const labelledBy = (el) => {
    const root = el.getRootNode();
    const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    return clean(ids.map((id) => root.getElementById(id)).filter(Boolean).map((n) => n.innerText || n.textContent).join(' '))
      .slice(0, 80) || null;
  };
  // Nom d'un champ : son <label>, aria-labelledby, ou le libellé posé à côté. Sans le contenu des champs englobés.
  const labelOf = (el, field) => {
    const label = (el.labels && el.labels[0]) || null;
    const textOf = (node) => {
      const copy = node.cloneNode(true);
      copy.querySelectorAll('input, select, textarea, button').forEach((c) => c.remove());
      return clean(copy.textContent).replace(/\s*:$/, '').slice(0, 80) || null;
    };
    const near = field && !label ? nearLabel(el) : null;
    return (label && textOf(label)) || labelledBy(el) || (near && textOf(near)) || null;
  };
  // Un id fabriqué à chaque chargement (« ember1234 », « a3f9c2e1b7 ») casserait les tests rejoués
  const stableId = (id) => !!id && /^[A-Za-z][\w-]*$/.test(id) && !/\d{4,}/.test(id) && !/[0-9a-f]{8,}/i.test(id);
  const testAttrOf = (el) => ['data-test', 'data-testid', 'data-test-id'].find((a) => el.hasAttribute(a)) || null;
  // Classe CSS d'un élément générique (div, span...) qui le désigne de façon stable : ni chiffres (classes
  // fabriquées, « css-1x2y3z »), ni état (« active », « is-open », « oxd-select-text--active »).
  const GENERIC = ['div', 'span', 'li', 'td', 'p', 'img', 'svg', 'i', 'label', 'ul', 'tr', 'section', 'article'];
  const STATE = /^(active|selected|open|opened|closed|focus|focused|hover|disabled|checked|current|show|shown|hidden|visible|expanded|collapsed)$/i;
  const classOf = (el) => Array.from(el.classList || []).find((c) => /^[A-Za-z][A-Za-z_-]{2,}$/.test(c) && !c.includes('--')
    && !STATE.test(c) && !/^(is|has)-/.test(c)) || null;
  // Texte affiché d'un Shadow DOM : innerText de ses enfants visibles (document.body.innerText ne le contient pas)
  const shownText = (root) => clean(Array.from(root.children)
    .filter((c) => !['STYLE', 'SCRIPT', 'TEMPLATE', 'LINK'].includes(c.tagName) && getComputedStyle(c).display !== 'none')
    .map((c) => c.innerText || '').join(' '));

  // Chaîne XPath 1.0 : pas d'échappement possible, on choisit les guillemets
  const xlit = (v) => {
    const s = String(v);
    if (!s.includes('"')) return '"' + s + '"';
    if (!s.includes("'")) return "'" + s + "'";
    return 'concat(' + s.split('"').map((p) => '"' + p + '"').join(", '\"', ") + ')';
  };
  const UPPER = 'ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸ';
  const LOWER = 'abcdefghijklmnopqrstuvwxyzàâäçéèêëîïôöùûüÿ';
  // Renvoie l'expression XPath qui désigne exactement cet élément, avec un index
  // [n] si plusieurs éléments correspondent, ou null si elle ne le trouve pas.
  const xpathFor = (el, xp) => {
    const found = document.evaluate(xp, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
    for (let i = 0; i < found.snapshotLength; i++) {
      if (found.snapshotItem(i) === el) return found.snapshotLength > 1 ? `(${xp})[${i + 1}]` : xp;
    }
    return null;
  };

  // Le document, puis chaque Shadow DOM ouvert, avec le chemin des hôtes qui y mènent
  const roots = [];
  const hostOf = (host, root) => {
    const attr = testAttrOf(host);
    let css = host.tagName.toLowerCase();
    if (attr) css = `[${attr}="${q(host.getAttribute(attr))}"]`;
    else if (stableId(host.id)) css = `#${host.id}`;
    const all = Array.from(root.querySelectorAll(css));
    return {css, nth: all.length > 1 ? all.indexOf(host) : null};
  };
  const walk = (root, hosts) => {
    roots.push({root, hosts});
    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot, hosts.concat([hostOf(el, root)])); });
  };
  walk(document, []);
  roots.forEach(({root}) => root.querySelectorAll('[' + refAttr + ']').forEach((e) => e.removeAttribute(refAttr)));

  // Chemin CSS depuis la racine d'un composant : querySelector ne connaît pas :has-text
  const pathIn = (el, root) => {
    const parts = [];
    for (let node = el; node && node !== root && node.nodeType === 1; node = node.parentNode) {
      const tag = node.tagName.toLowerCase();
      const same = Array.from(node.parentNode.children).filter((c) => c.tagName === node.tagName);
      parts.unshift(same.length > 1 ? `${tag}:nth-of-type(${same.indexOf(node) + 1})` : tag);
    }
    return parts.join(' > ');
  };

  const elements = [];
  const add = (el, root, hosts) => {
    if (elements.length >= max || el.hasAttribute(refAttr) || !visible(el)) return;
    const docRoot = root === document;
    const tag = el.tagName.toLowerCase();
    const type = el.getAttribute('type');
    const kind = (type || '').toLowerCase();
    const role = el.getAttribute('role');
    const editable = el.isContentEditable && el.matches('[contenteditable=""], [contenteditable=true]');
    // Champ à remplir ou à choisir : saisie, liste native ou « maison » (une div focalisable), zone de texte riche
    const field = (tag === 'input' && !['submit', 'button', 'reset', 'image'].includes(kind)) || tag === 'select'
      || tag === 'textarea' || editable || FIELD_ROLES.includes(role)
      || (GENERIC.includes(tag) && !role && el.hasAttribute('tabindex') && el.getAttribute('tabindex') !== '-1');
    const labelText = labelOf(el, field);
    let text = clean(el.innerText);
    if (!text && tag === 'input' && ['submit', 'button', 'reset'].includes(kind)) text = clean(el.value);
    // Contenu actuel d'un champ, montré au modèle (jamais celui d'un mot de passe)
    let filled = '';
    if (tag === 'textarea') filled = el.value;
    else if (tag === 'select') filled = el.selectedIndex >= 0 ? el.options[el.selectedIndex].text : '';
    else if (tag === 'input' && !['password', 'checkbox', 'radio', 'file', 'submit', 'button', 'reset', 'image', 'hidden',
                                  'range', 'color'].includes(kind)) filled = el.value;

    const testAttr = testAttrOf(el);
    const attrs = {
      test_attr: testAttr,
      test_id: testAttr ? el.getAttribute(testAttr) : null,
      id: el.id || null,
      name: el.getAttribute('name'),
      placeholder: el.getAttribute('placeholder'),
      aria: el.getAttribute('aria-label'),
      label: labelText,
      title: el.getAttribute('title'),
      role,
      value: clean(filled).slice(0, 80) || null,
      editable: editable || null,
      field: field || null,
      draggable: el.getAttribute('draggable') === 'true' || null,
      autocomplete: el.getAttribute('autocomplete'),
    };

    // Sélecteur lisible, du plus robuste au moins robuste
    let css = null;
    if (attrs.test_id) css = `[${testAttr}="${q(attrs.test_id)}"]`;
    else if (stableId(attrs.id)) css = `#${attrs.id}`;
    else if (attrs.name) css = `${tag}[name="${q(attrs.name)}"]`;
    else if (attrs.aria) css = `${tag}[aria-label="${q(attrs.aria)}"]`;
    else if (attrs.placeholder) css = `${tag}[placeholder="${q(attrs.placeholder)}"]`;
    else if (tag === 'input' && el.value) css = `input[value="${q(el.value)}"]`;
    // Élément générique sans attribut : sa classe, si elle est la seule de ce nom (div.liste-choix)
    const cls = GENERIC.includes(tag) ? classOf(el) : null;
    const clsXpath = cls ? `//${tag}[contains(concat(" ", normalize-space(@class), " "), " ${cls} ")]` : null;
    const byClass = !css && !!cls && root.querySelectorAll(`${tag}.${cls}`).length === 1;
    if (byClass) css = `${tag}.${cls}`;

    // Champ sans attribut utile, ou liste « maison » parmi d'autres : le libellé posé juste avant le désigne,
    // en XPath (//label[normalize-space(.)="User Role"]/following::div[@tabindex][1]), que Playwright comprend aussi.
    let byLabel = null;
    if (!css && docRoot && field && labelText) {
      const target = tag + (GENERIC.includes(tag) ? '[@tabindex]' : role ? `[@role=${xlit(role)}]` : '');
      const label = xlit(labelText);
      byLabel = xpathFor(el, `//label[normalize-space(.)=${label}]/following::${target}[1]`)
        || xpathFor(el, `//label[starts-with(normalize-space(.), ${label})]/following::${target}[1]`)
        || xpathFor(el, `//label[starts-with(normalize-space(.), ${label})]//${target}`);
    }

    let selector;
    let nth = null;
    let lastClass;  // undefined tant que le dernier recours n'a pas servi
    if (css) {
      const matches = Array.from(root.querySelectorAll(css));
      if (matches.length > 1) nth = matches.indexOf(el);
      selector = css;
    } else if (byLabel) {
      selector = `xpath=${byLabel}`;
    } else if (docRoot && text) {
      // Extension Playwright :has-text() : contient le texte, sans tenir compte de la casse.
      // Le rôle ARIA ou la classe, s'il y en a, rendent le sélecteur plus précis : div[role="option"]:has-text("ESS").
      const needle = text.slice(0, 60);
      const base = attrs.role ? `${tag}[role="${q(attrs.role)}"]` : cls ? `${tag}.${cls}` : tag;
      selector = `${base}:has-text("${q(needle)}")`;
      const same = Array.from(root.querySelectorAll(base))
        .filter((e) => clean(e.innerText).toLowerCase().includes(needle.toLowerCase()));
      if (same.length > 1) nth = same.indexOf(el);
    } else if (!docRoot) {
      selector = pathIn(el, root);
      const matches = Array.from(root.querySelectorAll(selector));
      if (matches.length > 1) nth = matches.indexOf(el);
    } else {
      // Un bouton ou une icône sans texte ni attribut ne dirait rien au modèle : on l'ignore. Un champ, lui,
      // reste utilisable en dernier recours, désigné par sa classe ou sa balise et son rang (fragile pour les tests rejoués).
      if (!field) return;
      lastClass = cls || classOf(el);
      selector = lastClass ? `${tag}.${lastClass}` : tag;
      const matches = Array.from(root.querySelectorAll(selector));
      if (matches.length > 1) nth = matches.indexOf(el);
    }

    // Équivalent XPath, pour le test Robot Framework / Selenium généré à la fin
    // (Selenium ne connaît pas :has-text ni .nth de Playwright). XPath n'entre pas dans un Shadow DOM.
    let xpath = null;
    if (!docRoot) xpath = null;
    else if (byLabel) xpath = byLabel;
    else if (attrs.test_id) xpath = xpathFor(el, `//*[@${testAttr}=${xlit(attrs.test_id)}]`);
    else if (stableId(attrs.id)) xpath = xpathFor(el, `//*[@id=${xlit(attrs.id)}]`);
    else if (attrs.name) xpath = xpathFor(el, `//${tag}[@name=${xlit(attrs.name)}]`);
    else if (attrs.aria) xpath = xpathFor(el, `//${tag}[@aria-label=${xlit(attrs.aria)}]`);
    else if (attrs.placeholder) xpath = xpathFor(el, `//${tag}[@placeholder=${xlit(attrs.placeholder)}]`);
    else if (tag === 'input' && el.value) xpath = xpathFor(el, `//input[@value=${xlit(el.value)}]`);
    else if (byClass) xpath = xpathFor(el, clsXpath);
    else if (text) {
      const needle = text.slice(0, 60);
      const base = attrs.role ? `//${tag}[@role=${xlit(attrs.role)}]` : clsXpath || `//${tag}`;
      xpath = xpathFor(el, `${base}[contains(normalize-space(.), ${xlit(needle)})]`)
        || xpathFor(el, `${base}[contains(translate(normalize-space(.), "${UPPER}", "${LOWER}"), ${xlit(needle.toLowerCase())})]`);
    } else if (lastClass !== undefined) {
      xpath = xpathFor(el, lastClass ? `//${tag}[contains(concat(" ", normalize-space(@class), " "), " ${lastClass} ")]` : `//${tag}`);
    }

    const ref = start + elements.length + 1;
    el.setAttribute(refAttr, String(ref));
    elements.push({ref, tag, type, text: text.slice(0, 80), attrs, selector, nth, xpath, shadow: hosts.length ? hosts : null});
  };

  const CANDIDATES = 'a[href], button, input:not([type=hidden]), select, textarea, ' +
    '[role=button], [role=link], [role=checkbox], [role=radio], [role=switch], [role=tab], [role=option], ' +
    '[role=menuitem], [role=combobox], [aria-haspopup]:not([aria-haspopup=false]), [onclick], ' +
    '[contenteditable=""], [contenteditable=true], [tabindex]:not([tabindex="-1"]), ' +
    // Glisser-déposer : l'élément à glisser, et les zones où le déposer
    '[draggable=true], [aria-dropeffect], [class*="dropzone" i], [class*="droppable" i], [id*="dropzone" i], [id*="droppable" i]';
  for (const {root, hosts} of roots) root.querySelectorAll(CANDIDATES).forEach((el) => add(el, root, hosts));

  // Éléments cliquables sans rôle ni attribut (options d'une liste « maison ») : le site leur donne
  // le curseur en forme de main. On garde le plus extérieur, et rien de ce qui est déjà dans la liste.
  let scanned = 0;
  for (const {root, hosts} of roots) {
    for (const el of root.querySelectorAll('div, span, li, td, p, img, svg')) {
      if (elements.length >= max || ++scanned > 4000) break;
      if (el.closest('[' + refAttr + ']') || el.querySelector('[' + refAttr + ']')) continue;
      if (getComputedStyle(el).cursor !== 'pointer') continue;
      const parent = el.parentElement;
      if (parent && getComputedStyle(parent).cursor === 'pointer') continue;
      if (!clean(el.innerText) && !el.getAttribute('aria-label') && !el.getAttribute('title')) continue;
      add(el, root, hosts);
    }
  }

  const shadowTexts = roots.slice(1).map(({root}) => shownText(root)).filter(Boolean);
  return {elements, text: clean(document.body ? document.body.innerText : ''), shadow_text: shadowTexts.join(' '),
          closed: window.agentClosedShadows || 0};
}
"""

# Texte visible d'un document : celui de la page (innerText) et celui de ses Shadow DOM, à part.
_TEXT_JS = r"""
() => {
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const shadow = [];
  const walk = (root) => root.querySelectorAll('*').forEach((el) => {
    if (!el.shadowRoot) return;
    shadow.push(clean(Array.from(el.shadowRoot.children)
      .filter((c) => !['STYLE', 'SCRIPT', 'TEMPLATE', 'LINK'].includes(c.tagName) && getComputedStyle(c).display !== 'none')
      .map((c) => c.innerText || '').join(' ')));
    walk(el.shadowRoot);
  });
  walk(document);
  return {light: clean(document.body ? document.body.innerText : ''), shadow: shadow.filter(Boolean).join(' ')};
}
"""

# Comment désigner un cadre (iframe) dans le document qui le contient, et s'il est visible.
_FRAME_JS = r"""
(el) => {
  const q = (v) => String(v).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  const stableId = (id) => !!id && /^[A-Za-z][\w-]*$/.test(id) && !/\d{4,}/.test(id) && !/[0-9a-f]{8,}/i.test(id);
  const tag = el.tagName.toLowerCase();
  let css = tag;
  if (stableId(el.id)) css = `#${el.id}`;
  else if (el.getAttribute('name')) css = `${tag}[name="${q(el.getAttribute('name'))}"]`;
  else if (el.getAttribute('title')) css = `${tag}[title="${q(el.getAttribute('title'))}"]`;
  else if (el.getAttribute('src')) css = `${tag}[src="${q(el.getAttribute('src'))}"]`;
  const all = Array.from(el.getRootNode().querySelectorAll(css));
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return {
    css, nth: all.length > 1 ? all.indexOf(el) : null,
    label: el.getAttribute('title') || el.getAttribute('name') || el.id || css,
    visible: r.width >= 40 && r.height >= 20 && s.visibility !== 'hidden' && s.display !== 'none',
  };
}
"""

_ATTR_LABELS = (("test_id", "data-test"), ("id", "id"), ("name", "name"), ("placeholder", "placeholder"), ("aria", "aria-label"),
                ("label", "label"), ("title", "title"))

# Valeurs d'autocomplete d'un champ de nom ou d'adresse : signalées au modèle, et leur contenu est masqué
PERSONAL_AUTOCOMPLETE = ("name", "given-name", "family-name", "additional-name", "honorific-prefix", "honorific-suffix",
                         "nickname", "street-address", "address-line1", "address-line2", "address-line3", "address-level1",
                         "address-level2", "address-level3", "address-level4", "postal-code")

# Balises sans rôle propre : une div cliquable n'est ni un bouton ni un lien, c'est « l'élément »
_GENERIC_TAGS = ("div", "span", "li", "td", "p", "img", "svg", "i", "label", "ul", "tr", "section", "article")

# Rôle ARIA -> façon de nommer l'élément dans une phrase (et s'il est féminin)
_ROLE_NOUNS = {"option": ("l'option", True), "radio": ("l'option", True), "checkbox": ("la case", True),
               "combobox": ("la liste", True), "listbox": ("la liste", True), "tab": ("l'onglet", False),
               "switch": ("l'interrupteur", False), "link": ("le lien", False)}


@dataclass
class Element:
    """Un élément interactif de la page, numéroté pour le modèle."""

    ref: int
    tag: str
    type: str | None
    text: str
    attrs: dict
    selector: str
    nth: int | None = None
    xpath: str | None = None  # même élément en XPath, pour Selenium et Robot Framework
    shadow: list | None = None  # hôtes des Shadow DOM qui le contiennent : [{"css", "nth"}], du document vers l'élément
    frame: list | None = None  # cadres (iframes) qui le contiennent : [{"css", "nth", "label"}], de la page vers l'élément

    def editable(self) -> bool:
        """Zone de texte riche (contenteditable), comme le corps d'un éditeur TinyMCE."""
        return bool((self.attrs or {}).get("editable"))

    def draggable(self) -> bool:
        """Élément à glisser-déposer au sens de HTML5 (attribut draggable)."""
        return bool((self.attrs or {}).get("draggable"))

    def is_field(self) -> bool:
        """Champ à remplir ou à choisir : saisie, liste (native ou « maison »), zone de texte riche."""
        if self.tag in ("input", "textarea", "select"):
            return (self.type or "").lower() not in ("submit", "button", "reset", "image")
        return self.editable() or bool((self.attrs or {}).get("field"))

    def reads_text(self) -> bool:
        """Son contenu se lit dans son texte affiché, pas dans une valeur : zone de texte riche, liste « maison »."""
        return self.editable() or self.tag not in ("input", "textarea", "select")

    def label(self) -> str:
        """Nom court de l'élément, tel qu'un testeur le désignerait : « Username », « Login »."""
        attrs = self.attrs or {}
        if self.is_field():
            name = (attrs.get("label") or attrs.get("aria") or attrs.get("placeholder") or attrs.get("title")
                    or attrs.get("name") or attrs.get("id") or attrs.get("test_id")
                    or (self.text if self.tag not in ("select", "textarea") else None))
        else:  # un bouton dont le texte n'est qu'une icône (⚙, ×) se nomme par son aria-label
            text = self.text if any(ch.isalnum() for ch in self.text) else ""
            name = text or attrs.get("aria") or attrs.get("title") or self.text or attrs.get("test_id") or attrs.get("id")
        return name or self.tag

    def noun(self) -> tuple[str, bool]:
        """« le champ », « la liste »..., et si le nom est féminin (« la case est cochée »)."""
        kind = (self.type or "").lower()
        if self.editable():
            return "la zone de texte", True
        if self.tag == "input" and kind == "checkbox":
            return "la case", True
        if self.tag == "input" and kind == "radio":
            return "l'option", True
        if self.tag == "select":
            return "la liste", True
        if self.tag in ("input", "textarea") and kind not in ("submit", "button", "reset"):
            return "le champ", False
        if (self.attrs or {}).get("role") in _ROLE_NOUNS:
            return _ROLE_NOUNS[self.attrs["role"]]
        if self.tag in _GENERIC_TAGS and not (self.attrs or {}).get("role"):  # une div focalisable, ou cliquable, sans rôle
            return ("le champ", False) if self.is_field() else ("l'élément", False)
        return ("le lien", False) if self.tag == "a" else ("le bouton", False)

    def frame_label(self) -> str | None:
        return self.frame[-1].get("label") or self.frame[-1]["css"] if self.frame else None

    def describe(self) -> str:
        """Description d'une ligne, envoyée au modèle."""
        parts = [f"[{self.ref}]", self.tag]
        if self.tag == "input" and self.type:
            parts.append(f"type={self.type}")
        attrs = self.attrs or {}
        if attrs.get("editable"):
            parts.append("éditable")
        if attrs.get("role"):
            parts.append(f'role="{attrs["role"]}"')
        for key, label in _ATTR_LABELS:
            if attrs.get(key):
                parts.append(f'{label}="{attrs[key]}"')
        if (attrs.get("autocomplete") or "").lower() in PERSONAL_AUTOCOMPLETE:  # un nom, une adresse : voir masquage.py
            parts.append(f'autocomplete="{attrs["autocomplete"]}"')
        if attrs.get("value") is not None:
            parts.append(f'valeur="{attrs["value"]}"')
        if self.text:
            parts.append(f'texte="{self.text}"')
        if self.frame:
            parts.append(f"(dans le cadre « {self.frame_label()} »)")
        if self.shadow:
            parts.append(f"(dans le composant {self.shadow[-1]['css']})")
        return " ".join(parts)


@dataclass
class Observation:
    """Ce que l'agent voit de la page à un instant donné."""

    url: str
    title: str
    text: str
    elements: list[Element] = field(default_factory=list)
    screenshot: bytes | None = None  # capture JPEG envoyée au modèle quand la vision est activée
    frame_texts: list = field(default_factory=list)  # (nom du cadre, texte visible) de chaque cadre observé
    frames: dict = field(default_factory=dict, repr=False, compare=False)  # ref -> cadre Playwright qui le contient
    tabs: list = field(default_factory=list)  # titres des onglets ouverts
    tab: int = 1  # onglet observé, à partir de 1
    closed_shadows: int = 0  # composants web fermés, ouverts pour le test

    def find(self, ref: int) -> Element | None:
        return next((e for e in self.elements if e.ref == ref), None)

    def to_prompt(self, max_text: int = 1500) -> str:
        text = self.text if len(self.text) <= max_text else self.text[:max_text] + " [...]"
        lines = [f"URL : {self.url}", f"Titre : {self.title}"]
        if len(self.tabs) > 1:
            lines.append("Onglets ouverts : " + " ; ".join(f"{index}. {title}" + (" (actif)" if index == self.tab else "")
                                                          for index, title in enumerate(self.tabs, 1)))
        lines += ["Texte visible (extrait) :", text or "(vide)", ""]
        for label, frame_text in self.frame_texts:
            short = frame_text if len(frame_text) <= 600 else frame_text[:600] + " [...]"
            lines += [f"Texte du cadre « {label} » :", short, ""]
        lines.append("Éléments interactifs :")
        lines.extend(e.describe() for e in self.elements)
        if not self.elements:
            lines.append("(aucun)")
        return "\n".join(lines)


@dataclass
class ActionResult:
    """Résultat de l'exécution d'une action."""

    ok: bool  # l'action s'est exécutée sans erreur technique
    detail: str  # message lisible, repris dans le rapport
    element: Element | None = None  # élément ciblé (click, fill, select)
    check: bool | None = None  # résultat d'une vérification, None si ce n'en est pas une
    value: str | None = None  # valeur réelle constatée : option choisie, texte avec sa casse exacte
    by_ai: bool = False  # vérification visuelle : c'est l'avis du modèle sur la capture, pas une mesure
    frame: list | None = None  # cadre où le texte attendu a été trouvé (vérification ou attente)
    in_shadow: bool = False  # texte trouvé dans un Shadow DOM, que document.body.innerText ne contient pas
    dialog: dict | None = None  # fenêtre JavaScript ouverte par l'action : type, message, réponse donnée
    target: Element | None = None  # glisser-déposer : l'élément où l'on dépose
    page: object | None = field(default=None, repr=False, compare=False)  # onglet où continuer, s'il a changé
    opened_tab: bool = False  # l'action a ouvert un nouvel onglet


class DialogWatcher:
    """Fenêtres JavaScript (alerte, confirmation, saisie) : la réponse choisie par l'agent, OK par défaut.

    Sans gestionnaire, Playwright fermerait chaque fenêtre en choisissant Annuler, sans que
    l'agent la voie : un formulaire confirmé par une fenêtre ne serait jamais envoyé.
    """

    def __init__(self) -> None:
        self.accept, self.text, self.seen = True, None, []

    def handle(self, dialog) -> None:
        accepted, text = self.accept, self.text if dialog.type == "prompt" else None
        try:
            if not accepted:
                dialog.dismiss()
            elif text is not None:
                dialog.accept(text)
            else:
                dialog.accept()
        except PlaywrightError:
            pass  # page fermée entre-temps
        self.seen.append({"type": dialog.type, "message": dialog.message, "accepted": accepted, "text": text})
        self.accept, self.text = True, None  # la consigne ne vaut que pour une fenêtre


class TabWatcher:
    """Onglets ouverts pendant le test (lien target=_blank, window.open), dans l'ordre."""

    def __init__(self) -> None:
        self.opened: list = []

    def handle(self, page) -> None:
        self.opened.append(page)


def tabs(context) -> TabWatcher:
    """Le suivi des nouveaux onglets du navigateur, branché à la première demande."""
    watcher = getattr(context, "_agent_tabs", None)
    if watcher is None:
        watcher = TabWatcher()
        context.on("page", watcher.handle)
        context._agent_tabs = watcher
    return watcher


def open_closed_shadows(context) -> None:
    """Les composants web fermés seront créés ouverts, dans chaque page et chaque onglet (voir OPEN_SHADOW_JS)."""
    if not getattr(context, "_agent_open_shadows", False):
        context.add_init_script(OPEN_SHADOW_JS)
        context._agent_open_shadows = True


def _new_tab(page: Page, watcher: TabWatcher, before: int):
    """Le nouvel onglet ouvert par l'action, s'il y en a un (il s'ouvre quelques dizaines de millisecondes après le clic)."""
    deadline = time.monotonic() + POPUP_WAIT_MS / 1000
    while len(watcher.opened) <= before and time.monotonic() < deadline:
        page.wait_for_timeout(25)
    if len(watcher.opened) <= before:
        return None
    new = watcher.opened[-1]
    try:
        new.wait_for_load_state("load", timeout=10000)
    except PlaywrightError:
        pass
    return new


def dialogs(page: Page) -> DialogWatcher:
    """Le gestionnaire de fenêtres JavaScript de la page, branché à la première demande."""
    watcher = getattr(page, "_agent_dialogs", None)
    if watcher is None:
        watcher = DialogWatcher()
        page.on("dialog", watcher.handle)
        page._agent_dialogs = watcher
    return watcher


def _frame_path(frame) -> list[dict] | None:
    """Chemin d'un cadre depuis la page : comment désigner chaque iframe traversée. None si un cadre est caché."""
    hops, current = [], frame
    try:
        while current.parent_frame is not None:
            handle = current.frame_element()
            try:
                hop = handle.evaluate(_FRAME_JS)
            finally:
                handle.dispose()
            if not hop["visible"]:
                return None
            hops.append({"css": hop["css"], "nth": hop["nth"], "label": hop["label"]})
            current = current.parent_frame
    except PlaywrightError:
        return None  # cadre en cours de chargement ou fermé entre-temps
    return list(reversed(hops)) if len(hops) <= MAX_FRAME_DEPTH else None


def _frames(page: Page) -> list[tuple]:
    """Les cadres visibles de la page, sans les publicités : (cadre Playwright, chemin)."""
    found = []
    for frame in page.frames:
        if frame.parent_frame is None or frame.is_detached() or _AD_FRAMES.search(frame.url or "") \
                or _AD_FRAME_NAMES.search(frame.name or ""):
            continue
        path = _frame_path(frame)
        if path is not None:
            found.append((frame, path))
        if len(found) >= MAX_FRAMES:
            break
    return found


def _joined(data: dict) -> str:
    return " ".join(part for part in (data.get("text"), data.get("shadow_text")) if part)


def observe(page: Page, max_elements: int = 80) -> Observation:
    """Photographie l'état de la page : URL, titre, texte et éléments numérotés, cadres compris."""
    data = page.evaluate(_OBSERVE_JS, {"refAttr": REF_ATTR, "max": max_elements, "start": 0})
    elements = [Element(**item) for item in data["elements"]]
    frames, frame_texts = {}, []
    for frame, path in _frames(page):
        try:
            found = frame.evaluate(_OBSERVE_JS, {"refAttr": REF_ATTR, "max": max(max_elements - len(elements), 0),
                                                 "start": len(elements)})
        except PlaywrightError:
            continue  # cadre en cours de chargement ou fermé entre-temps
        for item in found["elements"]:
            element = Element(**item, frame=path)
            elements.append(element)
            frames[element.ref] = frame
        if _joined(found):
            frame_texts.append((path[-1]["label"], _joined(found)))
    pages = page.context.pages
    titles = []
    for other in pages:
        try:
            titles.append(other.title() or other.url)
        except PlaywrightError:
            titles.append(other.url)
    return Observation(url=page.url, title=page.title(), text=_joined(data), elements=elements,
                       frame_texts=frame_texts, frames=frames, tabs=titles,
                       tab=pages.index(page) + 1 if page in pages else 1, closed_shadows=data.get("closed", 0))


def _short_error(exc: Exception) -> str:
    """Première ligne du message d'erreur Playwright, souvent très long."""
    return str(exc).strip().splitlines()[0][:300] if str(exc).strip() else exc.__class__.__name__


def visible_text(page: Page) -> str:
    """Texte visible de la page, Shadow DOM compris, espaces regroupés, comme le lit un utilisateur."""
    data = page.main_frame.evaluate(_TEXT_JS)
    return " ".join(part for part in (data["light"], data["shadow"]) if part)


def _texts(page: Page) -> list[tuple]:
    """Textes visibles de la page puis de chaque cadre : (chemin du cadre, texte, texte des Shadow DOM)."""
    found = []
    for path, frame in [(None, page.main_frame)] + [(path, frame) for frame, path in _frames(page)]:
        try:
            data = frame.evaluate(_TEXT_JS)
        except PlaywrightError:
            continue
        found.append((path, data["light"], data["shadow"]))
    return found


def _locate_text(page: Page, text: str) -> tuple | None:
    """Où le texte est-il affiché ? (chemin du cadre, dans un Shadow DOM, casse exacte), ou None."""
    needle = " ".join(text.split()).lower()
    for path, light, shadow in _texts(page):
        for part, in_shadow in ((light, False), (shadow, True)):
            position = part.lower().find(needle)
            if position >= 0:
                exact = part[position:position + len(needle)]
                return path, in_shadow, exact if exact.lower() == needle else text
    return None


def _wait_text(page: Page, text: str, timeout_s: float, present: bool = True) -> tuple[bool, tuple | None, float]:
    """Attend que le texte soit affiché (ou ait disparu) : (réussi, où il est, secondes écoulées)."""
    start = time.monotonic()
    while True:
        where = _locate_text(page, text)
        elapsed = time.monotonic() - start
        if (where is not None) == present:
            return True, where, elapsed
        if elapsed > timeout_s:
            return False, where, elapsed
        page.wait_for_timeout(100)


def _poll(page: Page, condition):
    """Réessaie une vérification jusqu'à ce qu'elle réussisse ou que le délai soit écoulé.

    La page peut encore changer juste après une action (tri, compteur mis à jour) :
    comme expect de Playwright, on laisse quelques secondes au résultat attendu.
    """
    deadline = time.monotonic() + CHECK_TIMEOUT_MS / 1000
    while True:
        ok, detail, value = condition()
        if ok or time.monotonic() > deadline:
            return ok, detail, value
        page.wait_for_timeout(100)


def _settle(page: Page) -> None:
    """Laisse le temps à une éventuelle navigation de se terminer."""
    try:
        page.wait_for_load_state("load", timeout=5000)
    except PlaywrightError:
        pass


def _dialog_detail(dialog: dict) -> str:
    kind = {"alert": "Alerte", "confirm": "Fenêtre de confirmation", "prompt": "Fenêtre de saisie",
            "beforeunload": "Fenêtre de départ"}.get(dialog["type"], "Fenêtre")
    if not dialog["accepted"]:
        answer = "refusée (Annuler)"
    elif dialog.get("text") is not None:
        answer = f"acceptée avec la saisie « {dialog['text']} »"
    else:
        answer = "acceptée (OK)"
    return f"{kind} « {dialog['message']} » {answer}."


def execute(page: Page, action: Action, observation: Observation | None, timeout_ms: int = 5000) -> ActionResult:
    """Exécute une action sur la page et renvoie son résultat, sans jamais lever d'exception.

    Une fenêtre JavaScript ouverte par l'action est notée dans le résultat : le modèle lit son
    message dans l'historique, et les tests générés savent y répondre de la même façon.
    """
    watcher, tab_watcher = dialogs(page), tabs(page.context)
    before, tabs_before = len(watcher.seen), len(tab_watcher.opened)
    result = _run(page, action, observation, timeout_ms, watcher)
    opened = watcher.seen[before:]
    if opened:
        result.dialog = opened[-1]
        result.detail = f"{result.detail} {_dialog_detail(opened[-1])}"
    if result.ok and action.name in ("click", "press"):  # un lien target=_blank, un window.open : l'agent suit l'onglet
        new = _new_tab(page, tab_watcher, tabs_before)
        if new is not None:
            result.page, result.opened_tab = new, True
            result.detail = f"{result.detail} Nouvel onglet ouvert : {new.url}"
    return result


def _run(page: Page, action: Action, observation: Observation | None, timeout_ms: int, watcher: DialogWatcher) -> ActionResult:
    args = action.args
    try:
        if action.name == "goto":
            page.goto(args["url"], wait_until="load", timeout=15000)
            return ActionResult(True, f"Page ouverte : {page.url}")

        if action.name in ("click", "fill", "select", "check_value", "check_state", "hover", "upload", "drag") \
                or (action.name in ("press", "scroll") and "ref" in args):
            element = observation.find(args["ref"]) if observation else None
            if element is None:
                return ActionResult(False, f"Aucun élément [{args['ref']}] sur la page actuelle")
            # Un élément d'un cadre se cherche dans ce cadre ; Playwright entre seul dans les Shadow DOM
            scope = observation.frames.get(element.ref) or page
            locator = scope.locator(f'[{REF_ATTR}="{element.ref}"]')
            if action.name == "check_value":
                return _check_value(page, locator, element, args["value"], timeout_ms, args.get("regex", False))
            if action.name == "check_state":
                return _check_state(page, locator, element, args["state"], timeout_ms)
            if action.name == "click":
                if args.get("double"):
                    locator.dblclick(timeout=timeout_ms)
                    verb = "Double-clic sur"
                elif args.get("button") == "right":
                    locator.click(button="right", timeout=timeout_ms)
                    verb = "Clic droit sur"
                else:
                    locator.click(timeout=timeout_ms)
                    verb = "Clic sur"
                _settle(page)
                return ActionResult(True, f"{verb} {element.describe()}", element)
            if action.name == "hover":
                locator.hover(timeout=timeout_ms)
                page.wait_for_timeout(200)  # le temps qu'un menu s'ouvre
                return ActionResult(True, f"Survol de {element.describe()}", element)
            if action.name == "press":
                locator.press(args["key"], timeout=timeout_ms)
                _settle(page)
                return ActionResult(True, f"Touche {key_label(args['key'])} dans {element.describe()}", element)
            if action.name == "scroll":
                locator.scroll_into_view_if_needed(timeout=timeout_ms)
                page.wait_for_timeout(300)  # une liste qui se charge en arrivant à l'écran
                return ActionResult(True, f"Page défilée jusqu'à {element.describe()}", element)
            if action.name == "upload":
                path = DATA_DIR / args["file"]
                if not path.is_file():
                    return ActionResult(False, f"Fichier « {args['file']} » introuvable dans le dossier donnees_de_test", element)
                locator.set_input_files(str(path), timeout=timeout_ms)
                return ActionResult(True, f"Fichier « {args['file']} » joint : {element.describe()}", element)
            if action.name == "drag":
                target = observation.find(args["to"])
                if target is None:
                    return ActionResult(False, f"Aucun élément [{args['to']}] sur la page actuelle", element)
                if observation.frames.get(target.ref) is not observation.frames.get(element.ref):
                    return ActionResult(False, "Glisser-déposer impossible entre deux cadres différents", element)
                locator.drag_to(scope.locator(f'[{REF_ATTR}="{target.ref}"]'), timeout=timeout_ms)
                _settle(page)
                return ActionResult(True, f"{element.describe()} glissé sur {target.describe()}", element, target=target)
            if action.name == "fill":
                locator.fill(args["text"], timeout=timeout_ms)
                return ActionResult(True, f"Champ rempli : {element.describe()}", element)
            selected = locator.select_option(args["value"], timeout=timeout_ms)
            _settle(page)
            # Playwright accepte le libellé ou la valeur : on garde la valeur réelle de l'option
            return ActionResult(True, f"Option « {args['value']} » choisie", element, value=selected[0] if selected else None)

        if action.name == "press":  # sans élément : la touche va à la page (Échap ferme une fenêtre, par exemple)
            page.keyboard.press(args["key"])
            _settle(page)
            return ActionResult(True, f"Touche {key_label(args['key'])}")

        if action.name == "scroll":
            if args["to"] == "bottom":
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(500)  # une liste qui se charge en bas de page
                return ActionResult(True, "Page défilée jusqu'en bas")
            page.evaluate("window.scrollTo(0, 0)")
            return ActionResult(True, "Page remontée en haut")

        if action.name == "switch_tab":
            pages = page.context.pages
            if not 1 <= args["index"] <= len(pages):
                return ActionResult(False, f"Pas d'onglet n°{args['index']} ({len(pages)} onglet{'s' if len(pages) > 1 else ''} ouvert{'s' if len(pages) > 1 else ''})")
            target = pages[args["index"] - 1]
            target.bring_to_front()
            return ActionResult(True, f"Onglet n°{args['index']} : {target.title() or target.url}", page=target)

        if action.name == "dialog":  # consigne pour la prochaine fenêtre JavaScript
            watcher.accept, watcher.text = args["accept"], args.get("text") if args["accept"] else None
            answer = "OK" if args["accept"] else "Annuler"
            typed = f", avec la saisie « {watcher.text} »" if watcher.text is not None else ""
            return ActionResult(True, f"Prochaine fenêtre JavaScript : réponse {answer}{typed}")

        if action.name == "wait":
            seconds = args["seconds"]
            if "text" not in args:
                page.wait_for_timeout(seconds * 1000)
                return ActionResult(True, f"Attente de {seconds} s")
            found, where, elapsed = _wait_text(page, args["text"], seconds)
            if not found:
                return ActionResult(False, f"« {args['text']} » ne s'est pas affiché en {seconds} s")
            path, in_shadow, exact = where
            return ActionResult(True, f"« {args['text']} » affiché après {elapsed:.0f} s", value=exact, frame=path,
                                in_shadow=in_shadow)

        if action.name == "check_text":
            text, absent = args["text"], args.get("absent", False)
            if absent:  # vérification négative : le texte ne doit apparaître ni dans la page, ni dans ses cadres
                gone, _, _ = _wait_text(page, text, CHECK_TIMEOUT_MS / 1000, present=False)
                if gone:
                    return ActionResult(True, f"Texte « {text} » absent, comme attendu", check=True)
                return ActionResult(True, f"Texte « {text} » présent alors qu'il devrait être absent", check=False)
            found, where, _ = _wait_text(page, text, CHECK_TIMEOUT_MS / 1000)
            if not found:
                return ActionResult(True, f"Texte « {text} » absent de la page", check=False)
            # La vérification ignore la casse ; Selenium non. On note la casse exacte de la page.
            path, in_shadow, exact = where
            place = f" dans le cadre « {path[-1]['label']} »" if path else ""
            return ActionResult(True, f"Texte « {text} » présent{place}", check=True, value=exact, frame=path,
                                in_shadow=in_shadow)

        if action.name == "check_visual":  # avis du modèle sur la capture d'écran qu'il a reçue
            if observation is None or observation.screenshot is None:
                return ActionResult(False, "La vérification visuelle demande la vision (option --vision, ou case Vision dans Alfred)")
            verdict = "conforme" if args["ok"] else "non conforme"
            return ActionResult(True, f"Avis de l'IA sur la capture d'écran ({verdict}) : {args['seen']}",
                                check=args["ok"], by_ai=True)

        if action.name == "check_order":
            ok, detail, _ = _poll(page, lambda: _order(visible_text(page), args["texts"]))
            return ActionResult(True, detail, check=ok)

        if action.name == "check_count":
            needle, expected = " ".join(args["text"].split()).lower(), args["count"]

            def count():
                found = visible_text(page).lower().count(needle)
                detail = f"« {args['text']} » apparaît {found} fois" + ("" if found == expected else f" au lieu de {expected}")
                return found == expected, detail, str(found)

            ok, detail, found = _poll(page, count)
            return ActionResult(True, detail, check=ok, value=found)

        if action.name == "check_url":
            part, absent = args["contains"], args.get("absent", False)
            pattern = re.compile(re.escape(part))
            try:
                if absent:  # vérification négative : l'URL ne doit pas contenir ce texte
                    expect(page).not_to_have_url(pattern, timeout=CHECK_TIMEOUT_MS)
                    return ActionResult(True, f"L'URL ne contient pas « {part} », comme attendu", check=True)
                expect(page).to_have_url(pattern, timeout=CHECK_TIMEOUT_MS)
                return ActionResult(True, f"L'URL contient « {part} »", check=True)
            except AssertionError:
                if absent:
                    return ActionResult(True, f"L'URL contient « {part} » alors qu'elle ne devrait pas ({page.url})", check=False)
                return ActionResult(True, f"L'URL ne contient pas « {part} » ({page.url})", check=False)

        return ActionResult(False, f"Action non exécutable : {action.name}")
    except PlaywrightError as exc:
        return ActionResult(False, f"Erreur Playwright : {_short_error(exc)}")


def _order(page_text: str, texts: list[str]) -> tuple[bool, str, None]:
    """Les textes apparaissent-ils dans cet ordre ? La casse et les espaces ne comptent pas.

    Chaque texte est cherché après le précédent, comme le font les tests générés
    (expression régulière « A.*B » avec pytest, recherche pas à pas avec Robot Framework).
    """
    lowered, start, previous = page_text.lower(), 0, None
    for text in texts:
        needle = " ".join(text.split()).lower()
        position = lowered.find(needle, start)
        if position < 0:
            if previous is None or needle not in lowered:
                return False, f"« {text} » absent de la page", None
            return False, f"Ordre non respecté : « {text} » n'apparaît pas après « {previous} »", None
        start, previous = position + len(needle), text
    return True, "Ordre respecté : " + ", puis ".join(f"« {t} »" for t in texts), None


def _check_value(page: Page, locator, element: Element, expected: str, timeout_ms: int, regex: bool = False) -> ActionResult:
    """Contenu d'un champ ou d'une zone de texte, ou option choisie dans une liste (son libellé ou sa valeur).

    Avec regex, la valeur attendue est une expression régulière : un format (date, code, identifiant).
    Pour une liste, le motif s'applique au libellé affiché.
    """
    secret = element.tag == "input" and (element.type or "").lower() == "password"
    name = element.label()
    matches = (lambda value: re.search(expected, value) is not None) if regex else (lambda value: value == expected)

    def read():
        if element.reads_text():  # zone de texte riche, liste « maison » : son texte affiché
            value = " ".join(locator.inner_text(timeout=timeout_ms).split())
        else:
            value = locator.input_value(timeout=timeout_ms)
        if element.tag == "select":
            label = locator.evaluate("e => e.selectedIndex >= 0 ? e.options[e.selectedIndex].text.trim() : ''")
            ok = matches(label) if regex else matches(value) or matches(label)
            shown = label or value
        else:
            ok, shown = matches(value), value
        if secret:  # un mot de passe n'apparaît jamais dans le rapport
            return ok, f"« {name} » : mot de passe " + ("conforme" if ok else "différent de celui attendu"), value
        verb = "indique" if element.tag == "select" else "contient"
        found = "est vide" if shown == "" else f"{verb} « {shown} »"
        if regex:
            return ok, f"« {name} » {found}" + (", conforme au motif" if ok else f", qui ne correspond pas au motif « {expected} »"), value
        if ok:
            return ok, f"« {name} » est vide" if shown == "" else f"« {name} » {verb} « {shown} »", value
        wanted = "vide" if expected == "" else f"« {expected} »"
        return ok, f"« {name} » {found} au lieu de {wanted}", value

    ok, detail, value = _poll(page, read)
    return ActionResult(True, detail, element, check=ok, value=value)


def _check_state(page: Page, locator, element: Element, state: str, timeout_ms: int) -> ActionResult:
    """État d'un élément : actif, grisé, coché ou décoché."""
    readers = {
        "enabled": lambda: locator.is_enabled(timeout=timeout_ms),
        "disabled": lambda: locator.is_disabled(timeout=timeout_ms),
        "checked": lambda: locator.is_checked(timeout=timeout_ms),
        "unchecked": lambda: not locator.is_checked(timeout=timeout_ms),
    }
    label, name = STATE_LABELS[state], element.label()

    def read():
        ok = readers[state]()
        return ok, f"« {name} » est {label}" if ok else f"« {name} » n'est pas {label}", None

    ok, detail, _ = _poll(page, read)
    return ActionResult(True, detail, element, check=ok)
