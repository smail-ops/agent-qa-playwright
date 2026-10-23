"""Double de test de SeleniumLibrary, piloté par Playwright.

Les tests du projet font tourner le fichier .robot généré avec le vrai moteur
Robot Framework : syntaxe, variables, échappements, mots-clés BuiltIn et
mots-clés écrits dans le fichier. Seuls les mots-clés de SeleniumLibrary
utilisés par le générateur sont imités ici avec Playwright, pour se passer de
Chrome et de son pilote, avec les mêmes règles que la vraie bibliothèque
(source : SeleniumLibrary 6.9). En plus, chaque localisateur doit désigner un
seul élément de la page.

Le vrai SeleniumLibrary, avec Chrome, est utilisé dans GitHub Actions (job robot).

Limite du double : Playwright doit répondre à une fenêtre JavaScript dès qu'elle
s'ouvre, alors que Selenium y répond plus tard (Handle Alert). Le double répond
donc OK à chaque fenêtre et note son message : « Handle Alert » ne prend en
charge que la réponse ACCEPT. Le vrai SeleniumLibrary vérifie les autres.
"""

from __future__ import annotations

import re
import time

from playwright.sync_api import expect, sync_playwright
from robot.api import logger
from robot.utils import timestr_to_secs


class SeleniumLibrary:
    ROBOT_LIBRARY_SCOPE = "GLOBAL"

    def __init__(self, timeout: str = "5s", implicit_wait: str = "0s"):
        self._timeout_ms = timestr_to_secs(timeout) * 1000
        self._implicit_wait_ms = timestr_to_secs(implicit_wait) * 1000
        self._playwright = self._browser = self._page = None
        self._frame = None  # cadre sélectionné (Select Frame), None pour la page
        self._alerts: list[str] = []  # messages des fenêtres JavaScript pas encore traitées par Handle Alert
        self._dom_count = 0

    def open_browser(self, url: str, browser: str = "firefox", options=None):
        # options : préférences de Chrome (OPTIONS_CHROME), sans objet pour le Chromium de Playwright.
        # Leur syntaxe est contrôlée avec le vrai SeleniumLibrary dans test_robot.py.
        logger.info(f"Navigateur : {browser}, options : {options}")
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch()
        self._page = self._browser.new_page()
        self._page.on("dialog", self._dialog)
        self._frame, self._alerts = None, []
        self._page.goto(url)

    def _dialog(self, dialog):
        # Selenium affiche le message sur une ligne (Handle Alert)
        self._alerts.append(" ".join(dialog.message.splitlines()))
        dialog.accept()

    def close_browser(self):
        if self._browser:
            self._browser.close()
            self._playwright.stop()
            self._playwright = self._browser = self._page = None

    def go_to(self, url: str):
        self._page.goto(url)
        self._frame = None  # comme Selenium : une navigation revient à la page

    def select_frame(self, locator: str):
        element = self._find(locator)
        if element.evaluate("e => e.tagName.toLowerCase()") not in ("iframe", "frame"):
            raise AssertionError(f"Element '{locator}' is not a frame.")
        self._frame = element.element_handle().content_frame()

    def unselect_frame(self):
        self._frame = None

    def handle_alert(self, action: str = "ACCEPT", timeout=None):
        if action.upper() != "ACCEPT":
            raise AssertionError(f"Le double de SeleniumLibrary répond OK à toutes les fenêtres : action {action} "
                                 "vérifiée seulement avec le vrai SeleniumLibrary.")
        limit = time.monotonic() + (timestr_to_secs(timeout) if timeout else self._timeout_ms / 1000)
        while not self._alerts:
            if time.monotonic() > limit:
                raise AssertionError(f"Alert not found in {timeout or f'{self._timeout_ms / 1000:g} seconds'}.")
            self._page.wait_for_timeout(100)
        return self._alerts.pop(0)

    def input_text_into_alert(self, text: str, action: str = "ACCEPT", timeout=None):
        raise AssertionError("Le double de SeleniumLibrary ne sait pas remplir une fenêtre de saisie : "
                             "vérifiée seulement avec le vrai SeleniumLibrary.")

    def set_window_size(self, width: int, height: int, inner: bool = False):
        self._page.set_viewport_size({"width": width, "height": height})

    def input_text(self, locator: str, text: str, clear: bool = True):
        self._find(locator).fill(text)

    def click_element(self, locator: str):
        self._find(locator).click()

    def select_from_list_by_value(self, locator: str, *values: str):
        self._find(locator).select_option(value=list(values))

    def select_from_list_by_label(self, locator: str, *labels: str):
        self._find(locator).select_option(label=list(labels))

    def wait_until_page_contains_element(self, locator: str, timeout=None, error=None, limit=None):
        delay = timestr_to_secs(timeout) if timeout else self._timeout_ms / 1000
        end = time.monotonic() + delay
        while self._locate(locator).count() == 0:
            if time.monotonic() > end:
                raise AssertionError(error or f"Element '{locator}' did not appear in {delay:g} seconds.")
            self._page.wait_for_timeout(100)

    def wait_until_element_is_visible(self, locator: str, timeout=None, error=None):
        delay = timestr_to_secs(timeout) if timeout else self._timeout_ms / 1000
        end = time.monotonic() + delay
        while not self._visible(locator):
            if time.monotonic() > end:
                raise AssertionError(error or f"Element '{locator}' not visible after {delay:g} seconds.")
            self._page.wait_for_timeout(100)

    def wait_until_element_is_not_visible(self, locator: str, timeout=None, error=None):
        delay = timestr_to_secs(timeout) if timeout else self._timeout_ms / 1000
        end = time.monotonic() + delay
        while self._visible(locator):
            if time.monotonic() > end:
                raise AssertionError(error or f"Element '{locator}' still visible after {delay:g} seconds.")
            self._page.wait_for_timeout(100)

    def set_selenium_implicit_wait(self, value: str) -> str:
        # Comme SeleniumLibrary : renvoie l'ancienne valeur, à redonner ensuite
        previous = f"{self._implicit_wait_ms / 1000:g} seconds"
        self._implicit_wait_ms = timestr_to_secs(value) * 1000
        return previous

    def _visible(self, locator: str) -> bool:
        try:
            found = self._locate(locator)
            return found.count() > 0 and found.first.is_visible()
        except Exception:  # noqa: BLE001 (comme Selenium : un localisateur dom: qui échoue ne trouve rien)
            return False

    def wait_until_location_contains(self, expected: str):
        expect(self._page).to_have_url(re.compile(re.escape(expected)), timeout=self._timeout_ms)

    def wait_until_location_does_not_contain(self, location: str):
        expect(self._page).not_to_have_url(re.compile(re.escape(location)), timeout=self._timeout_ms)

    def execute_javascript(self, *code: str):
        # Selenium exécute le code comme le corps d'une fonction anonyme, dans le cadre sélectionné
        return self._scope().evaluate("() => {" + "".join(code) + "}")

    def get_text(self, locator: str) -> str:
        return self._find(locator).inner_text()

    def get_value(self, locator: str) -> str:
        return self._find(locator).input_value()

    def get_selected_list_label(self, locator: str) -> str:
        return self._find(locator, "select").evaluate("e => e.selectedIndex >= 0 ? e.options[e.selectedIndex].text.trim() : ''")

    # --- Vérifications sur un élément (mêmes messages d'erreur que SeleniumLibrary) ---

    def textfield_value_should_be(self, locator: str, expected: str, message: str | None = None):
        actual = self._find(locator, "input").input_value()
        if actual != expected:
            raise AssertionError(message or f"Value of text field '{locator}' should have been '{expected}' but was '{actual}'.")

    def textarea_value_should_be(self, locator: str, expected: str, message: str | None = None):
        actual = self._find(locator, "textarea").input_value()
        if actual != expected:
            raise AssertionError(message or f"Text area '{locator}' should have had text '{expected}' but it had '{actual}'.")

    def list_selection_should_be(self, locator: str, *expected: str):
        selected = self._find(locator, "select").evaluate(
            "e => Array.from(e.selectedOptions).map((o) => [o.text.trim(), o.value])")
        labels, values = [label for label, _ in selected], [value for _, value in selected]
        if sorted(expected) not in (sorted(labels), sorted(values)):
            raise AssertionError(f"List '{locator}' should have had selection [ {' | '.join(expected)} ] "
                                 f"but selection was [ {' | '.join(labels)} ].")

    def element_should_be_enabled(self, locator: str):
        if not self._is_enabled(locator):
            raise AssertionError(f"Element '{locator}' is disabled.")

    def element_should_be_disabled(self, locator: str):
        if self._is_enabled(locator):
            raise AssertionError(f"Element '{locator}' is enabled.")

    def checkbox_should_be_selected(self, locator: str):
        if not self._find(locator, "input", "checkbox").is_checked():
            raise AssertionError(f"Checkbox '{locator}' should have been selected but was not.")

    def checkbox_should_not_be_selected(self, locator: str):
        if self._find(locator, "input", "checkbox").is_checked():
            raise AssertionError(f"Checkbox '{locator}' should not have been selected.")

    def element_attribute_value_should_be(self, locator: str, attribute: str, expected, message: str | None = None):
        element = self._find(locator)
        if attribute == "checked":  # Selenium lit la propriété des attributs booléens : « true » ou None
            actual = "true" if element.evaluate("e => e.checked === true") else None
        else:
            actual = element.get_attribute(attribute)
        if actual != expected:
            raise AssertionError(message or f"Element '{locator}' attribute should have value '{expected}' "
                                            f"but its value was '{actual}'.")

    def _is_enabled(self, locator: str) -> bool:
        # SeleniumLibrary considère aussi un champ en lecture seule comme désactivé
        element = self._find(locator)
        return element.is_enabled() and element.get_attribute("readonly") is None

    def element_text_should_be(self, locator: str, expected: str, message: str | None = None, ignore_case: bool = False):
        actual = self._find(locator).inner_text()
        if (actual.lower() != expected.lower()) if ignore_case else (actual != expected):
            raise AssertionError(message or f"The text of element '{locator}' should have been '{expected}' but it was '{actual}'.")

    def _scope(self):
        return self._frame or self._page

    def _locate(self, locator: str):
        strategy, _, value = locator.partition(":")
        if strategy == "dom":  # expression JavaScript qui renvoie l'élément, comme SeleniumLibrary
            handle = self._scope().evaluate_handle(f"() => {value}")
            element = handle.as_element()
            if element is None:
                return self._scope().locator("[data-double-dom=absent]")
            self._dom_count += 1
            element.evaluate("(e, n) => e.setAttribute('data-double-dom', n)", str(self._dom_count))
            return self._scope().locator(f'[data-double-dom="{self._dom_count}"]')
        if strategy not in ("css", "xpath"):
            raise AssertionError(f"Localisateur inattendu : {locator}")
        return self._scope().locator(value if strategy == "css" else f"xpath={value}")

    def _find(self, locator: str, tag: str | None = None, kind: str | None = None):
        found = self._locate(locator)
        # timeout=0 voudrait dire « sans limite » pour Playwright, d'où le minimum de 1 ms
        expect(found, f"{locator} doit désigner un seul élément").to_have_count(1, timeout=max(self._implicit_wait_ms, 1))
        if tag is not None:  # SeleniumLibrary refuse aussi un élément du mauvais type
            actual_tag, actual_kind = found.evaluate("e => [e.tagName.toLowerCase(), (e.type || '').toLowerCase()]")
            if actual_tag != tag or (kind is not None and actual_kind != kind):
                raise AssertionError(f"Element with locator '{locator}' not found.")
        return found
