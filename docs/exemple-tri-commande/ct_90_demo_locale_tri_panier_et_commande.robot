*** Settings ***
Documentation     Test généré automatiquement par agent-qa-playwright.
...               Cas de test : CT-90 Démo locale : tri, panier et commande
...               Rejoue les actions réussies de l'agent avec Selenium, sans IA.
# implicit_wait : avant d'agir, Selenium attend que l'élément apparaisse, comme Playwright.
Library           SeleniumLibrary    timeout=${DELAI}    implicit_wait=${DELAI}
Test Teardown     Close Browser

*** Variables ***
${BROWSER}        chrome
# Attente maximale d'un élément, d'un texte ou d'une URL.
${DELAI}          5s

*** Test Cases ***
CT-90 Démo locale : tri, panier et commande
    Open Browser    http://localhost:8000/index.html    ${BROWSER}
    Set Window Size    1280    800
    Input Text    css:#user-name    standard_user
    Input Text    css:#password    secret_sauce
    Click Element    css:[data-test="login-button"]
    La page affiche ce nombre de fois    Ajouter au panier    3
    Select From List By Value    css:[data-test="sort"]    price
    La page affiche dans l'ordre    Lampe de vélo    T-shirt    Sac à dos
    Click Element    xpath:(//button[contains(normalize-space(.), "Ajouter au panier")])[1]
    La page affiche ce nombre de fois    Retirer    1
    Click Element    css:[data-test="checkout"]
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Element Should Be Disabled    css:[data-test="continuer"]
    Click Element    css:[data-test="cgu"]
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Checkbox Should Be Selected    css:[data-test="cgu"]
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Element Should Be Enabled    css:[data-test="continuer"]
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Textfield Value Should Be    css:#code-postal    ${EMPTY}

*** Keywords ***
La page affiche ce nombre de fois
    [Documentation]    Attend que le texte soit affiché exactement ce nombre de fois.
    ...    La casse et les espaces ne comptent pas.
    [Arguments]    ${texte}    ${nombre}
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Le texte visible apparaît    ${texte}    ${nombre}

Le texte visible apparaît
    [Arguments]    ${texte}    ${nombre}
    ${visible}=    Execute Javascript    return document.body.innerText
    ${trouve}=    Evaluate    " ".join($visible.split()).lower().count(" ".join($texte.split()).lower())
    Should Be Equal As Integers    ${trouve}    ${nombre}    msg=« ${texte} » est affiché ${trouve} fois au lieu de ${nombre}.    values=False

La page affiche dans l'ordre
    [Documentation]    Attend que les textes soient affichés dans cet ordre, par exemple après un tri.
    ...    La casse et les espaces ne comptent pas.
    [Arguments]    @{textes}
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Les textes visibles se suivent    @{textes}

Les textes visibles se suivent
    [Arguments]    @{textes}
    ${visible}=    Execute Javascript    return document.body.innerText
    ${visible}=    Evaluate    " ".join($visible.split()).lower()
    ${debut}=    Set Variable    ${0}
    FOR    ${texte}    IN    @{textes}
        ${cherche}=    Evaluate    " ".join($texte.split()).lower()
        ${position}=    Evaluate    $visible.find($cherche, $debut)
        Should Be True    $position >= 0    msg=Ordre non respecté : « ${texte} » n'est pas affiché à la place attendue.
        ${debut}=    Evaluate    $position + len($cherche)
    END
