"""Le navigateur d'une exécution : Chromium, Firefox ou WebKit, et un téléphone ou une tablette émulés.

    python -m agent cas.md --navigateur firefox
    python -m agent cas.md --navigateur webkit --appareil "iPhone 15"

WebKit est le moteur de Safari : avec un iPhone émulé, c'est le plus fidèle. Playwright
émule l'appareil (taille d'écran, densité de pixels, navigateur mobile, écran tactile) ;
Firefox, lui, ne sait pas émuler un mobile.

Chaque navigateur s'installe une fois : python -m playwright install firefox webkit.
Les tests générés gardent ces réglages : l'appareil dans le test pytest, le navigateur
correspondant dans le test Robot (Chrome, Firefox ou Safari, avec l'émulation mobile de Chrome).
"""

from __future__ import annotations

import json
import re
import threading
from functools import lru_cache
from pathlib import Path

NAVIGATEURS = ("chromium", "firefox", "webkit")
NOMS = {"chromium": "Chromium", "firefox": "Firefox", "webkit": "WebKit"}
DESKTOP_VIEWPORT = {"width": 1280, "height": 800}  # fenêtre de l'agent sans appareil émulé
APPAREILS_CONSEILLES = ("iPhone 15", "Pixel 7", "Galaxy S24", "iPad Mini")
SELENIUM = {"chromium": "chrome", "firefox": "firefox", "webkit": "safari"}  # même moteur pour le test Robot


class NavigateurAbsent(RuntimeError):
    """Le navigateur demandé n'est pas installé pour Playwright."""


def _snake(name: str) -> str:
    return re.sub(r"([A-Z])", lambda match: "_" + match.group(1).lower(), name)


@lru_cache(maxsize=1)
def appareils() -> dict[str, dict]:
    """Les appareils que Playwright sait émuler : nom -> réglages (user_agent, viewport, is_mobile...).

    La liste est lue dans le paquet de Playwright, sans lancer de navigateur ; une version de Playwright qui la
    range ailleurs la donne elle-même. Vide si rien ne marche : le nom de l'appareil est alors vérifié au lancement.
    """
    return _from_package() or _from_driver()


def _from_package() -> dict[str, dict]:
    """La liste du paquet de Playwright (deviceDescriptorsSource.json), là où il la range."""
    try:
        import playwright
    except ImportError:
        return {}
    package = Path(playwright.__file__).parent / "driver" / "package"
    usual = package / "lib" / "server" / "deviceDescriptorsSource.json"
    for source in [usual] if usual.is_file() else sorted(package.glob("lib/**/deviceDescriptorsSource.json")):
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found = {name: {_snake(key): value for key, value in descriptor.items() if key != "screen"}
                 for name, descriptor in data.items() if isinstance(descriptor, dict)}
        if found:
            return found
    return {}


def _from_driver() -> dict[str, dict]:
    """La liste demandée à Playwright, dans un fil à part : son API synchrone refuse de démarrer dans une boucle
    asyncio, ou à l'intérieur d'un autre Playwright du même fil."""
    found: dict[str, dict] = {}

    def read() -> None:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                found.update({name: {key: value for key, value in descriptor.items() if key != "screen"}
                              for name, descriptor in playwright.devices.items()})
        except Exception:  # Playwright absent ou cassé : le nom sera vérifié au lancement
            found.clear()

    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    worker.join(60)
    return dict(found)


def _cle(name: str) -> str:
    return " ".join(str(name).lower().split())


def nom_appareil(name: str, connus: dict | None = None) -> str | None:
    """Le nom exact de l'appareil (« iphone  15 » donne « iPhone 15 »), ou None s'il est inconnu."""
    connus = appareils() if connus is None else connus
    wanted = _cle(name)
    return next((known for known in connus if _cle(known) == wanted), None)


def choisir(navigateur: str | None, appareil: str | None, connus: dict | None = None) -> tuple[str, str | None]:
    """Le navigateur et l'appareil vérifiés, ou une erreur claire (ValueError)."""
    navigateur = (navigateur or "chromium").strip().lower()
    if navigateur not in NAVIGATEURS:
        raise ValueError(f"navigateur inconnu : « {navigateur} ». Choisis chromium, firefox ou webkit.")
    if not appareil or not str(appareil).strip():
        return navigateur, None
    connus = appareils() if connus is None else connus
    if not connus:  # liste introuvable : Playwright vérifiera le nom au lancement
        return navigateur, " ".join(str(appareil).split())
    nom = nom_appareil(appareil, connus)
    if nom is None:
        exemples = ", ".join(APPAREILS_CONSEILLES)
        raise ValueError(f"appareil inconnu : « {appareil} ». Exemples d'appareils que Playwright sait émuler : {exemples}.")
    if navigateur == "firefox" and connus[nom].get("is_mobile"):
        raise ValueError(f"Firefox ne sait pas émuler un téléphone ou une tablette ({nom}) : choisis chromium ou webkit.")
    return navigateur, nom


def options_contexte(playwright, appareil: str | None) -> dict:
    """Réglages du contexte du navigateur : la fenêtre de l'agent, ou ceux de l'appareil émulé."""
    if not appareil:
        return {"viewport": dict(DESKTOP_VIEWPORT)}
    try:
        return dict(playwright.devices[appareil])
    except KeyError:
        raise ValueError(f"appareil inconnu de Playwright : « {appareil} ».") from None


def lancer(playwright, navigateur: str, headed: bool = False):
    """Lance le navigateur. S'il n'est pas installé, l'erreur donne la commande qui l'installe."""
    from playwright.sync_api import Error as PlaywrightError

    try:
        return getattr(playwright, navigateur).launch(headless=not headed)
    except PlaywrightError as exc:
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            raise NavigateurAbsent(f"{NOMS[navigateur]} n'est pas installé pour Playwright. Installe-le une fois avec : "
                                   f"python -m playwright install {navigateur}") from exc
        raise


def libelle(navigateur: str, version: str = "", appareil: str | None = None) -> str:
    """« Chromium 140.0 », « WebKit 26.0, iPhone 15 émulé »."""
    nom = f"{NOMS.get(navigateur, navigateur)} {version}".strip()
    return f"{nom}, {appareil} émulé" if appareil else nom
