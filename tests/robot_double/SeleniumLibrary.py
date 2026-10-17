"""Double de test de SeleniumLibrary, piloté par Playwright.

Les tests du projet font tourner le fichier .robot généré avec le vrai moteur
Robot Framework : syntaxe, variables, échappements, mots-clés BuiltIn et
mots-clés écrits dans le fichier. Seuls les mots-clés de SeleniumLibrary
utilisés par le générateur sont imités ici avec Playwright, pour se passer de
Chrome et de son pilote. En plus, chaque localisateur doit désigner un seul
élément de la page.

Le vrai SeleniumLibrary, avec Chrome, est utilisé dans GitHub Actions (job robot).
"""

from __future__ import annotations

import re

from playwright.sync_api import expect, sync_playwright
from robot.utils import timestr_to_secs


class SeleniumLibrary:
    ROBOT_LIBRARY_SCOPE = "GLOBAL"

    def __init__(self, timeout: str = "5s", implicit_wait: str = "0s"):
        self._timeout_ms = timestr_to_secs(timeout) * 1000
        self._implicit_wait_ms = timestr_to_secs(implicit_wait) * 1000
        self._playwright = self._browser = self._page = None

    def open_browser(self, url: str, browser: str = "firefox"):
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch()
        self._page = self._browser.new_page()
        self._page.goto(url)

    def close_browser(self):
        if self._browser:
            self._browser.close()
            self._playwright.stop()
            self._playwright = self._browser = self._page = None

    def go_to(self, url: str):
        self._page.goto(url)

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

    def wait_until_location_contains(self, expected: str):
        expect(self._page).to_have_url(re.compile(re.escape(expected)), timeout=self._timeout_ms)

    def wait_until_location_does_not_contain(self, location: str):
        expect(self._page).not_to_have_url(re.compile(re.escape(location)), timeout=self._timeout_ms)

    def execute_javascript(self, *code: str):
        # Selenium exécute le code comme le corps d'une fonction anonyme
        return self._page.evaluate("() => {" + "".join(code) + "}")

    def _find(self, locator: str):
        strategy, _, value = locator.partition(":")
        if strategy not in ("css", "xpath"):
            raise AssertionError(f"Localisateur inattendu : {locator}")
        found = self._page.locator(value if strategy == "css" else f"xpath={value}")
        # timeout=0 voudrait dire « sans limite » pour Playwright, d'où le minimum de 1 ms
        expect(found, f"{locator} doit désigner un seul élément").to_have_count(1, timeout=max(self._implicit_wait_ms, 1))
        return found
