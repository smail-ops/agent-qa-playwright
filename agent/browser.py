"""Tout ce qui touche au navigateur : observer la page et exécuter les actions.

Observer : on liste les éléments interactifs visibles (champs, boutons, liens)
et on les numérote. Le modèle désigne ensuite un élément par son numéro
(« ref ») au lieu d'inventer un sélecteur, ce qui évite les hallucinations.

Pour chaque élément, on calcule aussi un sélecteur lisible et stable
(data-test, id, name...) et son équivalent XPath. Ils ne servent pas à
l'agent, mais aux tests générés à la fin (pytest et Robot Framework), qui
doivent pouvoir rejouer le parcours sans IA.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect

from .actions import STATE_LABELS, Action

REF_ATTR = "data-agent-ref"  # attribut temporaire posé sur chaque élément observé
CHECK_TIMEOUT_MS = 3000  # délai laissé à la page pour atteindre l'état attendu par une vérification

# Script exécuté dans la page pour lister les éléments interactifs.
_OBSERVE_JS = r"""
({refAttr, max}) => {
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
  // Texte du <label> associé à un champ, sans le contenu des champs qu'il englobe
  const labelOf = (el) => {
    const label = el.labels && el.labels[0];
    if (!label) return null;
    const copy = label.cloneNode(true);
    copy.querySelectorAll('input, select, textarea, button').forEach((c) => c.remove());
    return clean(copy.textContent).replace(/\s*:$/, '').slice(0, 80) || null;
  };

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

  document.querySelectorAll('[' + refAttr + ']').forEach((e) => e.removeAttribute(refAttr));
  const candidates = document.querySelectorAll(
    'a[href], button, input:not([type=hidden]), select, textarea, ' +
    '[role=button], [role=link], [role=checkbox], [role=tab], [onclick]'
  );

  const elements = [];
  for (const el of candidates) {
    if (elements.length >= max) break;
    if (!visible(el)) continue;

    const tag = el.tagName.toLowerCase();
    const type = el.getAttribute('type');
    let text = clean(el.innerText);
    if (!text && tag === 'input' && ['submit', 'button', 'reset'].includes(type)) text = clean(el.value);

    const testAttr = ['data-test', 'data-testid', 'data-test-id'].find((a) => el.hasAttribute(a)) || null;
    const attrs = {
      test_attr: testAttr,
      test_id: testAttr ? el.getAttribute(testAttr) : null,
      id: el.id || null,
      name: el.getAttribute('name'),
      placeholder: el.getAttribute('placeholder'),
      aria: el.getAttribute('aria-label'),
      label: labelOf(el),
    };

    // Sélecteur lisible, du plus robuste au moins robuste
    let css = null;
    if (attrs.test_id) css = `[${testAttr}="${q(attrs.test_id)}"]`;
    else if (attrs.id && /^[A-Za-z][\w-]*$/.test(attrs.id)) css = `#${attrs.id}`;
    else if (attrs.name) css = `${tag}[name="${q(attrs.name)}"]`;
    else if (attrs.aria) css = `${tag}[aria-label="${q(attrs.aria)}"]`;
    else if (attrs.placeholder) css = `${tag}[placeholder="${q(attrs.placeholder)}"]`;
    else if (tag === 'input' && el.value) css = `input[value="${q(el.value)}"]`;

    let selector;
    let nth = null;
    if (css) {
      const matches = Array.from(document.querySelectorAll(css));
      if (matches.length > 1) nth = matches.indexOf(el);
      selector = css;
    } else if (text) {
      // Extension Playwright :has-text() : contient le texte, sans tenir compte de la casse
      const needle = text.slice(0, 60);
      selector = `${tag}:has-text("${q(needle)}")`;
      const same = Array.from(document.querySelectorAll(tag))
        .filter((e) => clean(e.innerText).toLowerCase().includes(needle.toLowerCase()));
      if (same.length > 1) nth = same.indexOf(el);
    } else {
      continue;  // élément impossible à désigner de façon fiable
    }

    // Équivalent XPath, pour le test Robot Framework / Selenium généré à la fin
    // (Selenium ne connaît pas :has-text ni .nth de Playwright).
    let xpath = null;
    if (attrs.test_id) xpath = xpathFor(el, `//*[@${testAttr}=${xlit(attrs.test_id)}]`);
    else if (attrs.id && /^[A-Za-z][\w-]*$/.test(attrs.id)) xpath = xpathFor(el, `//*[@id=${xlit(attrs.id)}]`);
    else if (attrs.name) xpath = xpathFor(el, `//${tag}[@name=${xlit(attrs.name)}]`);
    else if (attrs.aria) xpath = xpathFor(el, `//${tag}[@aria-label=${xlit(attrs.aria)}]`);
    else if (attrs.placeholder) xpath = xpathFor(el, `//${tag}[@placeholder=${xlit(attrs.placeholder)}]`);
    else if (tag === 'input' && el.value) xpath = xpathFor(el, `//input[@value=${xlit(el.value)}]`);
    else if (text) {
      const needle = text.slice(0, 60);
      xpath = xpathFor(el, `//${tag}[contains(normalize-space(.), ${xlit(needle)})]`)
        || xpathFor(el, `//${tag}[contains(translate(normalize-space(.), "${UPPER}", "${LOWER}"), ${xlit(needle.toLowerCase())})]`);
    }

    const ref = elements.length + 1;
    el.setAttribute(refAttr, String(ref));
    elements.push({ref, tag, type, text: text.slice(0, 80), attrs, selector, nth, xpath});
  }
  return {elements, text: clean(document.body ? document.body.innerText : '')};
}
"""

_ATTR_LABELS = (("test_id", "data-test"), ("id", "id"), ("name", "name"), ("placeholder", "placeholder"), ("aria", "aria-label"),
                ("label", "label"))


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

    def label(self) -> str:
        """Nom court de l'élément, tel qu'un testeur le désignerait : « Username », « Login »."""
        attrs = self.attrs or {}
        if self.tag in ("input", "textarea", "select") and (self.type or "") not in ("submit", "button", "reset"):
            name = (attrs.get("label") or attrs.get("aria") or attrs.get("placeholder") or attrs.get("name")
                    or attrs.get("id") or attrs.get("test_id"))
        else:
            name = self.text or attrs.get("aria") or attrs.get("test_id") or attrs.get("id")
        return name or self.tag

    def noun(self) -> tuple[str, bool]:
        """« le champ », « la liste »..., et si le nom est féminin (« la case est cochée »)."""
        kind = (self.type or "").lower()
        if self.tag == "input" and kind == "checkbox":
            return "la case", True
        if self.tag == "input" and kind == "radio":
            return "l'option", True
        if self.tag == "select":
            return "la liste", True
        if self.tag in ("input", "textarea") and kind not in ("submit", "button", "reset"):
            return "le champ", False
        return ("le lien", False) if self.tag == "a" else ("le bouton", False)

    def describe(self) -> str:
        """Description d'une ligne, envoyée au modèle."""
        parts = [f"[{self.ref}]", self.tag]
        if self.tag == "input" and self.type:
            parts.append(f"type={self.type}")
        for key, label in _ATTR_LABELS:
            if self.attrs.get(key):
                parts.append(f'{label}="{self.attrs[key]}"')
        if self.text:
            parts.append(f'texte="{self.text}"')
        return " ".join(parts)


@dataclass
class Observation:
    """Ce que l'agent voit de la page à un instant donné."""

    url: str
    title: str
    text: str
    elements: list[Element] = field(default_factory=list)
    screenshot: bytes | None = None  # capture JPEG envoyée au modèle quand la vision est activée

    def find(self, ref: int) -> Element | None:
        return next((e for e in self.elements if e.ref == ref), None)

    def to_prompt(self, max_text: int = 1500) -> str:
        text = self.text if len(self.text) <= max_text else self.text[:max_text] + " [...]"
        lines = [f"URL : {self.url}", f"Titre : {self.title}", "Texte visible (extrait) :", text or "(vide)", ""]
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


def observe(page: Page, max_elements: int = 80) -> Observation:
    """Photographie l'état de la page : URL, titre, texte et éléments numérotés."""
    data = page.evaluate(_OBSERVE_JS, {"refAttr": REF_ATTR, "max": max_elements})
    elements = [Element(**item) for item in data["elements"]]
    return Observation(url=page.url, title=page.title(), text=data["text"], elements=elements)


def _short_error(exc: Exception) -> str:
    """Première ligne du message d'erreur Playwright, souvent très long."""
    return str(exc).strip().splitlines()[0][:300] if str(exc).strip() else exc.__class__.__name__


def visible_text(page: Page) -> str:
    """Texte visible de la page, espaces regroupés, comme le lit un utilisateur."""
    return " ".join(page.locator("body").inner_text().split())


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


def execute(page: Page, action: Action, observation: Observation | None, timeout_ms: int = 5000) -> ActionResult:
    """Exécute une action sur la page et renvoie son résultat, sans jamais lever d'exception."""
    args = action.args
    try:
        if action.name == "goto":
            page.goto(args["url"], wait_until="load", timeout=15000)
            return ActionResult(True, f"Page ouverte : {page.url}")

        if action.name in ("click", "fill", "select", "check_value", "check_state"):
            element = observation.find(args["ref"]) if observation else None
            if element is None:
                return ActionResult(False, f"Aucun élément [{args['ref']}] sur la page actuelle")
            locator = page.locator(f'[{REF_ATTR}="{element.ref}"]')
            if action.name == "check_value":
                return _check_value(page, locator, element, args["value"], timeout_ms)
            if action.name == "check_state":
                return _check_state(page, locator, element, args["state"], timeout_ms)
            if action.name == "click":
                locator.click(timeout=timeout_ms)
                _settle(page)
                return ActionResult(True, f"Clic sur {element.describe()}", element)
            if action.name == "fill":
                locator.fill(args["text"], timeout=timeout_ms)
                return ActionResult(True, f"Champ rempli : {element.describe()}", element)
            selected = locator.select_option(args["value"], timeout=timeout_ms)
            _settle(page)
            # Playwright accepte le libellé ou la valeur : on garde la valeur réelle de l'option
            return ActionResult(True, f"Option « {args['value']} » choisie", element, value=selected[0] if selected else None)

        if action.name == "check_text":
            text, absent = args["text"], args.get("absent", False)
            body = expect(page.locator("body"))
            try:
                if absent:  # vérification négative : le texte ne doit pas apparaître
                    body.not_to_contain_text(text, ignore_case=True, use_inner_text=True, timeout=CHECK_TIMEOUT_MS)
                    return ActionResult(True, f"Texte « {text} » absent, comme attendu", check=True)
                body.to_contain_text(text, ignore_case=True, use_inner_text=True, timeout=CHECK_TIMEOUT_MS)
                # La vérification ignore la casse ; Selenium non. On note la casse exacte de la page.
                page_text = page.locator("body").inner_text()
                start = page_text.lower().find(text.lower())
                exact = page_text[start : start + len(text)] if start >= 0 else text
                if exact.lower() != text.lower():  # cas rare : la mise en minuscules change la longueur
                    exact = text
                return ActionResult(True, f"Texte « {text} » présent", check=True, value=exact)
            except AssertionError:
                if absent:
                    return ActionResult(True, f"Texte « {text} » présent alors qu'il devrait être absent", check=False)
                return ActionResult(True, f"Texte « {text} » absent de la page", check=False)

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


def _check_value(page: Page, locator, element: Element, expected: str, timeout_ms: int) -> ActionResult:
    """Contenu d'un champ, ou option choisie dans une liste (son libellé ou sa valeur)."""
    secret = element.tag == "input" and (element.type or "").lower() == "password"
    name = element.label()

    def read():
        value = locator.input_value(timeout=timeout_ms)
        if element.tag == "select":
            label = locator.evaluate("e => e.selectedIndex >= 0 ? e.options[e.selectedIndex].text.trim() : ''")
            ok, shown = expected in (value, label), label or value
        else:
            ok, shown = value == expected, value
        if secret:  # un mot de passe n'apparaît jamais dans le rapport
            return ok, f"« {name} » : mot de passe " + ("conforme" if ok else "différent de celui attendu"), value
        verb = "indique" if element.tag == "select" else "contient"
        if ok:
            return ok, f"« {name} » est vide" if shown == "" else f"« {name} » {verb} « {shown} »", value
        found = "est vide" if shown == "" else f"{verb} « {shown} »"
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
