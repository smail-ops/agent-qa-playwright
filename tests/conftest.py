"""Outils partagés par les tests : site de démo local et navigateur Playwright.

Les tests n'ont besoin ni de clé d'API ni d'Internet : ils utilisent le site
du dossier demo_site/ et le faux modèle ScriptedProvider.
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo_site"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # pas de journal HTTP dans la sortie des tests
        pass


@pytest.fixture(scope="session")
def demo_url():
    """Sert le site de démo sur un port libre, le temps des tests."""
    handler = functools.partial(_QuietHandler, directory=str(DEMO_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="session")
def pw_browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def pw_page(pw_browser):
    context = pw_browser.new_context()
    page = context.new_page()
    yield page
    context.close()
