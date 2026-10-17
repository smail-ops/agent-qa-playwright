"""Génération d'un test Robot Framework (SeleniumLibrary) à partir d'une exécution.

Même principe que le script pytest : l'agent explore le site une fois avec
Playwright et l'IA, puis le test généré rejoue les actions réussies avec
Selenium, sans IA. Robot Framework et SeleniumLibrary sont très utilisés dans
les équipes de test, souvent avec un outil de gestion des tests comme Squash TM.

Exemple de test produit :

    *** Test Cases ***
    CT-01 Connexion avec un utilisateur valide
        Open Browser    https://www.saucedemo.com/    ${BROWSER}
        Set Window Size    1280    800
        Input Text    css:#user-name    standard_user
        Click Element    css:[data-test="login-button"]
        Wait Until Location Contains    inventory
        La page affiche    Products

Les vérifications de texte passent par deux mots-clés écrits dans le fichier,
« La page affiche » et « La page n'affiche pas ». Comme l'agent, ils lisent le
texte visible de la page (innerText). « Wait Until Page Contains » de
SeleniumLibrary compterait aussi le texte caché, par exemple un message
d'erreur présent dans la page mais masqué : le test passerait à tort.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .browser import Element
    from .runner import RunResult

SEP = "    "  # Robot Framework sépare les arguments par au moins deux espaces
WINDOW_SIZE = ("1280", "800")  # même taille de fenêtre que l'agent

_HEADER = """*** Settings ***
Documentation     Test généré automatiquement par agent-qa-playwright.
...               Cas de test : {title}
...               Rejoue les actions réussies de l'agent avec Selenium, sans IA.
# implicit_wait : avant d'agir, Selenium attend que l'élément apparaisse, comme Playwright.
Library           SeleniumLibrary    timeout=${{DELAI}}    implicit_wait=${{DELAI}}
Test Teardown     Close Browser

*** Variables ***
${{BROWSER}}        chrome
# Attente maximale d'un élément, d'un texte ou d'une URL.
${{DELAI}}          5s

*** Test Cases ***
{name}
{body}
"""

_SHOWS = """La page affiche
    [Documentation]    Attend que le texte soit affiché. La casse et les espaces ne comptent pas, comme pour l'agent.
    [Arguments]    ${texte}
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Le texte visible contient    ${texte}

Le texte visible contient
    [Documentation]    innerText ne renvoie que le texte affiché à l'écran : un message caché ne compte pas.
    [Arguments]    ${texte}
    ${visible}=    Execute Javascript    return document.body.innerText
    Should Contain    ${visible}    ${texte}    msg=Le texte « ${texte} » n'est pas affiché.    values=False
    ...    ignore_case=True    strip_spaces=True    collapse_spaces=True
"""

_HIDES = """La page n'affiche pas
    [Documentation]    Attend que le texte ne soit plus affiché. Un texte caché compte comme absent.
    [Arguments]    ${texte}
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Le texte visible ne contient pas    ${texte}

Le texte visible ne contient pas
    [Arguments]    ${texte}
    ${visible}=    Execute Javascript    return document.body.innerText
    Should Not Contain    ${visible}    ${texte}    msg=Le texte « ${texte} » est affiché alors qu'il devrait être absent.
    ...    values=False    ignore_case=True    strip_spaces=True    collapse_spaces=True
"""


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


def robot_locator(element: Element) -> str:
    """Localisateur SeleniumLibrary : CSS quand c'est possible, sinon XPath."""
    is_plain_css = ":has-text(" not in element.selector
    if is_plain_css and element.nth is None:
        return f"css:{element.selector}"
    if element.xpath:
        return f"xpath:{element.xpath}"
    return f"css:{element.selector}"  # dernier recours : premier élément correspondant


def robot_filename(run: RunResult) -> str:
    return f"{run.case.slug}.robot"


def _keyword(*cells: str) -> str:
    return SEP + SEP.join(cells)


def generate_robot(run: RunResult) -> str:
    """Construit le test Robot Framework à partir des étapes réussies."""
    lines: list[str] = []
    keywords: list[str] = []  # mots-clés à écrire dans le fichier, selon les vérifications
    browser_open = False

    def open_browser(url: str) -> None:
        nonlocal browser_open
        lines.append(_keyword("Open Browser", robot_arg(url), "${BROWSER}"))
        lines.append(_keyword("Set Window Size", *WINDOW_SIZE))
        browser_open = True

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
            continue

        if not browser_open:  # le navigateur doit être ouvert avant toute autre action
            open_browser(run.case.url)

        if action.name == "click":
            lines.append(_keyword("Click Element", robot_arg(robot_locator(result.element))))
        elif action.name == "fill":
            lines.append(_keyword("Input Text", robot_arg(robot_locator(result.element)), robot_arg(args["text"])))
        elif action.name == "select":
            locator = robot_arg(robot_locator(result.element))
            if result.value is not None:
                lines.append(_keyword("Select From List By Value", locator, robot_arg(result.value)))
            else:
                lines.append(_keyword("Select From List By Label", locator, robot_arg(args["value"])))
        elif action.name == "check_text":
            if args.get("absent"):
                lines.append(_keyword("La page n'affiche pas", robot_arg(args["text"])))
                block = _HIDES
            else:
                # Texte tel qu'il apparaît sur la page (casse exacte), plus lisible dans le test
                lines.append(_keyword("La page affiche", robot_arg(result.value or args["text"])))
                block = _SHOWS
            if block not in keywords:
                keywords.append(block)
        elif action.name == "check_url":
            keyword = "Wait Until Location Does Not Contain" if args.get("absent") else "Wait Until Location Contains"
            lines.append(_keyword(keyword, robot_arg(args["contains"])))

    body = "\n".join(lines) or _keyword("No Operation")
    code = _HEADER.format(title=robot_arg(run.case.title), name=robot_arg(run.case.title), body=body)
    if keywords:
        code += "\n*** Keywords ***\n" + "\n".join(keywords)
    return code


def write_robot(run: RunResult) -> None:
    """Écrit le test Robot Framework dans le dossier du rapport."""
    path = run.out_dir / robot_filename(run)
    path.write_text(generate_robot(run), encoding="utf-8")
    run.robot_path = path
