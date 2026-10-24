*** Settings ***
Documentation     Test généré automatiquement par agent-qa-playwright.
...               Cas de test : CT-00 Démo locale : connexion et panier
...               Rejoue les actions réussies de l'agent avec Selenium, sans IA.
# implicit_wait : avant d'agir, Selenium attend que l'élément apparaisse, comme Playwright.
Library           SeleniumLibrary    timeout=${DELAI}    implicit_wait=${DELAI}
Suite Setup       Charger les mots de passe
Test Teardown     Close Browser

*** Variables ***
${BROWSER}        chrome
# Attente maximale d'un élément, d'un texte ou d'une URL.
${DELAI}          5s
# Préférences de Chrome, utilisées par le mot-clé « Ouvrir le navigateur ».
${OPTIONS_CHROME}    add_experimental_option("prefs", {"credentials_enable_service": False, "profile.password_manager_enabled": False, "profile.password_manager_leak_detection": False})

*** Test Cases ***
CT-00 Démo locale : connexion et panier
    Ouvrir le navigateur    http://localhost:8000/index.html
    Input Text    css:#user-name    standard_user
    Input Password    css:#password    %{MOT_DE_PASSE_LOCALHOST}
    Click Element    css:[data-test="login-button"]
    Wait Until Location Contains    inventory
    La page affiche    Produits
    Click Element    xpath:(//button[contains(normalize-space(.), "Ajouter au panier")])[1]
    La page affiche    Panier (1)

*** Keywords ***
Ouvrir le navigateur
    [Documentation]    Ouvre le navigateur sur la page, à la taille de la fenêtre de l'agent.
    ...    Avec Chrome, le gestionnaire de mots de passe est coupé (variable OPTIONS_CHROME) : après une connexion
    ...    avec un mot de passe connu des fuites de données, comme celui du site d'entraînement SauceDemo, Chrome
    ...    afficherait une alerte « mot de passe compromis » qui interrompt le test.
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
    [Arguments]    ${texte}
    ${visible}=    Texte visible de la page
    Should Contain    ${visible}    ${texte}    msg=Le texte « ${texte} » n'est pas affiché.    values=False
    ...    ignore_case=True    strip_spaces=True    collapse_spaces=True

Texte visible de la page
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

Charger les mots de passe
    [Documentation]    Lit le fichier .env du projet : l'agent y a rangé les mots de passe de test (%{MOT_DE_PASSE_...}),
    ...    qui ne sont jamais écrits dans ce fichier.
    Evaluate    dotenv.load_dotenv(dotenv.find_dotenv(usecwd=True))    modules=dotenv
