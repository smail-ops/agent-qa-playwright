language: fr

*** Settings ***
Documentation     Scénario Gherkin généré automatiquement par agent-qa-playwright.
...               Cas de test : CT-00 Démo locale : connexion et panier
...               Même scénario que ct_00_demo_locale_connexion_et_panier.feature : Robot Framework comprend
...               Étant donné que, Quand, Alors et Et. Chaque phrase est un mot-clé, plus bas.
Library           SeleniumLibrary    timeout=${DELAI}    implicit_wait=${DELAI}
Test Teardown     Close Browser

*** Variables ***
${BROWSER}        chrome
# Attente maximale d'un élément, d'un texte ou d'une URL.
${DELAI}          5s
# Préférences de Chrome, utilisées par le mot-clé « Ouvrir le navigateur ».
${OPTIONS_CHROME}    add_experimental_option("prefs", {"credentials_enable_service": False, "profile.password_manager_enabled": False, "profile.password_manager_leak_detection": False})

*** Test Cases ***
CT-00 Démo locale : connexion et panier
    [Tags]    CT-00
    Étant donné que je suis sur la page "http://localhost:8000/index.html"
    Quand je saisis "standard_user" dans le champ "Identifiant"
    Et je saisis "secret_sauce" dans le champ "Mot de passe"
    Et je clique sur le bouton "Connexion"
    Alors l'URL contient "inventory"
    Et la page affiche "Produits"
    Quand je clique sur le bouton "Ajouter au panier" n°1
    Alors la page affiche "Panier (1)"

*** Keywords ***
je suis sur la page "${url}"
    Ouvrir le navigateur    ${url}

je saisis "${texte}" dans le champ "Identifiant"
    Input Text    css:#user-name    ${texte}

je saisis "${texte}" dans le champ "Mot de passe"
    Input Text    css:#password    ${texte}

je clique sur le bouton "Connexion"
    Click Element    css:[data-test="login-button"]

l'URL contient "${texte}"
    Wait Until Location Contains    ${texte}

la page affiche "${texte}"
    La page affiche    ${texte}

je clique sur le bouton "Ajouter au panier" n°1
    Click Element    xpath:(//button[contains(normalize-space(.), "Ajouter au panier")])[1]

Ouvrir le navigateur
    [Documentation]    Ouvre le navigateur sur la page, à la taille de la fenêtre de l'agent.
    ...    Avec Chrome, le gestionnaire de mots de passe est coupé (variable OPTIONS_CHROME) : après une connexion
    ...    avec un mot de passe connu des fuites de données, comme secret_sauce, Chrome afficherait une alerte
    ...    « mot de passe compromis » qui interrompt le test.
    [Arguments]    ${url}
    ${chrome}=    Evaluate    $BROWSER.lower().replace(" ", "") in ('chrome', 'googlechrome', 'gc', 'headlesschrome')
    IF    ${chrome}
        Open Browser    ${url}    ${BROWSER}    options=${OPTIONS_CHROME}
    ELSE
        Open Browser    ${url}    ${BROWSER}
    END
    Set Window Size    1280    800

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
