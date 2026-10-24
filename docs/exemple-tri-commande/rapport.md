# Rapport : CT-90 Démo locale : tri, panier et commande

| | |
|---|---|
| Statut | **✅ PASS** |
| Modèle | scripted (scénario simulé) |
| Appels au modèle | aucun (démo sans IA) |
| Vision | non |
| Navigateur | Chromium 141.0.7390.37 |
| Date | 24/10/2026 07:41 |
| Durée | 3 s |
| Actions | 15 |

**Conclusion :** Tri, panier et formulaire de commande conformes

## Étapes

| N° | Action | Cible | Résultat | Raison donnée par l'agent | Capture |
|---|---|---|---|---|---|
| 1 | goto | « http://localhost:8000/index.html » | OK | Ouverture de l'URL indiquée dans le cas de test | [voir](captures/etape-01.png) |
| 2 | fill | `#user-name` ← « standard_user » | OK | Étape 1 : saisie de l'identifiant | [voir](captures/etape-02.png) |
| 3 | fill | `#password` ← « [mot de passe] » | OK | Étape 1 : saisie du mot de passe | [voir](captures/etape-03.png) |
| 4 | click | `[data-test="login-button"]` | OK | Étape 2 : clic sur Connexion | [voir](captures/etape-04.png) |
| 5 | check_count | « Ajouter au panier » (3 fois) | ✅ vérifié | Résultat attendu : trois boutons « Ajouter au panier » | [voir](captures/etape-05.png) |
| 6 | select | `[data-test="sort"]` ← « Prix croissant » | OK | Étape 3 : tri par prix croissant | [voir](captures/etape-06.png) |
| 7 | check_order | « Lampe de vélo » puis « T-shirt » puis « Sac à dos » | ✅ vérifié | Résultat attendu : produits triés du moins cher au plus cher | [voir](captures/etape-07.png) |
| 8 | click | `button:has-text("Ajouter au panier")` (n°1) | OK | Étape 4 : ajout du premier produit | [voir](captures/etape-08.png) |
| 9 | check_count | « Retirer » (1 fois) | ✅ vérifié | Résultat attendu : un seul bouton « Retirer » | [voir](captures/etape-09.png) |
| 10 | click | `[data-test="checkout"]` | OK | Étape 5 : clic sur Commander | [voir](captures/etape-10.png) |
| 11 | check_state | `[data-test="continuer"]` : grisé | ✅ vérifié | Résultat attendu : « Continuer » grisé avant l'acceptation des conditions | [voir](captures/etape-11.png) |
| 12 | click | `[data-test="cgu"]` | OK | Étape 6 : acceptation des conditions générales de vente | [voir](captures/etape-12.png) |
| 13 | check_state | `[data-test="cgu"]` : coché | ✅ vérifié | La case est bien cochée | [voir](captures/etape-13.png) |
| 14 | check_state | `[data-test="continuer"]` : actif | ✅ vérifié | Résultat attendu : « Continuer » devient actif | [voir](captures/etape-14.png) |
| 15 | check_value | `#code-postal` ← (vide) | ✅ vérifié | Résultat attendu : le champ Code postal est vide | [voir](captures/etape-15.png) |

## Vérifications

- ✅ « Ajouter au panier » apparaît 3 fois
- ✅ Ordre respecté : « Lampe de vélo », puis « T-shirt », puis « Sac à dos »
- ✅ « Retirer » apparaît 1 fois
- ✅ « Continuer » est grisé
- ✅ « J'accepte les conditions générales de vente » est coché
- ✅ « Continuer » est actif
- ✅ « Code postal » est vide

## Tests générés

Ils rejouent les actions réussies de l'agent, sans IA :

- [`test_ct_90_demo_locale_tri_panier_et_commande.py`](test_ct_90_demo_locale_tri_panier_et_commande.py) : pytest et Playwright
- [`ct_90_demo_locale_tri_panier_et_commande.robot`](ct_90_demo_locale_tri_panier_et_commande.robot) : Robot Framework et Selenium
- [`ct_90_demo_locale_tri_panier_et_commande.feature`](ct_90_demo_locale_tri_panier_et_commande.feature) : scénario Gherkin en français (BDD), à lire ou à reprendre dans Squash TM ou Cucumber
- [`ct_90_demo_locale_tri_panier_et_commande_bdd.robot`](ct_90_demo_locale_tri_panier_et_commande_bdd.robot) : le scénario Gherkin, exécutable avec Robot Framework

```
pytest test_ct_90_demo_locale_tri_panier_et_commande.py
robot ct_90_demo_locale_tri_panier_et_commande.robot
robot ct_90_demo_locale_tri_panier_et_commande_bdd.robot
```

## Cas de test exécuté

```markdown
# CT-90 Démo locale : tri, panier et commande
URL : http://localhost:8000/index.html

## Étapes
1. Saisir « standard_user » dans le champ Identifiant, puis « [mot de passe] » dans le champ Mot de passe
2. Cliquer sur Connexion
3. Trier les produits par « Prix croissant »
4. Ajouter le premier produit au panier
5. Cliquer sur « Commander »
6. Cocher la case « J'accepte les conditions générales de vente »

## Résultats attendus
- Trois boutons « Ajouter au panier » s'affichent
- Après le tri, l'ordre est : « Lampe de vélo », « T-shirt », puis « Sac à dos »
- Un seul bouton « Retirer » s'affiche après l'ajout
- Le bouton « Continuer » est grisé tant que les conditions ne sont pas acceptées, puis il devient actif
- Le champ Code postal est vide
```
