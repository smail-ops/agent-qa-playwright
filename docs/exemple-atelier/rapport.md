# Rapport : CT-91 Démo locale : iframe, Shadow DOM, liste maison et alerte

| | |
|---|---|
| Statut | **✅ PASS** |
| Modèle | scripted (scénario simulé) |
| Appels au modèle | aucun (démo sans IA) |
| Vision | non |
| Date | 23/10/2026 08:52 |
| Durée | 6 s |
| Actions | 16 |

**Conclusion :** Cadre, composant web, liste maison, confirmation et chargement lent : tout est vérifié

## Étapes

| N° | Action | Cible | Résultat | Raison donnée par l'agent | Capture |
|---|---|---|---|---|---|
| 1 | goto | « http://localhost:8000/atelier.html » | OK | Ouverture de l'URL indiquée dans le cas de test | [voir](captures/etape-01.png) |
| 2 | fill | `#note` (cadre « Éditeur de note ») ← « Livrer avant midi » | OK | Étape 1 : saisie dans l'éditeur, placé dans un cadre (iframe) | [voir](captures/etape-02.png) |
| 3 | check_value | `#note` (cadre « Éditeur de note ») ← « Livrer avant midi » | ✅ vérifié | Résultat attendu : la note contient le texte saisi | [voir](captures/etape-03.png) |
| 4 | click | `#generer` dans `generateur-code` | OK | Étape 2 : le bouton est dans un composant web (Shadow DOM) | [voir](captures/etape-04.png) |
| 5 | check_value | `#code` dans `generateur-code` ← « ^PROMO-[0-9A-F]{4}-[0-9A-F]{4}$ » (motif) | ✅ vérifié | Résultat attendu : le code suit le format PROMO-XXXX-XXXX | [voir](captures/etape-05.png) |
| 6 | check_text | « Code prêt à copier » | ✅ vérifié | Résultat attendu : message affiché par le composant | [voir](captures/etape-06.png) |
| 7 | click | `xpath=//label[normalize-space(.)="Rôle"]/following::div[@tabindex][1]` | OK | Étape 3 : ouverture de la liste « maison » Rôle, faite de div | [voir](captures/etape-07.png) |
| 8 | click | `div[role="option"]:has-text("ESS")` | OK | Étape 3 : choix de l'option ESS | [voir](captures/etape-08.png) |
| 9 | check_value | `xpath=//label[normalize-space(.)="Rôle"]/following::div[@tabindex][1]` ← « ESS » | ✅ vérifié | Résultat attendu : la liste Rôle indique ESS | [voir](captures/etape-09.png) |
| 10 | check_text | « Rôle : ESS » | ✅ vérifié | Résultat attendu : le rôle choisi s'affiche | [voir](captures/etape-10.png) |
| 11 | dialog | OK | OK | Étape 4 : la fenêtre de confirmation recevra OK | [voir](captures/etape-11.png) |
| 12 | click | `[data-test="envoyer"]` | OK, fenêtre « Confirmer l'envoi de la commande ? » : OK | Étape 4 : envoi de la commande | [voir](captures/etape-12.png) |
| 13 | check_text | « Commande envoyée » | ✅ vérifié | Résultat attendu : la commande est envoyée | [voir](captures/etape-13.png) |
| 14 | click | `[data-test="charger"]` | OK | Étape 5 : chargement du stock | [voir](captures/etape-14.png) |
| 15 | wait | « Stock chargé » (10 s au plus) | OK | Étape 5 : le chargement prend quelques secondes | [voir](captures/etape-15.png) |
| 16 | check_text | « Stock chargé : 12 articles » | ✅ vérifié | Résultat attendu : le stock s'affiche | [voir](captures/etape-16.png) |

## Vérifications

- ✅ « Note de commande » contient « Livrer avant midi »
- ✅ « Code promo » contient « PROMO-7F3A-91C2 », conforme au motif
- ✅ Texte « Code prêt à copier » présent
- ✅ « Rôle » contient « ESS »
- ✅ Texte « Rôle : ESS » présent
- ✅ Texte « Commande envoyée » présent
- ✅ Texte « Stock chargé : 12 articles » présent

## Tests générés

Ils rejouent les actions réussies de l'agent, sans IA :

- [`test_ct_91_demo_locale_iframe_shadow_dom_liste_maison_et_alerte.py`](test_ct_91_demo_locale_iframe_shadow_dom_liste_maison_et_alerte.py) : pytest et Playwright
- [`ct_91_demo_locale_iframe_shadow_dom_liste_maison_et_alerte.robot`](ct_91_demo_locale_iframe_shadow_dom_liste_maison_et_alerte.robot) : Robot Framework et Selenium
- [`ct_91_demo_locale_iframe_shadow_dom_liste_maison_et_alerte.feature`](ct_91_demo_locale_iframe_shadow_dom_liste_maison_et_alerte.feature) : scénario Gherkin en français (BDD), à lire ou à reprendre dans Squash TM ou Cucumber
- [`ct_91_demo_locale_iframe_shadow_dom_liste_maison_et_alerte_bdd.robot`](ct_91_demo_locale_iframe_shadow_dom_liste_maison_et_alerte_bdd.robot) : le scénario Gherkin, exécutable avec Robot Framework

```
pytest test_ct_91_demo_locale_iframe_shadow_dom_liste_maison_et_alerte.py
robot ct_91_demo_locale_iframe_shadow_dom_liste_maison_et_alerte.robot
robot ct_91_demo_locale_iframe_shadow_dom_liste_maison_et_alerte_bdd.robot
```

## Cas de test exécuté

```markdown
# CT-91 Démo locale : iframe, Shadow DOM, liste maison et alerte
URL : http://localhost:8000/atelier.html

## Étapes
1. Saisir « Livrer avant midi » dans la note de commande (un éditeur placé dans un cadre)
2. Cliquer sur le bouton « Générer un code » du code promo
3. Ouvrir la liste « Rôle » et choisir « ESS »
4. Cliquer sur « Envoyer la commande », puis confirmer l'envoi en cliquant sur « OK »
5. Cliquer sur « Charger le stock » et attendre la fin du chargement, qui prend quelques secondes

## Résultats attendus
- La note de commande contient « Livrer avant midi »
- Le code promo suit le format PROMO-XXXX-XXXX (chiffres, et lettres de A à F)
- Le message « Code prêt à copier » s'affiche
- La liste « Rôle » indique « ESS », et « Rôle : ESS » s'affiche
- « Commande envoyée » s'affiche
- « Stock chargé : 12 articles » s'affiche
```
