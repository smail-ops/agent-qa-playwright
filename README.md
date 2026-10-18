# Agent QA Playwright

![Tests](https://github.com/smail-ops/agent-qa-playwright/actions/workflows/tests.yml/badge.svg)

Un agent IA qui conçoit, exécute et analyse des tests web, comme le ferait un testeur.

On lui donne l'adresse d'un site, ou un cas de test écrit en français comme dans Squash TM. Il pilote un vrai navigateur avec Playwright, vérifie chaque résultat attendu, puis produit des tests automatisés qui se rejouent ensuite **sans IA** : gratuits, rapides et toujours identiques.

| | Ce que fait l'agent |
|---|---|
| **Concevoir** | À partir d'une URL, il analyse le site et propose des **user stories** avec leurs critères d'acceptation en **Gherkin**, puis les **cas de test** d'une user story. |
| **Exécuter** | Il exécute un cas de test, en Markdown ou en Gherkin, avec six familles de vérifications (texte, URL, ordre, nombre, valeur d'un champ, état d'un bouton ou d'une case). Avec la **vision**, le modèle voit aussi l'écran. |
| **Industrialiser** | Chaque exécution génère un test **pytest-playwright**, un test **Robot Framework** (SeleniumLibrary) et un **scénario Gherkin** exécutable. Un parcours réussi est **mémorisé** et rejoué sans IA ; si le site change, l'IA répare le parcours (**auto-réparation**). |
| **Analyser** | Quand un test échoue, il rédige la **fiche d'anomalie** (étapes pour reproduire, résultat obtenu et attendu, capture), avec un diagnostic de l'IA présenté comme un avis à confirmer. |
| **Piloter** | **Alfred**, une interface vocale, commente la mission en direct et répond aux questions : « pourquoi le dernier test a échoué ? », « refais le test de connexion sur le site de recette ». |

L'IA propose, le programme vérifie : le verdict vient toujours des vérifications réelles, jamais de l'avis du modèle (voir [Les garde-fous](#les-garde-fous)).

![Alfred à la fin d'une mission de démo : la liste des cas, le cadran, la vue du navigateur, le journal des étapes et les tests générés](docs/alfred.png)

## Comment ça marche

```mermaid
flowchart LR
    subgraph Concevoir
        URL[Adresse du site] --> AN[Relevé des pages, sans IA]
        AN --> US[User stories et critères Gherkin]
        US --> CT[Cas de test]
    end
    CT --> A[Agent]
    MEM[(Mémoire des parcours)] -. parcours connu, sans IA .-> A
    A -- état de la page --> M[Modèle IA : Gemini ou Claude]
    M -- action en JSON --> A
    A -- clic, saisie, vérification --> N[Navigateur Playwright]
    N -- nouvelle page --> A
    A --> S[Tests pytest, Robot Framework et Gherkin]
    A --> R[Rapport, captures, resultat.json]
    A -- échec --> F[Fiche d'anomalie et diagnostic]
    AL[Alfred : voix et conversation] -.-> A
```

À chaque tour de l'agent :

1. **Observer** : l'agent liste les éléments visibles de la page (champs, boutons, liens, listes, cases à cocher) et les numérote.
2. **Décider** : il envoie au modèle le cas de test, l'historique et cette liste. Le modèle répond par une seule action en JSON, par exemple `{"action": "fill", "args": {"ref": 1, "text": "standard_user"}}`.
3. **Agir** : l'agent valide la réponse, exécute l'action avec Playwright et prend une capture.
4. On recommence jusqu'à ce que le modèle termine le test.

## Les garde-fous

Une IA peut se tromper. Le programme ne lui fait pas confiance aveuglément :

- **Pas de sélecteur inventé** : le modèle désigne un élément par son numéro dans la liste, il ne peut pas cibler un élément qui n'existe pas.
- **Réponse validée** : une réponse mal formée ou une action inconnue n'est jamais exécutée. L'erreur est renvoyée au modèle pour qu'il corrige.
- **Statut fondé sur les vérifications** : si une vérification échoue, le test est FAIL, même si le modèle conclut PASS. Un PASS sans aucune vérification est refusé (statut BLOQUÉ).
- **Limites** : 20 actions du modèle au maximum et arrêt après 3 erreurs de suite.
- **Mémoire prudente** : seuls les parcours réussis sont mémorisés, et un élément n'est rejoué que s'il est retrouvé à l'identique. Au moindre doute, c'est le modèle qui cherche.
- **Diagnostic présenté comme un avis** : la fiche d'anomalie sépare les faits (étapes, résultats, capture) de l'avis de l'IA, à confirmer par un testeur.
- **Quotas respectés** : quand l'API signale une limite par minute, l'agent patiente le temps qu'elle indique, et Alfred l'annonce. Un quota du jour épuisé arrête la mission, avec l'heure de renouvellement.
- **Clés protégées** : les clés d'API restent dans un fichier `.env`, exclu de Git, comme le dossier `memoire/`.
- **Vision signalée** : une vérification faite à l'œil par l'IA sur une capture (`check_visual`) est marquée comme un avis dans le rapport, n'est pas rejouée par les tests générés (elle y figure comme « à automatiser ») et un parcours qui en dépend n'est pas mémorisé.
- **Conversation contrôlée** : quand on parle librement à Alfred, le modèle ne peut que proposer une action d'une liste fermée (lancer, afficher, arrêter, ouvrir un fichier, rédiger un cas, analyser un site). Le programme vérifie que le cas existe et que l'adresse est valide, et la réponse lue à voix haute ne contient jamais un mot de passe.
- **Propositions relues** : les user stories et les cas proposés par l'IA sont contrôlés (format, mots-clés Gherkin, étapes, résultats attendus), rien n'est enregistré sans l'accord du testeur, et le mot de passe du compte de test n'est jamais envoyé au modèle.

## Installation (Mac ou Linux)

Il faut Python 3.10 ou plus récent.

```bash
git clone https://github.com/smail-ops/agent-qa-playwright.git
cd agent-qa-playwright
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

Ouvre ensuite le fichier `.env` et colle ta clé Gemini après `GEMINI_API_KEY=`. Elle se crée gratuitement sur [Google AI Studio](https://aistudio.google.com).

## Premier essai, sans clé d'API

Le dossier `demo_site/` contient une petite boutique de démonstration. Le fournisseur `scripted` rejoue un scénario écrit à l'avance, sans IA. Pratique pour vérifier l'installation.

Dans un premier terminal :

```bash
python3 -m http.server 8000 -d demo_site
```

Dans un second terminal (environnement `.venv` activé) :

```bash
python -m agent cas_de_test/demo/CT-00_demo_locale.md --provider scripted --scenario cas_de_test/demo/scenario_CT-00.json --headed
```

Une seconde démo, `CT-90`, montre les vérifications plus riches (tri, nombre de boutons, bouton grisé, case cochée) :

```bash
python -m agent cas_de_test/demo/CT-90_demo_tri_et_commande.md --provider scripted --scenario cas_de_test/demo/scenario_CT-90.json --headed
```

Exemples de résultats : [`docs/exemple-demo`](docs/exemple-demo/rapport.md) et [`docs/exemple-tri-commande`](docs/exemple-tri-commande/rapport.md).

Encore plus simple : `python -m alfred` ouvre l'interface Alfred, qui sert elle-même le site de démo (un seul terminal suffit). Sur Mac, un double-clic sur `Alfred.command` fait la même chose.

## Lancer un vrai test avec l'IA

Les cas de test du dossier `cas_de_test/` portent sur [SauceDemo](https://www.saucedemo.com), un site public conçu pour s'entraîner au test.

```bash
python -m agent cas_de_test/CT-01_connexion_valide.md --headed --pause 4
```

- `--headed` affiche le navigateur pendant le test.
- `--pause 4` attend 4 secondes entre deux actions, pour rester dans les quotas de l'offre gratuite de Gemini.
- `--provider claude` utilise Claude à la place de Gemini (clé `ANTHROPIC_API_KEY` dans `.env`).
- `--model` choisit un modèle précis, par exemple `--model gemini-3.8-flash`. Par défaut, l'agent utilise l'alias `gemini-flash-latest`, qui suit le dernier modèle Flash de Google.
- `--url` lance le cas sur une autre adresse (recette, préproduction, adresse IP) sans modifier son fichier, par exemple `--url http://192.168.1.20:8080/`.
- `--sans-memoire` ignore le parcours mémorisé : tout le test passe par le modèle.
- `--vision` envoie aussi la capture d'écran de la page au modèle (voir [La vision](#la-vision)).

Un fichier Gherkin se lance de la même façon : chaque scénario du `.feature` est exécuté comme un cas de test, l'un après l'autre (`--numero 2` pour n'en lancer qu'un).

```bash
python -m agent cas_de_test/US-01_connexion.feature
```

Le programme renvoie un code de sortie exploitable en intégration continue : 0 pour PASS, 1 pour FAIL, 2 pour BLOQUÉ, 3 pour une erreur de configuration.

## Alfred, l'interface vocale

Alfred est une interface web locale. On choisit un cas de test (ou on l'écrit), Alfred lance l'agent et annonce chaque action à voix haute, pendant que le cadran montre où en est la mission et que la vue du navigateur s'affiche en direct. À la fin : le verdict, le rapport, les tests générés et, en cas d'échec, la fiche d'anomalie.

```bash
python -m alfred
```

Sur Mac, tu peux aussi double-cliquer sur `Alfred.command`, à la racine du projet. La page s'ouvre toute seule sur http://127.0.0.1:8765. Alfred sert aussi le site de démo : les cas CT-00 et CT-90 fonctionnent avec le moteur « Démo sans IA », sans clé d'API.

- **Le cadran** : l'anneau extérieur compte les actions (20 au plus), en vert les vérifications réussies et en rouge les échecs. L'anneau intérieur montre la phase en cours : observer, réfléchir, agir.
- **La voix** : Alfred annonce ce qu'il fait, puis le verdict. Les mots de passe ne sont jamais lus ni affichés.
- **Parler à Alfred** : au micro ou dans le champ « Écrivez-moi ». Voir [Parler librement à Alfred](#parler-librement-à-alfred).
- **Le site à tester** : le champ sous le titre du cas remplace son adresse le temps d'une mission (autre site, recette, adresse IP). Le fichier du cas ne change pas.
- **La mémoire** : un cas déjà réussi affiche « Parcours en mémoire ». La mission suivante le rejoue sans IA (les étapes rejouées portent la mention « Mémoire »), et Alfred annonce s'il doit réparer le parcours. La case « Ignorer la mémoire » refait tout avec l'IA.
- **L'analyse des échecs** : quand un test échoue, Alfred annonce le diagnostic de l'IA (bug de l'application, problème du test ou de l'environnement) et le bouton « Fiche d'anomalie » ouvre la fiche prête à copier.
- **Écrire un cas de test** directement dans la page, le lancer, puis l'enregistrer dans `cas_de_test/`.
- **Proposer des user stories et des tests** : Alfred analyse un site et propose des user stories (critères en Gherkin), ou rédige les cas de test d'une user story, tapée ou dictée. Voir [Proposer des user stories et des cas de test](#proposer-des-user-stories-et-des-cas-de-test).
- **La vision** : la case « Vision » envoie aussi la capture de la page au modèle.
- **La reconnaissance du maître** : au démarrage, Alfred se présente et salue son maître à voix haute, avec la voix du Mac. Au premier lancement sur un ordinateur, il demande ton prénom et l'enregistre dans le `.env` de cet ordinateur (`ALFRED_MAITRE`) : chaque installation connaît son maître. `--silencieux` coupe la salutation, `ALFRED_VOIX` choisit la voix.
- **Réglages** : changer le prénom, et la voix utilisée dans la page.

### Parler librement à Alfred

Alfred comprend deux sortes de phrases :

- **Les commandes simples**, comprises dans la page, sans IA et sans quota : « lance la démo », « lance le cas 2 », « vas-y », « arrête », « ouvre le rapport », « ouvre la fiche d'anomalie ».
- **Tout le reste** : une question ou une demande libre part au modèle (Gemini ou Claude), avec le contexte utile : la liste des cas de test, le cas affiché, la mission en cours et les derniers résultats (statut, vérifications en échec, diagnostic de l'IA). Une requête par phrase.

Quelques exemples :

| Ce que tu dis | Ce que fait Alfred |
|---|---|
| « Pourquoi le dernier test a échoué ? » | il répond à partir du résultat et du diagnostic, et propose d'ouvrir la fiche d'anomalie |
| « Quels tests ont échoué aujourd'hui ? » | il les cite, à partir des résultats du jour |
| « Refais le test de connexion sur le site de recette » | il choisit le bon cas, change l'adresse et lance la mission |
| « Vérifie qu'on ne peut pas commander sans code postal » | il rédige le cas au format CT, l'affiche, et le lance quand tu dis « vas-y » |
| « Analyse saucedemo.com et propose des user stories » | il ouvre les propositions et lance l'analyse |
| « Montre-moi la fiche d'anomalie de CT-14 » | il l'ouvre, même si le test a tourné un autre jour |

Alfred garde les derniers échanges en tête : on peut enchaîner (« et celui d'avant ? »). Sans clé d'API, seules les commandes simples fonctionnent, et Alfred le dit. Les résultats viennent du fichier `resultat.json` que chaque exécution écrit dans son dossier de rapport.

Sous le capot, le serveur n'utilise que la bibliothèque standard de Python et la page n'a pas de framework. Chaque événement de l'agent (observer, réfléchir, agir, étape terminée, mémoire, réparation, analyse) est diffusé à la page en Server-Sent Events. Le serveur n'écoute que sur 127.0.0.1, refuse les requêtes venant d'autres sites et ne sert que les fichiers de ses propres missions.

## Écrire un cas de test

Un fichier Markdown, avec un titre et une URL. Le reste est lu par l'IA comme par un testeur :

```markdown
# CT-02 Connexion refusée pour un utilisateur bloqué
URL : https://www.saucedemo.com/

## Étapes
1. Saisir « locked_out_user » dans le champ Username
2. Saisir « secret_sauce » dans le champ Password
3. Cliquer sur le bouton Login

## Résultats attendus
- Le message « Sorry, this user has been locked out. » s'affiche
- L'URL ne contient pas « inventory »
```

Pour chaque résultat attendu, l'agent choisit la vérification la plus précise :

| Résultat attendu | Vérification | Dans le test pytest | Dans le test Robot |
|---|---|---|---|
| un texte s'affiche, ou ne s'affiche pas | `check_text` | `to_contain_text` | `La page affiche` |
| l'URL contient, ou ne contient pas | `check_url` | `to_have_url` | `Wait Until Location Contains` |
| un ordre (« triés par prix croissant ») | `check_order` | expression régulière « A.*B » | `La page affiche dans l'ordre` |
| un nombre (« 2 boutons Remove ») | `check_count` | `compte()` | `La page affiche ce nombre de fois` |
| le contenu d'un champ, l'option d'une liste | `check_value` | `to_have_value` | `Textfield Value Should Be`, `List Selection Should Be` |
| un bouton grisé ou actif, une case cochée | `check_state` | `to_be_disabled`, `to_be_checked` | `Element Should Be Disabled`, `Checkbox Should Be Selected` |

Les résultats attendus négatifs (« ne s'affiche pas », « ne contient pas ») sont contrôlés avec l'option « absent ». Comme Playwright, chaque vérification laisse quelques secondes à la page pour atteindre l'état attendu, et seul le texte visible compte : un message d'erreur caché dans la page n'est pas considéré comme affiché.

Un cas de test peut aussi s'écrire en **Gherkin**, dans un fichier `.feature` : une ligne `# URL : https://...`, une `Fonctionnalité:` et ses `Scénario:`. Chaque scénario apparaît dans Alfred et se lance comme un cas : les lignes « Étant donné que » et « Quand » sont les étapes, les lignes « Alors » les résultats attendus.

```gherkin
# language: fr
# URL : https://www.saucedemo.com/
@US-01
Fonctionnalité: US-01 Connexion à la boutique
  En tant que client
  Je veux me connecter avec mon identifiant et mon mot de passe
  Afin d'accéder aux produits

  Scénario: Connexion réussie
    Étant donné que je suis sur la page de connexion
    Quand je saisis « standard_user » dans le champ « Username »
    Et je saisis « secret_sauce » dans le champ « Password »
    Et je clique sur le bouton « Login »
    Alors la page « Products » s'affiche
```

## Proposer des user stories et des cas de test

L'agent peut aussi préparer les tests. Dans Alfred, le bouton « Proposer des user stories et des tests » ouvre deux parcours :

1. **À partir d'un site** : Alfred ouvre l'adresse, parcourt quelques pages du même site (5 au plus) et relève les titres, les champs, les listes, les boutons, les liens et le texte visible. Ce relevé est fait par le programme, sans IA. Le modèle le reçoit ensuite, avec les captures si la vision est cochée, et propose de 3 à 6 **user stories** (« En tant que..., je veux..., afin de... »), classées par priorité, chacune avec ses **critères d'acceptation en Gherkin**. Une seule requête au modèle.
2. **À partir d'une user story** (proposée par Alfred, tapée ou dictée) : le modèle rédige de 2 à 5 **cas de test** au format des fichiers CT, le cas nominal d'abord, puis les cas d'erreur et les limites. Une requête de plus.

On coche les propositions à garder. Une user story devient un fichier `cas_de_test/US-xx_....feature`, dont chaque scénario se lance comme un cas de test. Un cas devient `cas_de_test/CT-xx_....md`, avec le prochain numéro libre.

![Les user stories proposées pour le site de démo, avec leurs critères d'acceptation en Gherkin](docs/propositions.png)

Pendant l'analyse, rien n'est modifié sur le site : seuls les liens du même site sont suivis, jamais ceux qui pourraient déconnecter, supprimer ou payer, et aucun bouton n'est cliqué. Pour voir les pages réservées aux utilisateurs, on peut donner un **compte de test** : Alfred remplit le formulaire de connexion de la première page. Le mot de passe sert uniquement à cette connexion : il n'est ni envoyé au modèle, ni affiché, ni enregistré. Comme le modèle ne le connaît pas, il écrit `<mot de passe>` dans ses propositions (à compléter dans le fichier), sauf si le site l'affiche en clair, comme SauceDemo.

Le moteur « Démo sans IA » rejoue des réponses enregistrées pour le site de démo local : de quoi essayer sans clé d'API.

En ligne de commande, les propositions sont écrites dans un dossier `propositions/` (exclu de Git), à relire avant de les déplacer dans `cas_de_test/` :

```bash
python -m agent.propositions https://www.saucedemo.com/ --identifiant standard_user
python -m agent.propositions https://www.saucedemo.com/ --user-story "En tant que client, je veux trier les produits par prix, afin de trouver le moins cher."
```

Avec `--identifiant`, le mot de passe est demandé sans s'afficher à l'écran (il n'apparaît pas non plus dans l'historique du terminal).

## La vision

Avec `--vision` (ou `AGENT_VISION=1` dans `.env`, ou la case « Vision » d'Alfred), le modèle reçoit à chaque tour une capture d'écran de la page, en plus de la liste des éléments. Il comprend mieux la mise en page, et une action de plus lui est ouverte : `check_visual`, pour un résultat attendu qui ne se vérifie qu'à l'œil (une photo, une couleur, une mise en page).

Une telle vérification reste l'avis du modèle sur une capture, pas une mesure : le rapport l'indique, les tests générés la gardent en commentaire « à automatiser », et un parcours qui en dépend n'est pas mémorisé. Le cas **CT-16** en montre l'intérêt : avec l'utilisateur `problem_user`, SauceDemo affiche la même photo pour tous les produits, un défaut invisible dans le texte de la page.

La vision coûte un peu plus cher : chaque requête envoie une image (en JPEG, de faible poids).

## La campagne SauceDemo

Le dossier `cas_de_test/` contient une campagne de 16 cas sur [SauceDemo](https://www.saucedemo.com), des cas positifs et des cas négatifs :

| Domaine | Cas | Verdict attendu |
|---|---|---|
| Connexion | CT-01 Connexion avec un utilisateur valide | PASS |
| Connexion | CT-02 Connexion refusée pour un utilisateur bloqué | PASS |
| Connexion | CT-04 Connexion refusée avec un mot de passe incorrect | PASS |
| Connexion | CT-05 Connexion refusée sans identifiant | PASS |
| Connexion | CT-06 Connexion refusée sans mot de passe | PASS |
| Connexion | CT-07 Déconnexion | PASS |
| Catalogue | CT-08 Fiche d'un produit | PASS |
| Catalogue | CT-15 Tri des produits par prix croissant | PASS |
| Panier | CT-03 Ajout d'un produit au panier | PASS |
| Panier | CT-09 Panier avec deux produits | PASS |
| Panier | CT-10 Retirer un produit du panier | PASS |
| Commande | CT-11 Commande complète | PASS |
| Commande | CT-12 Récapitulatif de commande avec les taxes | PASS |
| Commande | CT-13 Commande refusée sans code postal | PASS |
| Commande | CT-14 Commande avec l'utilisateur problem_user | FAIL : bug connu de SauceDemo, le nom de famille saisi n'est pas gardé |
| Catalogue | CT-16 Photos des produits avec problem_user (avec la vision) | FAIL : bug connu de SauceDemo, la même photo pour tous les produits |

CT-14 et CT-16 montrent que l'agent détecte de vrais défauts : l'utilisateur de test `problem_user` de SauceDemo contient des bugs volontaires, et l'agent rédige la fiche d'anomalie. Chaque cas consomme une dizaine d'appels au modèle la première fois. Grâce à la mémoire, les exécutions suivantes d'un cas réussi n'en consomment plus aucun : avec l'offre gratuite de Gemini, lance les nouveaux cas quelques-uns à la fois.

## Résultats

Chaque exécution crée un dossier `rapports/<date>_<cas_de_test>/` contenant :

| Fichier | Contenu |
|---|---|
| `rapport.md` | statut, appels au modèle, usage de la mémoire, tableau des étapes avec la raison de chaque action, vérifications |
| `captures/` | une capture d'écran par étape |
| `test_<cas_de_test>.py` | le script pytest qui rejoue le test sans IA |
| `<cas_de_test>.robot` | le test Robot Framework qui rejoue le test avec Selenium |
| `<cas_de_test>.feature` | le scénario Gherkin en français |
| `<cas_de_test>_bdd.robot` | le même scénario Gherkin, exécutable avec Robot Framework |
| `anomalie.md` | la fiche d'anomalie, seulement si le test échoue ou reste bloqué |
| `resultat.json` | le même bilan, lisible par un programme : statut, vérifications en échec, diagnostic, fichiers produits |

Pour rejouer le script pytest :

```bash
pytest rapports/<dossier>/test_<cas_de_test>.py
```

### Rejouer avec Robot Framework

Le fichier `.robot` utilise SeleniumLibrary, la bibliothèque de Robot Framework qui pilote le navigateur avec Selenium. Il faut Google Chrome sur la machine : Selenium télécharge seul le pilote qui correspond à sa version.

```bash
robot rapports/<dossier>/<cas_de_test>.robot
```

- `--variable BROWSER:headlesschrome` lance Chrome sans fenêtre, par exemple en intégration continue (`firefox` fonctionne aussi).
- `--variable DELAI:10s` allonge l'attente maximale, 5 secondes par défaut.
- `--outputdir resultats-robot` range le journal (`log.html` et `report.html`) dans un dossier à part.

Le générateur adapte le parcours à Selenium :

- les sélecteurs propres à Playwright (`:has-text`, plusieurs éléments identiques) deviennent des XPath ;
- le texte attendu est écrit tel qu'il apparaît sur la page, et pour une liste déroulante c'est la valeur réelle de l'option qui est utilisée ;
- les vérifications de texte passent par des mots-clés écrits dans le fichier (`La page affiche`, `La page n'affiche pas`, `La page affiche dans l'ordre`, `La page affiche ce nombre de fois`). Comme l'agent, ils ne regardent que le texte visible. `Wait Until Page Contains` compterait aussi un message d'erreur caché dans la page, et le test passerait à tort.

Exemple : [`docs/exemple-demo/ct_00_demo_locale_connexion_et_panier.robot`](docs/exemple-demo/ct_00_demo_locale_connexion_et_panier.robot).

### Le scénario Gherkin

Le fichier `.feature` décrit le parcours en langage métier, dans le format des scénarios BDD (Cucumber, Squash TM, Behave) :

```gherkin
# language: fr
Fonctionnalité: CT-90 Démo locale : tri, panier et commande

  @CT-90
  Scénario: CT-90 Démo locale : tri, panier et commande
    Étant donné que je suis sur la page "http://localhost:8000/index.html"
    Quand je saisis "standard_user" dans le champ "Identifiant"
    Et je saisis "secret_sauce" dans le champ "Mot de passe"
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
```

Robot Framework comprend ces mots-clés en français : le fichier `_bdd.robot` reprend le même scénario, et chaque phrase y devient un mot-clé qui rejoue l'étape avec SeleniumLibrary. Le scénario Gherkin est donc exécutable :

```bash
robot rapports/<dossier>/<cas_de_test>_bdd.robot
```

Exemple complet : [`docs/exemple-tri-commande`](docs/exemple-tri-commande/ct_90_demo_locale_tri_panier_et_commande.feature).

## La mémoire des parcours

Quand un test réussit avec l'IA, son parcours est enregistré dans `memoire/` : les actions, et pour chaque élément son sélecteur et son XPath. À la prochaine exécution du même cas :

1. **Rejeu sans IA** : l'agent rejoue le parcours étape par étape. Aucun appel au modèle, le quota reste intact, et le test dure quelques secondes.
2. **Auto-réparation** : si un élément n'est plus retrouvé à l'identique (le site a changé) ou qu'une action n'aboutit plus, le modèle reprend la main à cette étape, avec l'historique de ce qui a déjà été fait. Si le test réussit, la mémoire est mise à jour avec le parcours réparé.
3. **Échec réel** : si une vérification échoue pendant le rejeu, c'est le comportement du site qui a changé : le test est FAIL, et la mémoire n'est pas modifiée.

Le parcours est lié au texte exact du cas de test et à son adresse : modifier le cas, ou le lancer sur un autre site, repart de l'IA. Pour tout refaire avec l'IA : `--sans-memoire`, la case « Ignorer la mémoire » dans Alfred, ou supprimer le dossier `memoire/`. La démo sans IA n'utilise pas la mémoire : elle rejoue déjà un scénario enregistré.

## La fiche d'anomalie

Quand un test échoue (ou reste bloqué), l'agent rédige `anomalie.md`, prête à reporter dans Jira, Mantis ou Squash TM :

- un titre, la gravité et la nature proposées ;
- l'environnement : site testé, version de Chromium, système ;
- les **étapes pour reproduire**, tirées des actions réellement réalisées (les tentatives ratées puis corrigées par l'agent n'y figurent pas) ;
- le **résultat obtenu** et le **résultat attendu**, repris du cas de test ;
- la capture au moment de l'échec, et les tests générés qui reproduisent l'échec.

Avec Gemini ou Claude, le modèle reçoit ensuite les faits (cas de test, historique, état final de la page) et propose un **diagnostic** : bug de l'application, problème du test ou problème d'environnement, avec une gravité, une explication et la prochaine action conseillée. Ce diagnostic coûte un appel au modèle, et il est présenté comme un avis à confirmer.

## Tests du projet

Les 218 tests du projet utilisent le site de démo local et le fournisseur `scripted` : ils ne demandent ni clé d'API ni connexion Internet.

```bash
pytest
```

Ils couvrent la lecture des cas de test (Markdown et Gherkin) et des réponses du modèle, la construction des requêtes Gemini et Claude (avec ou sans image), l'observation de la page, les familles de vérifications (dont l'attente d'une page qui change encore et l'avis visuel), les règles de statut (FAIL forcé, PASS sans vérification refusé, arrêt après erreurs), la mémoire (rejeu sans appel au modèle, auto-réparation, cas modifié, autre site), la fiche d'anomalie, les tests générés, l'analyse d'un site (connexion avec un compte de test, liens risqués jamais suivis, mot de passe jamais transmis) et le contrôle des user stories et des cas proposés. Le script pytest est rejoué tel quel. Les fichiers `.robot`, classique et Gherkin, sont exécutés par Robot Framework, avec un double de SeleniumLibrary piloté par Playwright (`tests/robot_double`) pour se passer de Chrome.

Les tests d'Alfred vérifient l'API, le flux en direct, l'arrêt d'une mission, la mémoire, l'annonce du diagnostic, les propositions, la conversation libre (contexte envoyé au modèle, actions contrôlées, mot de passe jamais prononcé), les protections du serveur et la compréhension des commandes vocales, puis pilotent dans l'interface des missions complètes, une analyse de site et un cas de test dicté puis lancé sur « vas-y ».

GitHub Actions lance ces tests à chaque push, puis rejoue les tests Robot (classiques et Gherkin) des deux démos avec le vrai SeleniumLibrary et Chrome.

## Structure du code

| Fichier | Rôle |
|---|---|
| `agent/__main__.py` | ligne de commande |
| `agent/testcase.py` | lecture du cas de test en Markdown |
| `agent/user_stories.py` | user stories, fichiers Gherkin (.feature) et leurs scénarios lancés comme des cas de test |
| `agent/analyse.py` | analyse d'un site sans IA : pages, champs, boutons, liens, connexion avec un compte de test |
| `agent/propositions.py` | user stories et cas de test proposés par le modèle, contrôle et enregistrement |
| `agent/browser.py` | observation de la page et exécution des actions et des vérifications Playwright |
| `agent/actions.py` | liste des actions possibles et validation de la réponse JSON du modèle |
| `agent/prompts.py` | consignes données au modèle et message envoyé à chaque tour |
| `agent/providers.py` | appels aux API Gemini et Claude, faux modèle pour les tests |
| `agent/runner.py` | boucle de l'agent, rejeu de la mémoire, calcul du statut final, événements pour une interface |
| `agent/memoire.py` | mémoire des parcours réussis et rejeu sans IA |
| `agent/anomalie.py` | fiche d'anomalie et diagnostic de l'échec par le modèle |
| `agent/execution.py` | exécution complète d'un cas de test, commune à la ligne de commande et à Alfred |
| `agent/report.py` | rapport Markdown |
| `agent/resultat.py` | bilan lisible par un programme (`resultat.json`), relu par Alfred |
| `agent/codegen.py` | génération du script pytest-playwright |
| `agent/codegen_robot.py` | génération du test Robot Framework (SeleniumLibrary) |
| `agent/codegen_gherkin.py` | génération du scénario Gherkin et de sa version Robot Framework |
| `tests/robot_double/` | double de SeleniumLibrary piloté par Playwright, pour tester les fichiers `.robot` sans Chrome |
| `demo_site/` | boutique de démonstration : connexion, produits, tri, formulaire de commande |
| `alfred/server.py` | serveur local d'Alfred : API, flux d'événements en direct, protections |
| `alfred/missions.py` | missions lancées depuis l'interface, une à la fois |
| `alfred/narration.py` | phrases affichées et prononcées par Alfred |
| `alfred/conversation.py` | conversation libre : contexte envoyé au modèle, réponse et action contrôlées |
| `alfred/static/index.html` | l'interface : cadran, voix, commandes vocales et conversation, journal de mission, propositions |
| `Alfred.command` | lanceur d'Alfred pour macOS, en double-clic |

## Dépannage

| Message | Solution |
|---|---|
| `Clé GEMINI_API_KEY absente` | le fichier `.env` manque ou la clé n'est pas collée après `GEMINI_API_KEY=` |
| `Erreur API 404` | le modèle a été retiré : indique un modèle disponible dans `GEMINI_MODEL`, par exemple `gemini-3.8-flash` (liste sur Google AI Studio) |
| `La réponse du modèle est inutilisable` | le modèle a répondu dans un format inattendu (cela arrive avec les petits modèles) : relance la proposition, ou change de modèle |
| `Erreur API 429` ou `quota du jour épuisé` | quota de l'offre gratuite atteint. Quand Google indique un délai, l'agent patiente tout seul (3 fois au plus). Si c'est le quota du jour, il s'arrête et donne l'heure de renouvellement : en attendant, change de modèle avec `GEMINI_MODEL` dans `.env`, rejoue les cas déjà en mémoire ou utilise la démo sans IA |
| Le test rejoue un ancien parcours | la mémoire garde le dernier parcours réussi : lance le cas avec `--sans-memoire` (ou la case « Ignorer la mémoire »), ou supprime son fichier dans `memoire/` |
| `CERTIFICATE_VERIFY_FAILED` (Mac) | Python installé depuis python.org n'a pas encore ses certificats : double-clique sur `Install Certificates.command` dans le dossier `Applications/Python 3.x` |
| `Executable doesn't exist` | lance `playwright install chromium` |
| `robot: command not found` | active l'environnement (`source .venv/bin/activate`), puis `pip install -r requirements.txt` |
| `SessionNotCreatedException` avec `robot` | Chrome n'est pas installé ou n'a pas pu démarrer : installe Google Chrome, ou essaie `--variable BROWSER:firefox` |
| `Le port 8765 est déjà utilisé` | Alfred tourne déjà : ouvre http://127.0.0.1:8765, ou lance `python -m alfred --port 8766` |
| Alfred ne parle pas | clique d'abord dans la page (le navigateur bloque le son avant toute action), vérifie que « Voix activée » est allumé, et installe une voix française dans Réglages Système, Accessibilité, Contenu énoncé |
| Alfred répond qu'il lui faut une clé | une phrase qui n'est pas une commande simple part au modèle : ajoute `GEMINI_API_KEY` (ou `ANTHROPIC_API_KEY`) dans `.env`, puis relance Alfred. Sans clé, dis par exemple « lance la démo » |
| Pas de bouton micro | la reconnaissance vocale dépend du navigateur : elle fonctionne dans Chrome, Safari peut demander d'activer la dictée de macOS, Firefox ne la propose pas |

## Limites et pistes

- Les pages très dynamiques, les iframes et le shadow DOM ne sont pas encore gérés.
- La première exécution d'un cas coûte un appel au modèle par action : un cas de test de 10 étapes représente une douzaine d'appels. Les suivantes, grâce à la mémoire, n'en coûtent aucun tant que le site ne change pas.
- L'analyse d'un site suit les liens : sur une application monopage, où la navigation passe par des boutons, elle voit surtout la page d'arrivée et celle qui suit la connexion.
- Pistes : import des cas de test depuis un export Squash TM, campagne complète avec un rapport de synthèse, campagne lancée à la voix (« relance tous les tests qui ont échoué »).

## Licence

MIT
