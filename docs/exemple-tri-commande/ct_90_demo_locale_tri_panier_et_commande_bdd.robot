language: fr

*** Settings ***
Documentation     Scénario Gherkin généré automatiquement par agent-qa-playwright.
...               Cas de test : CT-90 Démo locale : tri, panier et commande
...               Même scénario que ct_90_demo_locale_tri_panier_et_commande.feature : Robot Framework comprend
...               Étant donné que, Quand, Alors et Et. Chaque phrase est un mot-clé, plus bas.
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
CT-90 Démo locale : tri, panier et commande
    [Tags]    CT-90
    Étant donné que je suis sur la page "http://localhost:8000/index.html"
    Quand je saisis "standard_user" dans le champ "Identifiant"
    Et je saisis le mot de passe "MOT_DE_PASSE_LOCALHOST" dans le champ "Mot de passe"
    Et je clique sur le bouton "Connexion"
    Alors la page affiche 3 fois "Ajouter au panier"
    Quand je choisis "Prix croissant" dans la liste "Trier"
    Alors la page affiche dans cet ordre : "Lampe de vélo", "T-shirt" et "Sac à dos"
    Quand je clique sur le bouton "Ajouter au panier" n°1
    Alors la page affiche 1 fois "Retirer"
    Quand je clique sur le lien "Commander"
    Alors le bouton "Continuer" est grisé
    Quand je clique sur la case "J'accepte les conditions générales de vente"
    Alors la case "J'accepte les conditions générales de vente" est cochée
    Et le bouton "Continuer" est actif
    Et le champ "Code postal" est vide

*** Keywords ***
je suis sur la page "${url}"
    Ouvrir le navigateur    ${url}

je saisis "${texte}" dans le champ "Identifiant"
    Input Text    css:#user-name    ${texte}

je saisis le mot de passe "${variable}" dans le champ "Mot de passe"
    Input Password    css:#password    %{${variable}}

je clique sur le bouton "Connexion"
    Cliquer    css:[data-test="login-button"]

la page affiche ${nombre:\d+} fois "${texte}"
    La page affiche ce nombre de fois    ${texte}    ${nombre}

je choisis "${option}" dans la liste "Trier"
    ${par_libelle}=    Run Keyword And Return Status    Select From List By Label    css:[data-test="sort"]    ${option}
    IF    not ${par_libelle}    Select From List By Value    css:[data-test="sort"]    ${option}

la page affiche dans cet ordre : ${textes}
    ${liste}=    Evaluate    re.findall(r'"([^"]*)"', $textes)    modules=re
    La page affiche dans l'ordre    @{liste}

je clique sur le bouton "Ajouter au panier" n°1
    Cliquer    xpath:(//button[contains(normalize-space(.), "Ajouter au panier")])[1]

je clique sur le lien "Commander"
    Cliquer    css:[data-test="checkout"]

le bouton "Continuer" est grisé
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Element Should Be Disabled    css:[data-test="continuer"]

je clique sur la case "J'accepte les conditions générales de vente"
    Cliquer    css:[data-test="cgu"]

la case "J'accepte les conditions générales de vente" est cochée
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Checkbox Should Be Selected    css:[data-test="cgu"]

le bouton "Continuer" est actif
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Element Should Be Enabled    css:[data-test="continuer"]

le champ "Code postal" est vide
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Textfield Value Should Be    css:#code-postal    ${EMPTY}

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

Cliquer
    [Documentation]    Clique sur l'élément, comme l'agent. Si la page le remplace au moment du clic (une liste
    ...    rechargée par l'API, une application monopage), Selenium lève StaleElementReferenceException :
    ...    le clic est retenté, avec l'élément cherché de nouveau.
    [Arguments]    ${localisateur}
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Click Element    ${localisateur}

La page affiche ce nombre de fois
    [Documentation]    Attend que le texte soit affiché exactement ce nombre de fois.
    ...    La casse et les espaces ne comptent pas.
    [Arguments]    ${texte}    ${nombre}
    Wait Until Keyword Succeeds    ${DELAI}    200ms    Le texte visible apparaît    ${texte}    ${nombre}

Le texte visible apparaît
    [Arguments]    ${texte}    ${nombre}
    ${visible}=    Texte visible de la page
    ${trouve}=    Evaluate    " ".join($visible.split()).lower().count(" ".join($texte.split()).lower())
    Should Be Equal As Integers    ${trouve}    ${nombre}    msg=« ${texte} » est affiché ${trouve} fois au lieu de ${nombre}.    values=False

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

La page affiche dans l'ordre
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

Charger les mots de passe
    [Documentation]    Lit le fichier .env du projet : l'agent y a rangé les mots de passe de test (%{MOT_DE_PASSE_...}),
    ...    qui ne sont jamais écrits dans ce fichier.
    Evaluate    dotenv.load_dotenv(dotenv.find_dotenv(usecwd=True))    modules=dotenv
