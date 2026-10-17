*** Settings ***
Documentation     Test généré automatiquement par agent-qa-playwright.
...               Cas de test : CT-00 Démo locale : connexion et panier
...               Rejoue les actions réussies de l'agent avec Selenium, sans IA.
# implicit_wait : avant d'agir, Selenium attend que l'élément apparaisse, comme Playwright.
Library           SeleniumLibrary    timeout=${DELAI}    implicit_wait=${DELAI}
Test Teardown     Close Browser

*** Variables ***
${BROWSER}        chrome
# Attente maximale d'un élément, d'un texte ou d'une URL.
${DELAI}          5s

*** Test Cases ***
CT-00 Démo locale : connexion et panier
    Open Browser    http://localhost:8000/index.html    ${BROWSER}
    Set Window Size    1280    800
    Input Text    css:#user-name    standard_user
    Input Text    css:#password    secret_sauce
    Click Element    css:[data-test="login-button"]
    Wait Until Location Contains    inventory
    La page affiche    Produits
    Click Element    xpath:(//button[contains(normalize-space(.), "Ajouter au panier")])[1]
    La page affiche    Panier (1)

*** Keywords ***
La page affiche
    [Documentation]    Attend que le texte soit affiché. La casse et les espaces ne comptent pas, comme pour l'agent.
    [Arguments]    ${texte}
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Le texte visible contient    ${texte}

Le texte visible contient
    [Documentation]    innerText ne renvoie que le texte affiché à l'écran : un message caché ne compte pas.
    [Arguments]    ${texte}
    ${visible}=    Execute Javascript    return document.body.innerText
    Should Contain    ${visible}    ${texte}    msg=Le texte « ${texte} » n'est pas affiché.    values=False
    ...    ignore_case=True    strip_spaces=True    collapse_spaces=True
