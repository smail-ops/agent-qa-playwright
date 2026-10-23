language: fr

*** Settings ***
Documentation     Scénario Gherkin généré automatiquement par agent-qa-playwright.
...               Cas de test : CT-91 Démo locale : iframe, Shadow DOM, liste maison et alerte
...               Même scénario que ct_91_demo_locale_iframe_shadow_dom_liste_maison_et_alerte.feature : Robot Framework comprend
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
CT-91 Démo locale : iframe, Shadow DOM, liste maison et alerte
    [Tags]    CT-91
    Étant donné que je suis sur la page "http://localhost:8000/atelier.html"
    Quand je saisis "Livrer avant midi" dans la zone de texte "Note de commande"
    Alors la zone de texte "Note de commande" contient "Livrer avant midi"
    Quand je clique sur le bouton "Générer un code"
    Alors le champ "Code promo" correspond au motif "^PROMO-[0-9A-F]{4}-[0-9A-F]{4}$"
    Et la page affiche "Code prêt à copier"
    Quand je clique sur le champ "Rôle"
    Et je clique sur l'option "ESS"
    Alors le champ "Rôle" contient "ESS"
    Et la page affiche "Rôle : ESS"
    Quand je clique sur le bouton "Envoyer la commande"
    Et j'accepte la fenêtre "Confirmer l'envoi de la commande ?"
    Alors la page affiche "Commande envoyée"
    Quand je clique sur le bouton "Charger le stock"
    Et j'attends jusqu'à 10 secondes que la page affiche "Stock chargé"
    Alors la page affiche "Stock chargé : 12 articles"

*** Keywords ***
je suis sur la page "${url}"
    Ouvrir le navigateur    ${url}

je saisis "${texte}" dans la zone de texte "Note de commande"
    Select Frame    css:#editeur
    Click Element    css:#note
    Input Text    css:#note    ${texte}
    Unselect Frame

la zone de texte "Note de commande" contient "${valeur}"
    Select Frame    css:#editeur
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Le texte de l'élément est    css:#note    ${valeur}
    Unselect Frame

je clique sur le bouton "Générer un code"
    Wait Until Page Contains Element    dom:document.querySelector('generateur-code').shadowRoot.querySelector('#generer')
    Click Element    dom:document.querySelector('generateur-code').shadowRoot.querySelector('#generer')

le champ "Code promo" correspond au motif "${motif}"
    Wait Until Keyword Succeeds    ${DELAI}    200ms    La valeur correspond au motif    dom:document.querySelector('generateur-code').shadowRoot.querySelector('#code')    ${motif}

la page affiche "${texte}"
    La page affiche    ${texte}

je clique sur le champ "Rôle"
    Click Element    xpath://label[normalize-space(.)="Rôle"]/following::div[@tabindex][1]

je clique sur l'option "ESS"
    Click Element    xpath://div[@role="option"][contains(normalize-space(.), "ESS")]

le champ "Rôle" contient "${valeur}"
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Le texte de l'élément est    xpath://label[normalize-space(.)="Rôle"]/following::div[@tabindex][1]    ${valeur}

je clique sur le bouton "Envoyer la commande"
    Click Element    css:[data-test="envoyer"]

j'accepte la fenêtre "${message}"
    Accepter la fenêtre    ${message}

je clique sur le bouton "Charger le stock"
    Click Element    css:[data-test="charger"]

j'attends jusqu'à ${secondes:\d+} secondes que la page affiche "${texte}"
    Wait Until Keyword Succeeds    ${secondes}s    500ms    Le texte visible contient    ${texte}

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

Le texte de l'élément est
    [Documentation]    Texte affiché dans l'élément, une zone de texte riche par exemple. Les espaces ne comptent pas.
    [Arguments]    ${localisateur}    ${attendu}
    ${texte}=    Get Text    ${localisateur}
    Should Be Equal    ${texte}    ${attendu}    msg=L'élément affiche « ${texte} » au lieu de « ${attendu} ».    values=False
    ...    strip_spaces=True    collapse_spaces=True

La valeur correspond au motif
    [Documentation]    La valeur du champ suit le motif (expression régulière) : un code généré, une date...
    [Arguments]    ${localisateur}    ${motif}
    ${valeur}=    Get Value    ${localisateur}
    Should Match Regexp    ${valeur}    ${motif}    msg=« ${valeur} » ne correspond pas au motif « ${motif} ».    values=False

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

Accepter la fenêtre
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
