"""Génération d'un test Robot Framework (SeleniumLibrary) à partir d'une exécution.

Même principe que le script pytest : l'agent explore le site une fois avec
Playwright et l'IA, puis le test généré rejoue les actions réussies avec
Selenium, sans IA. Robot Framework et SeleniumLibrary sont très utilisés dans
les équipes de test, souvent avec un outil de gestion des tests comme Squash TM.

Exemple de test produit :

    *** Test Cases ***
    CT-01 Connexion avec un utilisateur valide
        Ouvrir le navigateur    https://www.saucedemo.com/
        Input Text    css:#user-name    standard_user
        Cliquer    css:[data-test="login-button"]
        Wait Until Location Contains    inventory
        La page affiche    Products

Un clic passe par le mot-clé « Cliquer », écrit dans le fichier : si la page
remplace l'élément au moment du clic (une liste rechargée par l'API, une
application monopage), Selenium lève StaleElementReferenceException, et le clic
est retenté avec l'élément cherché de nouveau.

Les vérifications de texte passent par des mots-clés écrits dans le fichier :
« La page affiche », « La page n'affiche pas », « La page affiche dans l'ordre »
et « La page affiche ce nombre de fois ». Comme l'agent, ils lisent le texte
visible de la page (innerText), et celui des composants web (Shadow DOM), que
innerText ne contient pas. « Wait Until Page Contains » de SeleniumLibrary
compterait aussi le texte caché, par exemple un message d'erreur présent dans
la page mais masqué : le test passerait à tort.

Un élément d'un cadre (iframe) se manipule après « Select Frame », et un élément
d'un composant web se désigne avec un localisateur dom: qui passe par son hôte
(document.querySelector('hôte').shadowRoot...). Une fenêtre JavaScript ouverte
par une action reçoit la même réponse que pendant l'exploration, après
vérification de son message (« Accepter la fenêtre », « Refuser la fenêtre »).
Avant d'écrire dans une zone de texte riche (contenteditable, comme l'éditeur
TinyMCE), le test clique dedans : sans ce clic, Chrome 155 n'y tape rien.

Les vérifications sur un élément (valeur d'un champ, option choisie, bouton
grisé, case cochée) utilisent les mots-clés de SeleniumLibrary, dans
« Wait Until Keyword Succeeds » : comme l'agent, le test laisse quelques
secondes à la page pour atteindre l'état attendu.

« Ouvrir le navigateur », écrit lui aussi dans le fichier, ouvre le navigateur
à la taille de la fenêtre de l'agent. Avec Chrome, il coupe le gestionnaire de
mots de passe : après une connexion avec un mot de passe connu des fuites de
données, comme secret_sauce sur SauceDemo, Chrome affiche une alerte « mot de
passe compromis » qui interrompt le test. Le Chromium de Playwright, utilisé
par l'agent, n'a pas cette alerte.

Le test reprend aussi les conditions de l'exécution : le navigateur de l'agent
(Chrome, Firefox, ou Safari pour WebKit), l'appareil émulé (émulation mobile de
Chrome), la session reprise ou enregistrée (dossier sessions/, mots-clés
« Reprendre la session » et « Enregistrer la session ») et {unique}, qui
devient ${UNIQUE} : une valeur nouvelle à chaque lancement.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from .browser import AD_DOMAINS, OPEN_SHADOW_JS
from .coffre import env_names, split
from .donnees import FORMAT, morceaux
from .navigateur import NOMS, SELENIUM, appareils

if TYPE_CHECKING:
    from .browser import Element
    from .runner import RunResult

SEP = "    "  # Robot Framework sépare les arguments par au moins deux espaces
WINDOW_SIZE = ("1280", "800")  # même taille de fenêtre que l'agent

# Préférences de Chrome pour les tests rejoués : ni gestionnaire de mots de passe, ni alerte
# « mot de passe compromis » après une connexion (voir le mot-clé « Ouvrir le navigateur »).
CHROME_PREFS = {
    "credentials_enable_service": False,
    "profile.password_manager_enabled": False,
    "profile.password_manager_leak_detection": False,
}
CHROME_NAMES = ("chrome", "googlechrome", "gc", "headlesschrome")  # noms qui ouvrent Chrome dans SeleniumLibrary

_PREFS = "{" + ", ".join(f'"{name}": {value}' for name, value in CHROME_PREFS.items()) + "}"
VARIABLES = f"""*** Variables ***
${{BROWSER}}        chrome
# Attente maximale d'un élément, d'un texte ou d'une URL.
${{DELAI}}          5s
# Préférences de Chrome, utilisées par le mot-clé « Ouvrir le navigateur ».
${{OPTIONS_CHROME}}    add_experimental_option("prefs", {_PREFS})
"""


def mobile_emulation(appareil: str) -> dict | None:
    """L'émulation mobile de Chrome pour cet appareil : taille d'écran, densité, écran tactile, navigateur mobile."""
    descriptor = appareils().get(appareil)
    if not descriptor:
        return None
    viewport = descriptor.get("viewport") or {}
    return {"deviceMetrics": {"width": viewport.get("width", 390), "height": viewport.get("height", 660),
                              "pixelRatio": float(descriptor.get("device_scale_factor", 2)),
                              "touch": bool(descriptor.get("has_touch", True))},
            "userAgent": descriptor.get("user_agent", "")}


def robot_variables(run: RunResult) -> str:
    """La section *** Variables *** : le navigateur de l'agent, et l'appareil émulé par Chrome s'il y en a un."""
    navigateur, appareil = getattr(run, "navigateur", "chromium") or "chromium", getattr(run, "appareil", None)
    mobile = mobile_emulation(appareil) if appareil else None
    if navigateur == "chromium" and mobile is None:
        return VARIABLES
    options = f'add_experimental_option("prefs", {_PREFS})'
    if mobile is not None:
        comment = (f"# Même appareil que l'agent ({appareil}), émulé par Chrome : Selenium n'émule un mobile qu'avec "
                   "Chrome.")
        browser = "chrome"
        options += f';add_experimental_option("mobileEmulation", {mobile!r})'
    elif navigateur == "webkit":
        comment = ("# Même moteur que l'agent (WebKit) : Safari, sur Mac. À activer une fois dans le Terminal : "
                   "safaridriver --enable")
        browser = SELENIUM[navigateur]
    else:
        comment = f"# Même navigateur que l'agent : {NOMS[navigateur]}."
        browser = SELENIUM[navigateur]
    usage = " (et émulation de l'appareil)" if mobile else ""
    return (f"*** Variables ***\n{comment}\n${{BROWSER}}        {browser}\n# Attente maximale d'un élément, d'un texte ou "
            f"d'une URL.\n${{DELAI}}          5s\n# Préférences de Chrome, utilisées par le mot-clé « Ouvrir le navigateur »"
            f"{usage}.\n${{OPTIONS_CHROME}}    {options}\n")

_HEADER = """*** Settings ***
Documentation     Test généré automatiquement par agent-qa-playwright.
...               Cas de test : {title}
...               Rejoue les actions réussies de l'agent avec Selenium, sans IA.
# implicit_wait : avant d'agir, Selenium attend que l'élément apparaisse, comme Playwright.
Library           SeleniumLibrary    timeout=${{DELAI}}    implicit_wait=${{DELAI}}
{setup}Test Teardown     Close Browser

{variables}
*** Test Cases ***
{name}
{body}
"""

# Mots de passe : lus dans le fichier .env du projet avant le test, jamais écrits dans le fichier
SECRETS_SETUP = "Suite Setup       Charger les mots de passe\n"
_SECRETS = """Charger les mots de passe
    [Documentation]    Lit le fichier .env du projet : l'agent y a rangé les mots de passe de test (%{MOT_DE_PASSE_...}),
    ...    qui ne sont jamais écrits dans ce fichier.
    Evaluate    dotenv.load_dotenv(dotenv.find_dotenv(usecwd=True))    modules=dotenv
"""


def secret_cell(text: str, names: dict[str, str], unique: str | None = None) -> str:
    """Une valeur pour Robot Framework : un mot de passe devient %{MOT_DE_PASSE_...}, lu dans le fichier .env,
    et la valeur unique de l'agent devient ${UNIQUE}, recréée à chaque lancement."""
    cells = []
    for is_secret, value in split(text, names):
        if not value:
            continue
        if is_secret:
            cells.append("%{" + value + "}")
            continue
        cells += ["${UNIQUE}" if is_unique else robot_arg(chunk) for is_unique, chunk in morceaux(value, unique)]
    return "".join(cells) or "${EMPTY}"


value_cell = secret_cell  # le même mot-clé pour toute valeur tapée ou vérifiée

# {unique} : une valeur nouvelle à chaque lancement, comme pour l'agent (voir donnees.py)
UNIQUE_SETUP = "Test Setup        Créer la valeur unique\n"
_UNIQUE = f"""Créer la valeur unique
    [Documentation]    {{unique}} du cas de test : une valeur nouvelle à chaque lancement (mois, jour, heure, minutes,
    ...    secondes), pour créer des données qui n'existent pas encore, comme un e-mail ou un identifiant.
    ${{valeur}}=    Evaluate    datetime.datetime.now().strftime("{FORMAT}")    modules=datetime
    Set Test Variable    ${{UNIQUE}}    ${{valeur}}
"""

# Sessions : le test reprend celle d'un autre test (déjà connecté), ou garde la sienne. Fichier sessions/<nom>.json,
# au format storage_state de Playwright : l'agent, le test pytest et le test Robot partagent les mêmes sessions.
_SESSION_LOAD = """Reprendre la session
    [Documentation]    Le test commence déjà connecté : cookies et stockage local de la session enregistrée par le cas
    ...    qui contient « Enregistrer la session : <nom> » (fichier sessions/<nom>.json, à la racine du projet).
    [Arguments]    ${nom}
    ${fichier}=    Evaluate    pathlib.Path("sessions") / ($nom + ".json")    modules=pathlib
    ${existe}=    Evaluate    $fichier.is_file()
    Skip If    not $existe    Session « ${nom} » absente du dossier sessions : lance d'abord le test qui l'enregistre.
    ${session}=    Evaluate    json.loads($fichier.read_text(encoding="utf-8"))    modules=json
    ${selenium}=    Get Library Instance    SeleniumLibrary
    ${hote}=    Execute Javascript    return location.hostname
    FOR    ${cookie}    IN    @{session}[cookies]
        IF    $hote.endswith($cookie["domain"].lstrip("."))
            ${pour_selenium}=    Evaluate    {"name": $cookie["name"], "value": $cookie["value"], "path": $cookie.get("path") or "/", "secure": bool($cookie.get("secure")), "httpOnly": bool($cookie.get("httpOnly")), **({"expiry": int($cookie["expires"])} if $cookie.get("expires", -1) > 0 else {})}
            Run Keyword And Ignore Error    Evaluate    $selenium.driver.add_cookie($pour_selenium)
        END
    END
    ${origine}=    Execute Javascript    return location.origin
    FOR    ${site}    IN    @{session}[origins]
        IF    $site["origin"] == $origine
            FOR    ${entree}    IN    @{site}[localStorage]
                Execute Javascript    localStorage.setItem(arguments[0], arguments[1])    ARGUMENTS    ${entree}[name]    ${entree}[value]
            END
        END
    END
    Reload Page
"""

_SESSION_SAVE = """Enregistrer la session
    [Documentation]    Garde la session (cookies et stockage local) dans sessions/<nom>.json, au format de Playwright :
    ...    les tests qui la reprennent commencent déjà connectés.
    [Arguments]    ${nom}
    ${selenium}=    Get Library Instance    SeleniumLibrary
    ${cookies}=    Evaluate    [{"name": c["name"], "value": c["value"], "domain": c.get("domain", ""), "path": c.get("path", "/"), "expires": c.get("expiry", -1), "httpOnly": c.get("httpOnly", False), "secure": c.get("secure", False), "sameSite": c.get("sameSite", "Lax")} for c in $selenium.driver.get_cookies()]
    ${origine}=    Execute Javascript    return location.origin
    ${stockage}=    Execute Javascript    return Object.keys(localStorage).map((cle) => ({name: cle, value: localStorage.getItem(cle)}))
    ${fichier}=    Evaluate    pathlib.Path("sessions") / ($nom + ".json")    modules=pathlib
    Evaluate    $fichier.parent.mkdir(exist_ok=True)
    Evaluate    $fichier.write_text(json.dumps({"cookies": $cookies, "origins": [{"origin": $origine, "localStorage": $stockage}]}, ensure_ascii=False, indent=2), encoding="utf-8")    modules=json
"""


def typing_keyword(element, secret: bool = False) -> str:
    """Input Password pour un mot de passe (SeleniumLibrary ne l'écrit pas dans son journal), sinon Input Text."""
    password = element.tag == "input" and (element.type or "").lower() == "password"
    return "Input Password" if password or secret else "Input Text"


_OPEN = f"""Ouvrir le navigateur
    [Documentation]    Ouvre le navigateur sur la page, à la taille de la fenêtre de l'agent.
    ...    Avec Chrome, le gestionnaire de mots de passe est coupé (variable OPTIONS_CHROME) : après une connexion
    ...    avec un mot de passe connu des fuites de données, comme celui du site d'entraînement SauceDemo, Chrome
    ...    afficherait une alerte « mot de passe compromis » qui interrompt le test.
    [Arguments]    ${{url}}
    ${{chrome}}=    Evaluate    $BROWSER.lower().replace(" ", "") in {CHROME_NAMES!r}
    IF    ${{chrome}}
        Open Browser    ${{url}}    ${{BROWSER}}    options=${{OPTIONS_CHROME}}
    ELSE
        Open Browser    ${{url}}    ${{BROWSER}}
    END
    Set Window Size    {WINDOW_SIZE[0]}    {WINDOW_SIZE[1]}
"""

# Avec un appareil émulé, Chrome donne à la page la taille de l'écran de l'appareil : pas de taille de fenêtre
_OPEN_MOBILE = _OPEN.replace("Ouvre le navigateur sur la page, à la taille de la fenêtre de l'agent.",
                             "Ouvre le navigateur sur la page, avec l'appareil émulé par l'agent.").replace(
    f"    Set Window Size    {WINDOW_SIZE[0]}    {WINDOW_SIZE[1]}\n", "")


def open_block(run: RunResult) -> str:
    """Le mot-clé « Ouvrir le navigateur » : à la taille de la fenêtre de l'agent, ou avec son appareil émulé."""
    return _OPEN_MOBILE if getattr(run, "appareil", None) and mobile_emulation(run.appareil) else _OPEN

# Texte affiché : innerText de la page, puis celui des composants web, que innerText ne contient pas.
# Les lignes du code JavaScript sont mises bout à bout par SeleniumLibrary, sans espace.
_VISIBLE = """Texte visible de la page
    [Documentation]    Texte affiché à l'écran, comme le lit l'agent : innerText ne renvoie pas un message caché.
    ...    Le texte des composants web (Shadow DOM), que innerText ne contient pas, est ajouté à la fin.
    ${texte}=    Execute Javascript
    ...    const parts = [document.body.innerText];
    ...    const visiter = (racine) => racine.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) {
    ...    parts.push(Array.from(el.shadowRoot.children).filter((c) => !['STYLE', 'SCRIPT', 'TEMPLATE', 'LINK']
    ...    .includes(c.tagName) && getComputedStyle(c).display !== 'none').map((c) => c.innerText || '').join(' '));
    ...    visiter(el.shadowRoot); } });
    ...    visiter(document); return parts.join(' ');
    RETURN    ${texte}
"""

# Un clic : si la page remplace l'élément au moment du clic, Selenium ne le retrouve plus. Playwright, lui, cherche
# l'élément à chaque action : le test Robot fait de même en retentant le clic.
_CLICK = """Cliquer
    [Documentation]    Clique sur l'élément, comme l'agent. Si la page le remplace au moment du clic (une liste
    ...    rechargée par l'API, une application monopage), Selenium lève StaleElementReferenceException :
    ...    le clic est retenté, avec l'élément cherché de nouveau.
    [Arguments]    ${localisateur}
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Click Element    ${localisateur}
"""

_SHOWS = """La page affiche
    [Documentation]    Attend que le texte soit affiché. La casse et les espaces ne comptent pas, comme pour l'agent.
    [Arguments]    ${texte}
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Le texte visible contient    ${texte}

Le texte visible contient
    [Arguments]    ${texte}
    ${visible}=    Texte visible de la page
    Should Contain    ${visible}    ${texte}    msg=Le texte « ${texte} » n'est pas affiché.    values=False
    ...    ignore_case=True    strip_spaces=True    collapse_spaces=True
"""

_HIDES = """La page n'affiche pas
    [Documentation]    Attend que le texte ne soit plus affiché. Un texte caché compte comme absent.
    [Arguments]    ${texte}
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Le texte visible ne contient pas    ${texte}

Le texte visible ne contient pas
    [Arguments]    ${texte}
    ${visible}=    Texte visible de la page
    Should Not Contain    ${visible}    ${texte}    msg=Le texte « ${texte} » est affiché alors qu'il devrait être absent.
    ...    values=False    ignore_case=True    strip_spaces=True    collapse_spaces=True
"""


_ORDER = """La page affiche dans l'ordre
    [Documentation]    Attend que les textes soient affichés dans cet ordre, par exemple après un tri.
    ...    La casse et les espaces ne comptent pas.
    [Arguments]    @{textes}
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Les textes visibles se suivent    @{textes}

Les textes visibles se suivent
    [Arguments]    @{textes}
    ${visible}=    Texte visible de la page
    ${visible}=    Evaluate    " ".join($visible.split()).lower()
    ${debut}=    Set Variable    ${0}
    FOR    ${texte}    IN    @{textes}
        ${cherche}=    Evaluate    " ".join($texte.split()).lower()
        ${position}=    Evaluate    $visible.find($cherche, $debut)
        Should Be True    $position >= 0    msg=Ordre non respecté : « ${texte} » n'est pas affiché à la place attendue.
        ${debut}=    Evaluate    $position + len($cherche)
    END
"""

_COUNT = """La page affiche ce nombre de fois
    [Documentation]    Attend que le texte soit affiché exactement ce nombre de fois.
    ...    La casse et les espaces ne comptent pas.
    [Arguments]    ${texte}    ${nombre}
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Le texte visible apparaît    ${texte}    ${nombre}

Le texte visible apparaît
    [Arguments]    ${texte}    ${nombre}
    ${visible}=    Texte visible de la page
    ${trouve}=    Evaluate    " ".join($visible.split()).lower().count(" ".join($texte.split()).lower())
    Should Be Equal As Integers    ${trouve}    ${nombre}    msg=« ${texte} » est affiché ${trouve} fois au lieu de ${nombre}.    values=False
"""

# Texte d'une zone de texte riche (contenteditable), et vérifications d'un format (expression régulière)
_ELEMENT_TEXT = """Le texte de l'élément est
    [Documentation]    Texte affiché dans l'élément, une zone de texte riche par exemple. Les espaces ne comptent pas.
    [Arguments]    ${localisateur}    ${attendu}
    ${texte}=    Get Text    ${localisateur}
    Should Be Equal    ${texte}    ${attendu}    msg=L'élément affiche « ${texte} » au lieu de « ${attendu} ».    values=False
    ...    strip_spaces=True    collapse_spaces=True
"""

_VALUE_PATTERN = """La valeur correspond au motif
    [Documentation]    La valeur du champ suit le motif (expression régulière) : un code généré, une date...
    [Arguments]    ${localisateur}    ${motif}
    ${valeur}=    Get Value    ${localisateur}
    Should Match Regexp    ${valeur}    ${motif}    msg=« ${valeur} » ne correspond pas au motif « ${motif} ».    values=False
"""

_TEXT_PATTERN = """Le texte correspond au motif
    [Documentation]    Le texte affiché dans l'élément, espaces regroupés, suit le motif (expression régulière).
    [Arguments]    ${localisateur}    ${motif}
    ${texte}=    Get Text    ${localisateur}
    ${texte}=    Evaluate    " ".join($texte.split())
    Should Match Regexp    ${texte}    ${motif}    msg=« ${texte} » ne correspond pas au motif « ${motif} ».    values=False
"""

_LABEL_PATTERN = """L'option choisie correspond au motif
    [Documentation]    Le libellé de l'option choisie dans la liste suit le motif (expression régulière).
    [Arguments]    ${localisateur}    ${motif}
    ${libelle}=    Get Selected List Label    ${localisateur}
    Should Match Regexp    ${libelle}    ${motif}    msg=« ${libelle} » ne correspond pas au motif « ${motif} ».    values=False
"""

# Fenêtres JavaScript : même réponse que pendant l'exploration, après vérification du message
_ALERTS = """Accepter la fenêtre
    [Documentation]    Vérifie le message de la fenêtre JavaScript (alerte, confirmation) ouverte par l'action
    ...    précédente, puis répond OK.
    [Arguments]    ${message}
    ${affiche}=    Handle Alert    ACCEPT
    Should Be Equal    ${affiche}    ${message}    msg=La fenêtre affiche « ${affiche} » au lieu de « ${message} ».
    ...    values=False    strip_spaces=True    collapse_spaces=True

Refuser la fenêtre
    [Documentation]    Vérifie le message de la fenêtre JavaScript ouverte par l'action précédente, puis répond Annuler.
    [Arguments]    ${message}
    ${affiche}=    Handle Alert    DISMISS
    Should Be Equal    ${affiche}    ${message}    msg=La fenêtre affiche « ${affiche} » au lieu de « ${message} ».
    ...    values=False    strip_spaces=True    collapse_spaces=True
"""

# Bandeau de cookies : accepté par l'agent pendant l'exploration, accepté par le test s'il s'affiche
_COOKIES = """Accepter le bandeau de cookies
    [Documentation]    Accepte le bandeau de cookies s'il s'affiche, comme l'agent pendant l'exploration : à la première
    ...    visite, il couvre la page et bloque les clics. S'il ne s'affiche pas (consentement déjà donné, visite
    ...    depuis un pays hors d'Europe), il n'y a rien à faire.
    [Arguments]    ${bouton}
    ${affiche}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${bouton}    ${DELAI}
    IF    ${affiche}
        Click Element    ${bouton}
        # Sans attente implicite : une fois le bouton disparu, Selenium ne le cherche pas pendant tout le délai
        ${attente}=    Set Selenium Implicit Wait    0s
        Wait Until Element Is Not Visible    ${bouton}
        Set Selenium Implicit Wait    ${attente}
    END
"""

# Bandeau dans un cadre (iframe) de consentement : le cadre n'existe pas quand le consentement est déjà donné
_COOKIES_FRAME = """Accepter le bandeau de cookies du cadre
    [Documentation]    Le bandeau est dans un cadre (iframe) posé sur la page, comme ceux de Sourcepoint ou de TrustArc :
    ...    s'il s'affiche, le test entre dans le cadre et accepte, comme l'agent pendant l'exploration.
    [Arguments]    ${cadre}    ${bouton}
    ${affiche}=    Run Keyword And Return Status    Wait Until Element Is Visible    ${cadre}    ${DELAI}
    IF    ${affiche}
        Select Frame    ${cadre}
        Accepter le bandeau de cookies    ${bouton}
        Unselect Frame
    END
"""

# Glisser-déposer HTML5 (attribut draggable) : Selenium ne sait pas le faire à la souris dans Chrome.
# Le mot-clé envoie au navigateur les événements du glisser-déposer. Les lignes du code sont mises bout à bout.
_DRAG = """Glisser-déposer
    [Documentation]    Glisse l'élément et le dépose sur la cible. Pour un glisser-déposer HTML5 (attribut draggable),
    ...    le mot-clé envoie les événements dragstart, dragover et drop : Selenium ne sait pas le faire à la souris.
    [Arguments]    ${source}    ${cible}
    ${depart}=    Get WebElement    ${source}
    ${arrivee}=    Get WebElement    ${cible}
    Execute Javascript
    ...    const [depart, arrivee] = arguments; const donnees = new DataTransfer();
    ...    const envoyer = (element, type) => element.dispatchEvent(new DragEvent(type,
    ...    {bubbles: true, cancelable: true, dataTransfer: donnees}));
    ...    envoyer(depart, 'dragstart'); envoyer(arrivee, 'dragenter'); envoyer(arrivee, 'dragover');
    ...    envoyer(arrivee, 'drop'); envoyer(depart, 'dragend');
    ...    ARGUMENTS    ${depart}    ${arrivee}
"""

# Composants web fermés : créés ouverts avant le chargement de la page, comme pendant l'exploration
_CLOSED = f"""Ouvrir les composants fermés
    [Documentation]    Les composants web fermés (Shadow DOM « closed ») sont créés ouverts, comme pendant l'exploration :
    ...    sinon, Selenium ne peut pas y entrer. Chrome seulement (commande CDP), puis la page est rechargée.
    ${{chrome}}=    Evaluate    $BROWSER.lower().replace(" ", "") in {CHROME_NAMES!r}
    IF    not ${{chrome}}
        Log    Les composants fermés ne s'ouvrent qu'avec Chrome : le test risque d'échouer avec ${{BROWSER}}.    level=WARN
        RETURN
    END
    ${{selenium}}=    Get Library Instance    SeleniumLibrary
    Evaluate    $selenium.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {{"source": $OUVRIR_COMPOSANTS}})
    Reload Page
"""
CLOSED_VARIABLE = f"# Script qui crée ouverts les composants web fermés (mot-clé « Ouvrir les composants fermés »).\n${{OUVRIR_COMPOSANTS}}    {OPEN_SHADOW_JS}\n"

# Régies publicitaires bloquées, comme pendant l'exploration : une publicité plein écran couvrirait la page
_ADS = f"""Bloquer les publicités
    [Documentation]    Bloque les régies publicitaires, comme pendant l'exploration : une publicité plein écran (l'adresse
    ...    finit alors par #google_vignette) couvrirait la page. Chrome seulement (commande CDP), puis la page est rechargée.
    ${{chrome}}=    Evaluate    $BROWSER.lower().replace(" ", "") in {CHROME_NAMES!r}
    IF    not ${{chrome}}
        Log    Les publicités ne sont bloquées qu'avec Chrome : une publicité plein écran pourrait gêner le test.    level=WARN
        RETURN
    END
    ${{selenium}}=    Get Library Instance    SeleniumLibrary
    Evaluate    $selenium.driver.execute_cdp_cmd("Network.enable", {{}})
    Evaluate    $selenium.driver.execute_cdp_cmd("Network.setBlockedURLs", {{"urls": $PUBLICITES}})
    Reload Page
"""
ADS_VARIABLE = "# Adresses des régies publicitaires (mot-clé « Bloquer les publicités »).\n@{PUBLICITES}" + "".join(
    f"    *{domain}*" for domain in AD_DOMAINS) + "\n"

# Touches du clavier : nom Playwright -> nom Selenium, pour « Press Keys »
_SELENIUM_KEYS = {"Enter": "ENTER", "Escape": "ESCAPE", "Tab": "TAB", "Backspace": "BACK_SPACE", "Delete": "DELETE",
                  "Space": "SPACE", "ArrowUp": "ARROW_UP", "ArrowDown": "ARROW_DOWN", "ArrowLeft": "ARROW_LEFT",
                  "ArrowRight": "ARROW_RIGHT", "Home": "HOME", "End": "END", "PageUp": "PAGE_UP", "PageDown": "PAGE_DOWN",
                  "Control": "CTRL", "Shift": "SHIFT", "Alt": "ALT", "Meta": "COMMAND"}


def selenium_keys(key: str) -> str:
    """« Control+A » pour Playwright devient « CTRL+a » pour Press Keys de SeleniumLibrary."""
    return "+".join(_SELENIUM_KEYS.get(part, part.lower() if len(part) == 1 else part) for part in key.split("+"))


# Mots-clés qui lisent le texte visible : ils ont besoin de « Texte visible de la page »
TEXT_BLOCKS = (_SHOWS, _HIDES, _ORDER, _COUNT)

# Vérification d'état -> mot-clé SeleniumLibrary (et ses arguments après le localisateur)
_STATE_KEYWORDS = {
    "enabled": ("Element Should Be Enabled",),
    "disabled": ("Element Should Be Disabled",),
}


def _state_keyword(element: Element, state: str) -> tuple[str, ...]:
    """Mot-clé qui vérifie l'état, puis les arguments qui suivent le localisateur."""
    if state in _STATE_KEYWORDS:
        return _STATE_KEYWORDS[state]
    checked = state == "checked"
    kind = (element.type or "").lower() if element.tag == "input" else ""
    if kind == "checkbox":
        return ("Checkbox Should Be Selected",) if checked else ("Checkbox Should Not Be Selected",)
    if kind == "radio":  # Selenium lit la propriété checked : « true », ou rien
        return ("Element Attribute Value Should Be", "checked", "true" if checked else "${None}")
    return ("Element Attribute Value Should Be", "aria-checked", "true" if checked else "false")


def robot_arg(value: str) -> str:
    """Écrit une valeur pour qu'elle soit lue telle quelle par Robot Framework.

    Robot donne un sens particulier à ${...}, aux espaces multiples, au # en début
    de cellule et à « nom=valeur » : on les échappe.
    """
    if value == "":
        return "${EMPTY}"
    text = value.replace("\\", "\\\\").replace("\n", " ")
    text = re.sub(r"([$@&%])\{", r"\\\1{", text)  # pas d'interprétation comme variable
    text = re.sub(r" {2,}", lambda m: " " + "${SPACE}" * (len(m.group()) - 1), text)
    if text.startswith(" "):
        text = "${SPACE}" + text[1:]
    if text.endswith(" "):
        text = text[:-1] + "${SPACE}"
    if text.startswith("#"):
        text = "\\" + text  # sinon le reste de la ligne devient un commentaire
    if re.match(r"^[A-Za-z_]\w*=", text):
        text = text.replace("=", "\\=", 1)  # sinon Robot y voit un argument nommé
    return text


def js_string(value: str) -> str:
    """Chaîne JavaScript lisible : entre apostrophes, sauf si la valeur en contient (ou contient un \\)."""
    return f"'{value}'" if "'" not in value and "\\" not in value else json.dumps(value, ensure_ascii=False)


def _js_query(css: str, nth: int | None) -> str:
    return f".querySelector({js_string(css)})" if nth is None else f".querySelectorAll({js_string(css)})[{nth}]"


def robot_locator(element: Element) -> str:
    """Localisateur SeleniumLibrary : CSS quand c'est possible, sinon XPath.

    Dans un composant web (Shadow DOM), ni CSS ni XPath n'entrent : le localisateur dom:
    passe par l'hôte, document.querySelector('hôte').shadowRoot.querySelector('#champ').
    """
    if element.shadow:
        path = "document" + "".join(_js_query(hop["css"], hop["nth"]) + ".shadowRoot" for hop in element.shadow)
        return "dom:" + path + _js_query(element.selector, element.nth)
    if element.selector.startswith("xpath="):  # champ désigné par le libellé posé à côté
        return f"xpath:{element.xpath or element.selector[len('xpath='):]}"
    is_plain_css = ":has-text(" not in element.selector
    if is_plain_css and element.nth is None:
        return f"css:{element.selector}"
    if element.xpath:
        return f"xpath:{element.xpath}"
    return f"css:{element.selector}"  # dernier recours : premier élément correspondant


def frame_locator(hop: dict) -> str:
    """Localisateur d'un cadre (iframe) pour « Select Frame »."""
    if hop["nth"] is None:
        return f"css:{hop['css']}"
    return f"dom:document{_js_query(hop['css'], hop['nth'])}"


def dialog_keyword(dialog: dict) -> tuple[str, ...]:
    """Réponse à la fenêtre JavaScript ouverte par une action : même réponse que l'agent."""
    if dialog["accepted"] and dialog.get("text") is not None:  # fenêtre de saisie (prompt)
        return ("Input Text Into Alert", robot_arg(dialog["text"]), "action=ACCEPT")
    keyword = "Accepter la fenêtre" if dialog["accepted"] else "Refuser la fenêtre"
    return (keyword, robot_arg(" ".join(str(dialog["message"]).split())))


def value_keyword(element: Element, regex: bool) -> tuple[str, str | None]:
    """Mot-clé qui vérifie le contenu d'un élément, et le bloc de mots-clés à écrire dans le fichier s'il en faut un."""
    if regex:  # un format (expression régulière), pas une valeur exacte
        if element.reads_text():
            return "Le texte correspond au motif", _TEXT_PATTERN
        if element.tag == "select":
            return "L'option choisie correspond au motif", _LABEL_PATTERN
        return "La valeur correspond au motif", _VALUE_PATTERN
    if element.reads_text():  # zone de texte riche, liste « maison » : son texte affiché
        return "Le texte de l'élément est", _ELEMENT_TEXT
    return {"select": "List Selection Should Be", "textarea": "Textarea Value Should Be"}.get(
        element.tag, "Textfield Value Should Be"), None


def robot_filename(run: RunResult) -> str:
    return f"{run.case.slug}.robot"


def _keyword(*cells: str) -> str:
    return SEP + SEP.join(cells)


def generate_robot(run: RunResult) -> str:
    """Construit le test Robot Framework à partir des étapes réussies."""
    lines: list[str] = []
    keywords: list[str] = []  # mots-clés à écrire dans le fichier, selon les vérifications
    browser_open = False
    current: tuple = ()  # cadre sélectionné : localisateurs des iframes, depuis la page
    unique = getattr(run, "unique", None)  # valeur de {unique} pendant l'exécution de l'agent : ${UNIQUE} ici

    def open_browser(url: str) -> None:
        nonlocal browser_open
        lines.append(_keyword("Ouvrir le navigateur", secret_cell(url, {}, unique)))
        opening = open_block(run)
        if opening not in keywords:
            keywords.insert(0, opening)
        if run.closed_shadow:  # composants web fermés : ouverts avant d'agir, comme pendant l'exploration
            lines.append(_keyword("Ouvrir les composants fermés"))
            need(_CLOSED)
        if getattr(run, "ads_blocked", 0):  # pas de publicité plein écran, comme pendant l'exploration
            lines.append(_keyword("Bloquer les publicités"))
            need(_ADS)
        if run.case.session:  # déjà connecté : la session enregistrée par un autre test
            lines.append(_keyword("Reprendre la session", robot_arg(run.case.session)))
            need(_SESSION_LOAD)
        browser_open = True

    def enter(frame: list | None) -> None:
        """Sélectionne le cadre de l'élément, ou revient à la page : chaque mot-clé agit dans le cadre sélectionné."""
        nonlocal current
        target = tuple(frame_locator(hop) for hop in frame or [])
        if target == current:
            return
        if current:
            lines.append(_keyword("Unselect Frame"))
        lines.extend(_keyword("Select Frame", robot_arg(locator)) for locator in target)
        current = target

    def need(block: str | None) -> None:
        if block is not None and block not in keywords:
            keywords.append(block)
        if block in TEXT_BLOCKS and _VISIBLE not in keywords:
            keywords.append(_VISIBLE)

    names = env_names(run)  # repère du mot de passe -> variable du fichier .env
    for step in run.steps:
        action, result = step.action, step.result
        if action is None or not result.ok:
            continue  # on ne rejoue que ce qui a fonctionné
        args = action.args
        if action.name == "goto":
            if browser_open:
                lines.append(_keyword("Go To", secret_cell(args["url"], {}, unique)))
            else:
                open_browser(args["url"])
            current = ()  # une navigation revient toujours à la page
        else:
            if not browser_open:  # le navigateur doit être ouvert avant toute autre action
                open_browser(run.case.url)
            _step_lines(action, result, lines, enter, need, names, unique)
            if result.opened_tab or action.name == "switch_tab":
                current = ()  # un autre onglet : on part de sa page, sans cadre sélectionné
        if result.dialog is not None:  # fenêtre JavaScript ouverte par l'action
            answer = dialog_keyword(result.dialog)
            lines.append(_keyword(*answer))
            if answer[0] != "Input Text Into Alert":  # « Accepter la fenêtre » ou « Refuser la fenêtre »
                need(_ALERTS)
    if getattr(run, "session_saved", None) is not None and run.case.save_session:  # pour les tests déjà connectés
        enter(None)
        lines.append(_keyword("Enregistrer la session", robot_arg(run.case.save_session)))
        need(_SESSION_SAVE)

    body = "\n".join(lines) or _keyword("No Operation")
    variables = robot_variables(run) + (CLOSED_VARIABLE if run.closed_shadow else "") + \
        (ADS_VARIABLE if getattr(run, "ads_blocked", 0) else "")
    setup = ""
    if "%{" in body:  # des mots de passe, lus dans le fichier .env avant le test
        setup = SECRETS_SETUP
        keywords.append(_SECRETS)
    if "${UNIQUE}" in body:  # {unique} : une valeur nouvelle à chaque lancement
        setup += UNIQUE_SETUP
        keywords.append(_UNIQUE)
    code = _HEADER.format(title=robot_arg(run.case.title), setup=setup, variables=variables, name=robot_arg(run.case.title),
                          body=body)
    if keywords:
        code += "\n*** Keywords ***\n" + "\n".join(keywords)
    return code


def _step_lines(action, result, lines: list[str], enter, need, names: dict | None = None, unique: str | None = None) -> None:
    """Lignes du test pour une action réussie (autre que l'ouverture d'une page).

    unique : la valeur de {unique} pendant l'exécution de l'agent, qui devient ${UNIQUE} dans le test.
    """
    args = action.args
    element = result.element
    names = names or {}

    def cell(text: str) -> str:  # une valeur tapée ou vérifiée : mot de passe et valeur unique compris
        return secret_cell(text, names, unique)

    if element is not None:
        enter(element.frame)
        if element.shadow and action.name in ("click", "fill", "select", "hover", "press", "scroll", "upload", "drag"):
            # Selenium n'attend pas un élément désigné en JavaScript (dom:) : on attend qu'il apparaisse
            lines.append(_keyword("Wait Until Page Contains Element", robot_arg(robot_locator(element))))
    if action.name == "cookies":  # bandeau accepté par le programme pendant l'exploration
        enter(None)
        if args.get("cadre"):  # dans un cadre de consentement (iframe)
            lines.append(_keyword("Accepter le bandeau de cookies du cadre", robot_arg(frame_locator(args["cadre"][-1])),
                                  robot_arg(args["robot"])))
            need(_COOKIES_FRAME)
        else:
            lines.append(_keyword("Accepter le bandeau de cookies", robot_arg(args["robot"])))
        need(_COOKIES)
    elif action.name == "click":
        keyword = "Double Click Element" if args.get("double") else \
            "Open Context Menu" if args.get("button") == "right" else "Cliquer"
        lines.append(_keyword(keyword, robot_arg(robot_locator(element))))
        if keyword == "Cliquer":
            need(_CLICK)
        if result.opened_tab:  # le lien ouvre un nouvel onglet : le test y passe, comme l'agent
            lines.append(_keyword("Switch Window", "NEW"))
    elif action.name == "press":
        if element is None:
            enter(None)
        where = robot_arg(robot_locator(element)) if element is not None else "None"
        lines.append(_keyword("Press Keys", where, robot_arg(selenium_keys(args["key"]))))
        if result.opened_tab:
            lines.append(_keyword("Switch Window", "NEW"))
    elif action.name == "hover":
        lines.append(_keyword("Mouse Over", robot_arg(robot_locator(element))))
    elif action.name == "scroll":
        if element is not None:
            lines.append(_keyword("Scroll Element Into View", robot_arg(robot_locator(element))))
        else:
            enter(None)
            script = "window.scrollTo(0, document.body.scrollHeight)" if args["to"] == "bottom" else "window.scrollTo(0, 0)"
            lines.append(_keyword("Execute Javascript", script))
    elif action.name == "upload":  # le fichier est copié à côté du test
        lines.append(_keyword("Choose File", robot_arg(robot_locator(element)), "${CURDIR}${/}" + robot_arg(args["file"])))
    elif action.name == "drag":
        source, target = robot_arg(robot_locator(element)), robot_arg(robot_locator(result.target))
        if element.draggable():  # glisser-déposer HTML5 : par les événements du navigateur
            lines.append(_keyword("Glisser-déposer", source, target))
            need(_DRAG)
        else:
            lines.append(_keyword("Drag And Drop", source, target))
    elif action.name == "switch_tab":
        lines.append(_keyword("@{onglets}=", "Get Window Handles"))
        lines.append(_keyword("Switch Window", f"${{onglets}}[{args['index'] - 1}]"))
    elif action.name == "fill":
        if element.editable():  # Chrome ne tape dans une zone de texte riche qu'après un vrai clic
            lines.append(_keyword("Click Element", robot_arg(robot_locator(element))))
        typed = cell(args["text"])
        lines.append(_keyword(typing_keyword(element, "%{" in typed), robot_arg(robot_locator(element)), typed))
    elif action.name == "select":
        locator = robot_arg(robot_locator(element))
        if result.value is not None:
            lines.append(_keyword("Select From List By Value", locator, cell(result.value)))
        else:
            lines.append(_keyword("Select From List By Label", locator, cell(args["value"])))
    elif action.name == "wait":
        if "text" not in args:
            lines.append(_keyword("Sleep", f"{args['seconds']}s"))
        else:  # le texte peut être dans un cadre : on attend là où l'agent l'a trouvé
            enter(result.frame)
            lines.append(_keyword("Wait Until Keyword Succeeds", f"{args['seconds']}s", "500ms", "Le texte visible contient",
                                  cell(result.value or args["text"])))
            need(_SHOWS)
    elif action.name == "check_text":
        if args.get("absent"):
            enter(None)
            lines.append(_keyword("La page n'affiche pas", cell(args["text"])))
            for frame in getattr(result, "frames_checked", None) or []:  # ni dans la page, ni dans ses cadres
                enter(frame)
                lines.append(_keyword("La page n'affiche pas", cell(args["text"])))
            enter(None)
            need(_HIDES)
        else:
            enter(result.frame)
            # Texte tel qu'il apparaît sur la page (casse exacte), plus lisible dans le test
            lines.append(_keyword("La page affiche", cell(result.value or args["text"])))
            need(_SHOWS)
    elif action.name == "check_url":
        keyword = "Wait Until Location Does Not Contain" if args.get("absent") else "Wait Until Location Contains"
        lines.append(_keyword(keyword, cell(args["contains"])))
    elif action.name == "check_order":
        enter(None)
        lines.append(_keyword("La page affiche dans l'ordre", *(cell(text) for text in args["texts"])))
        need(_ORDER)
    elif action.name == "check_count":
        enter(None)
        lines.append(_keyword("La page affiche ce nombre de fois", cell(args["text"]), str(args["count"])))
        need(_COUNT)
    elif action.name in ("check_value", "check_state"):
        if action.name == "check_value":
            regex = bool(args.get("regex"))
            # Vérification réussie d'une zone de texte : on reprend son texte réel, espaces regroupés
            value = result.value if element.reads_text() and result.check and result.value is not None and not regex \
                else args["value"]
            keyword, block = value_keyword(element, regex)
            checking = (keyword, robot_arg(robot_locator(element)), value_cell(value, names) if regex else cell(value))
            need(block)
        else:
            keyword, *extra = _state_keyword(element, args["state"])
            checking = (keyword, robot_arg(robot_locator(element)), *extra)
        # Comme l'agent, le test laisse à la page le temps d'atteindre l'état attendu
        lines.append(_keyword("Wait Until Keyword Succeeds", "${DELAI}", "200ms", *checking))
    elif action.name == "check_visual":  # avis du modèle sur une capture : rien à rejouer sans IA
        lines.append(SEP + "# À automatiser : vérification visuelle faite par l'IA : " + " ".join(args["expected"].split()))


def write_robot(run: RunResult) -> None:
    """Écrit le test Robot Framework dans le dossier du rapport."""
    path = run.out_dir / robot_filename(run)
    path.write_text(generate_robot(run), encoding="utf-8")
    run.robot_path = path
