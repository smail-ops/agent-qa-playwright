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
        Click Element    css:[data-test="login-button"]
        Wait Until Location Contains    inventory
        La page affiche    Products

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
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

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

_HEADER = """*** Settings ***
Documentation     Test généré automatiquement par agent-qa-playwright.
...               Cas de test : {title}
...               Rejoue les actions réussies de l'agent avec Selenium, sans IA.
# implicit_wait : avant d'agir, Selenium attend que l'élément apparaisse, comme Playwright.
Library           SeleniumLibrary    timeout=${{DELAI}}    implicit_wait=${{DELAI}}
Test Teardown     Close Browser

{variables}
*** Test Cases ***
{name}
{body}
"""

_OPEN = f"""Ouvrir le navigateur
    [Documentation]    Ouvre le navigateur sur la page, à la taille de la fenêtre de l'agent.
    ...    Avec Chrome, le gestionnaire de mots de passe est coupé (variable OPTIONS_CHROME) : après une connexion
    ...    avec un mot de passe connu des fuites de données, comme secret_sauce, Chrome afficherait une alerte
    ...    « mot de passe compromis » qui interrompt le test.
    [Arguments]    ${{url}}
    ${{chrome}}=    Evaluate    $BROWSER.lower().replace(" ", "") in {CHROME_NAMES!r}
    IF    ${{chrome}}
        Open Browser    ${{url}}    ${{BROWSER}}    options=${{OPTIONS_CHROME}}
    ELSE
        Open Browser    ${{url}}    ${{BROWSER}}
    END
    Set Window Size    {WINDOW_SIZE[0]}    {WINDOW_SIZE[1]}
"""

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

    def open_browser(url: str) -> None:
        nonlocal browser_open
        lines.append(_keyword("Ouvrir le navigateur", robot_arg(url)))
        if _OPEN not in keywords:
            keywords.insert(0, _OPEN)
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

    for step in run.steps:
        action, result = step.action, step.result
        if action is None or not result.ok:
            continue  # on ne rejoue que ce qui a fonctionné
        args = action.args
        if action.name == "goto":
            if browser_open:
                lines.append(_keyword("Go To", robot_arg(args["url"])))
            else:
                open_browser(args["url"])
            current = ()  # une navigation revient toujours à la page
        else:
            if not browser_open:  # le navigateur doit être ouvert avant toute autre action
                open_browser(run.case.url)
            _step_lines(action, result, lines, enter, need)
        if result.dialog is not None:  # fenêtre JavaScript ouverte par l'action
            answer = dialog_keyword(result.dialog)
            lines.append(_keyword(*answer))
            if answer[0] != "Input Text Into Alert":  # « Accepter la fenêtre » ou « Refuser la fenêtre »
                need(_ALERTS)

    body = "\n".join(lines) or _keyword("No Operation")
    code = _HEADER.format(title=robot_arg(run.case.title), variables=VARIABLES, name=robot_arg(run.case.title), body=body)
    if keywords:
        code += "\n*** Keywords ***\n" + "\n".join(keywords)
    return code


def _step_lines(action, result, lines: list[str], enter, need) -> None:
    """Lignes du test pour une action réussie (autre que l'ouverture d'une page)."""
    args = action.args
    element = result.element
    if element is not None:
        enter(element.frame)
        if element.shadow and action.name in ("click", "fill", "select"):
            # Selenium n'attend pas un élément désigné en JavaScript (dom:) : on attend qu'il apparaisse
            lines.append(_keyword("Wait Until Page Contains Element", robot_arg(robot_locator(element))))
    if action.name == "click":
        lines.append(_keyword("Click Element", robot_arg(robot_locator(element))))
    elif action.name == "fill":
        if element.editable():  # Chrome ne tape dans une zone de texte riche qu'après un vrai clic
            lines.append(_keyword("Click Element", robot_arg(robot_locator(element))))
        lines.append(_keyword("Input Text", robot_arg(robot_locator(element)), robot_arg(args["text"])))
    elif action.name == "select":
        locator = robot_arg(robot_locator(element))
        if result.value is not None:
            lines.append(_keyword("Select From List By Value", locator, robot_arg(result.value)))
        else:
            lines.append(_keyword("Select From List By Label", locator, robot_arg(args["value"])))
    elif action.name == "wait":
        if "text" not in args:
            lines.append(_keyword("Sleep", f"{args['seconds']}s"))
        else:  # le texte peut être dans un cadre : on attend là où l'agent l'a trouvé
            enter(result.frame)
            lines.append(_keyword("Wait Until Keyword Succeeds", f"{args['seconds']}s", "500ms", "Le texte visible contient",
                                  robot_arg(result.value or args["text"])))
            need(_SHOWS)
    elif action.name == "check_text":
        if args.get("absent"):
            enter(None)
            lines.append(_keyword("La page n'affiche pas", robot_arg(args["text"])))
            need(_HIDES)
        else:
            enter(result.frame)
            # Texte tel qu'il apparaît sur la page (casse exacte), plus lisible dans le test
            lines.append(_keyword("La page affiche", robot_arg(result.value or args["text"])))
            need(_SHOWS)
    elif action.name == "check_url":
        keyword = "Wait Until Location Does Not Contain" if args.get("absent") else "Wait Until Location Contains"
        lines.append(_keyword(keyword, robot_arg(args["contains"])))
    elif action.name == "check_order":
        enter(None)
        lines.append(_keyword("La page affiche dans l'ordre", *(robot_arg(text) for text in args["texts"])))
        need(_ORDER)
    elif action.name == "check_count":
        enter(None)
        lines.append(_keyword("La page affiche ce nombre de fois", robot_arg(args["text"]), str(args["count"])))
        need(_COUNT)
    elif action.name in ("check_value", "check_state"):
        if action.name == "check_value":
            regex = bool(args.get("regex"))
            # Vérification réussie d'une zone de texte : on reprend son texte réel, espaces regroupés
            value = result.value if element.reads_text() and result.check and result.value is not None and not regex \
                else args["value"]
            keyword, block = value_keyword(element, regex)
            checking = (keyword, robot_arg(robot_locator(element)), robot_arg(value))
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
