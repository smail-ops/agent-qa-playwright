# Rapport : CT-00 Démo locale : connexion et panier

| | |
|---|---|
| Statut | **✅ PASS** |
| Modèle | scripted (scénario simulé) |
| Date | 17/10/2026 11:51 |
| Durée | 1 s |
| Actions | 8 |

**Conclusion :** Connexion réussie et produit ajouté au panier

## Étapes

| N° | Action | Cible | Résultat | Raison donnée par l'agent | Capture |
|---|---|---|---|---|---|
| 1 | goto | « http://localhost:8000/index.html » | OK | Ouverture de l'URL indiquée dans le cas de test | [voir](captures/etape-01.png) |
| 2 | fill | `#user-name` ← « standard_user » | OK | Étape 1 : saisie de l'identifiant | [voir](captures/etape-02.png) |
| 3 | fill | `#password` ← « secret_sauce » | OK | Étape 2 : saisie du mot de passe | [voir](captures/etape-03.png) |
| 4 | click | `[data-test="login-button"]` | OK | Étape 3 : clic sur Connexion | [voir](captures/etape-04.png) |
| 5 | check_url | « inventory » | ✅ vérifié | Résultat attendu : page des produits | [voir](captures/etape-05.png) |
| 6 | check_text | « Produits » | ✅ vérifié | Résultat attendu : titre Produits visible | [voir](captures/etape-06.png) |
| 7 | click | `button:has-text("Ajouter au panier") (n°1)` | OK | Étape 4 : ajout du premier produit | [voir](captures/etape-07.png) |
| 8 | check_text | « Panier (1) » | ✅ vérifié | Résultat attendu : compteur du panier à 1 | [voir](captures/etape-08.png) |

## Vérifications

- ✅ L'URL contient « inventory »
- ✅ Texte « Produits » présent
- ✅ Texte « Panier (1) » présent

## Tests générés

Ils rejouent les actions réussies de l'agent, sans IA :

- [`test_ct_00_demo_locale_connexion_et_panier.py`](test_ct_00_demo_locale_connexion_et_panier.py) : pytest et Playwright
- [`ct_00_demo_locale_connexion_et_panier.robot`](ct_00_demo_locale_connexion_et_panier.robot) : Robot Framework et Selenium

```
pytest test_ct_00_demo_locale_connexion_et_panier.py
robot ct_00_demo_locale_connexion_et_panier.robot
```

## Cas de test exécuté

```markdown
# CT-00 Démo locale : connexion et panier
URL : http://localhost:8000/index.html

## Étapes
1. Saisir « standard_user » dans le champ Identifiant
2. Saisir « secret_sauce » dans le champ Mot de passe
3. Cliquer sur Connexion
4. Ajouter le premier produit au panier

## Résultats attendus
- L'URL contient « inventory »
- La page « Produits » s'affiche
- Le panier indique « Panier (1) »
```
