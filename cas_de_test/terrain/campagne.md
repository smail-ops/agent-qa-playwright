# Campagne terrain : l'agent sur cinq sites d'entraînement

Vingt cas de test sur cinq sites publics conçus pour s'entraîner au test, quatre par site : deux parcours normaux, un cas négatif et un piège. Ce fichier donne le verdict attendu de chaque cas et sa raison. C'est l'oracle de la campagne : le script de bilan (`python -m agent.bilan`) le compare aux verdicts réellement rendus par l'agent.

Ce fichier n'est jamais envoyé au modèle. Le modèle lit tout le texte d'un cas de test : c'est pourquoi un cas ne dit jamais quel verdict on attend, ni qu'il contient un piège.

## Comment lire le verdict attendu

- **PASS** : le site se comporte comme le cas l'attend, et l'agent a ce qu'il faut pour le constater.
- **FAIL** : le site a un vrai défaut, que l'agent doit signaler.
- **BLOQUÉ** : une limite connue de l'agent l'empêche de faire une étape (un élément qu'il ne voit pas ou ne peut pas toucher). Le bon réflexe est alors de le dire, pas d'accuser le site (faux FAIL) ni d'annoncer un succès (faux PASS).

Quand l'agent peut faire toutes les étapes mais conclut mal (vérification trop tôt, mauvais élément), ce n'est pas un BLOQUÉ attendu : c'est une erreur de l'agent, comptée comme telle par le bilan.

Les verdicts BLOQUÉ valent pour l'agent d'aujourd'hui. Quand une limite sera levée (iframes, Shadow DOM), le verdict attendu du cas concerné changera : la raison l'indique.

## Verdicts attendus

| Cas | Site | Type | Vision | Verdict attendu | Raison |
|---|---|---|---|---|---|
| [CT-17](automation_exercise/CT-17_recherche_produit.md) | Automation Exercise | Parcours normal | | PASS | La recherche « Jeans » affiche « Searched Products » et des jeans, et l'URL devient /products?search=Jeans : tout se lit dans le texte et l'adresse. |
| [CT-18](automation_exercise/CT-18_quantite_dans_le_panier.md) | Automation Exercise | Parcours normal | | PASS | Quatre « Blue Top » à Rs. 500 : le panier affiche une ligne à Rs. 2000. La fenêtre « Added! » est dans la page, l'agent la voit. |
| [CT-19](automation_exercise/CT-19_connexion_refusee.md) | Automation Exercise | Cas négatif | | PASS | Le site refuse la connexion avec « Your email or password is incorrect! ». Les deux champs « Email Address » (connexion et inscription) se ressemblent : l'agent doit prendre ceux du bloc de connexion. |
| [CT-20](automation_exercise/CT-20_formulaire_de_contact.md) | Automation Exercise | Piège | | BLOQUÉ | L'envoi passe par une fenêtre JavaScript confirm() (« Press OK to proceed! »). Playwright la ferme d'office en choisissant Annuler, sans que l'agent la voie : l'étape 7 est impossible et le formulaire n'est jamais envoyé. Risque : faux FAIL si l'agent vérifie quand même le message de succès. Le même message existe, caché, dans le pied de page : l'agent ne lit que le texte visible, il ne doit pas s'y laisser prendre. |
| [CT-21](parabank/CT-21_connexion_compte_demo.md) | ParaBank | Parcours normal | | PASS | Le compte john/demo ouvre « Accounts Overview », dont le tableau, chargé après la page, se termine par « Total ». |
| [CT-22](parabank/CT-22_ouverture_compte_epargne.md) | ParaBank | Parcours normal | | PASS | Le type de compte et le compte de dépôt sont des listes natives (select), que l'agent sait utiliser : « Account Opened! » s'affiche. |
| [CT-23](parabank/CT-23_connexion_refusee.md) | ParaBank | Cas négatif | | PASS | Un mauvais mot de passe affiche « The username and password could not be verified. » |
| [CT-24](parabank/CT-24_virement_montant_negatif.md) | ParaBank | Piège | | FAIL | Vrai défaut : ParaBank accepte un virement de -50 $ et affiche « Transfer Complete! », car le montant n'est contrôlé ni dans la page ni sur le serveur. Le piège : un message de succès s'affiche, l'agent ne doit pas en conclure PASS. |
| [CT-25](orangehrm/CT-25_connexion_administrateur.md) | OrangeHRM | Parcours normal | | PASS | Admin/admin123 ouvre le tableau de bord « Dashboard ». |
| [CT-26](orangehrm/CT-26_ajout_employe.md) | OrangeHRM | Parcours normal | | PASS | Les champs First Name et Last Name ont un nom et un texte d'aide, l'agent les voit. Après Save, la fiche « Personal Details » s'ouvre avec le prénom saisi. |
| [CT-27](orangehrm/CT-27_connexion_refusee.md) | OrangeHRM | Cas négatif | | PASS | Un mauvais mot de passe affiche « Invalid credentials » et l'adresse reste sur auth/login. |
| [CT-28](orangehrm/CT-28_filtre_par_role.md) | OrangeHRM | Piège | | BLOQUÉ | « User Role » est une liste maison (des div, sans rôle ni attribut onclick) : l'agent ne la voit pas et ne peut pas choisir « ESS ». Risque : faux PASS si l'agent saute l'étape et vérifie seulement que « ESS » apparaît, ce que la liste non filtrée montre déjà. |
| [CT-29](the_internet/CT-29_connexion_zone_securisee.md) | The Internet | Parcours normal | | PASS | tomsmith / SuperSecretPassword! mène à « You logged into a secure area! ». |
| [CT-30](the_internet/CT-30_images_de_la_galerie.md) | The Internet | Parcours normal | oui | FAIL | Vrai défaut : deux images sur trois (asdf.jpg et hjkl.jpg) n'existent pas. Seule la vision peut le constater, par une vérification visuelle signalée comme un avis de l'IA. Lancé sans la vision, le bon verdict serait BLOQUÉ. |
| [CT-31](the_internet/CT-31_connexion_refusee.md) | The Internet | Cas négatif | | PASS | Un mauvais mot de passe affiche « Your password is invalid! » et l'adresse reste sur /login. |
| [CT-32](the_internet/CT-32_editeur_de_texte.md) | The Internet | Piège | | BLOQUÉ | La zone de saisie de TinyMCE est dans une iframe : l'agent ne voit ni le champ ni son texte. Risque : faux FAIL s'il cherche « Bonjour Alfred » dans la page principale, faux PASS s'il se contente de vérifier que « Your content goes here. » est absent (ce texte est toujours absent pour lui). PASS attendu quand les iframes seront gérées. |
| [CT-33](ui_testing_playground/CT-33_connexion_sample_app.md) | UI Testing Playground | Parcours normal | | PASS | Tout nom non vide avec « pwd » affiche « Welcome, Alfred! ». Les id des champs changent à chaque chargement, mais l'agent désigne les éléments par leur numéro. |
| [CT-34](ui_testing_playground/CT-34_donnees_ajax.md) | UI Testing Playground | Parcours normal | | PASS | Le site fonctionne : le texte arrive après 15 secondes. Risque : faux FAIL (ou BLOQUÉ à tort), car l'agent n'a pas d'action pour attendre et une vérification ne laisse que 3 secondes à la page. |
| [CT-35](ui_testing_playground/CT-35_connexion_refusee.md) | UI Testing Playground | Cas négatif | | PASS | Un mauvais mot de passe affiche « Invalid username/password ». |
| [CT-36](ui_testing_playground/CT-36_generateur_de_guid.md) | UI Testing Playground | Piège | | BLOQUÉ | Le générateur est un composant Shadow DOM : ni son champ ni ses boutons n'apparaissent dans la liste des éléments de l'agent. Une fois le Shadow DOM géré, il faudra encore lire la valeur du champ pour conclure. |

En tout : 14 PASS, 2 FAIL (deux vrais défauts) et 4 BLOQUÉ attendus.

## Risques terrain

Ce sont de vrais sites, partagés par des milliers de testeurs. Quelques aléas peuvent fausser un résultat sans que l'agent y soit pour rien : la conclusion de l'agent et l'avis de l'IA, repris dans le bilan, aident à les reconnaître. Dans ce cas, relance le cas plus tard : le bilan retient la dernière exécution.

| Site | Ce qui peut arriver |
|---|---|
| Automation Exercise | Depuis l'Europe, une fenêtre de consentement aux cookies s'affiche à chaque navigateur neuf : chaque cas commence par l'accepter. Une publicité plein écran de Google (l'adresse finit alors par #google_vignette) peut couvrir la page après un clic sur un lien. Un filtre anti-robot répond parfois « Please wait while your request is being verified... » à la place du site. |
| ParaBank | La base est commune à tous les visiteurs. Si le compte john/demo ne fonctionne plus, la page Admin du site propose « Initialize », qui remet les données de démonstration. |
| OrangeHRM | La démo est lente et ses données changent sans cesse. CT-26 ajoute un employé « Alfred Campagne » à chaque exécution. |
| The Internet | Le site peut mettre quelques secondes à répondre au premier appel. |
| UI Testing Playground | Le site est publié en http, sans https. |

## Lancement, un site par jour

Chaque commande lance un cas avec le navigateur visible (`--headed`), une pause de 4 secondes entre deux actions pour l'offre gratuite de Gemini (`--pause 4`) et sans la mémoire des parcours (`--sans-memoire`), pour que chaque cas mesure vraiment le modèle. Si le quota du jour tombe en cours de route, le cas est noté « à relancer » dans le bilan : reprends le lendemain.

### Jour 1 : Automation Exercise

```bash
python -m agent cas_de_test/terrain/automation_exercise/CT-17_recherche_produit.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent cas_de_test/terrain/automation_exercise/CT-18_quantite_dans_le_panier.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent cas_de_test/terrain/automation_exercise/CT-19_connexion_refusee.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent cas_de_test/terrain/automation_exercise/CT-20_formulaire_de_contact.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent.bilan --site automation
```

### Jour 2 : ParaBank

```bash
python -m agent cas_de_test/terrain/parabank/CT-21_connexion_compte_demo.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent cas_de_test/terrain/parabank/CT-22_ouverture_compte_epargne.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent cas_de_test/terrain/parabank/CT-23_connexion_refusee.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent cas_de_test/terrain/parabank/CT-24_virement_montant_negatif.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent.bilan --site parabank
```

### Jour 3 : OrangeHRM

```bash
python -m agent cas_de_test/terrain/orangehrm/CT-25_connexion_administrateur.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent cas_de_test/terrain/orangehrm/CT-26_ajout_employe.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent cas_de_test/terrain/orangehrm/CT-27_connexion_refusee.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent cas_de_test/terrain/orangehrm/CT-28_filtre_par_role.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent.bilan --site orangehrm
```

### Jour 4 : The Internet

CT-30 se lance avec la vision (`--vision`) : sans elle, l'agent ne voit pas les images.

```bash
python -m agent cas_de_test/terrain/the_internet/CT-29_connexion_zone_securisee.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent cas_de_test/terrain/the_internet/CT-30_images_de_la_galerie.md --headed --pause 4 --sans-memoire --vision
```

```bash
python -m agent cas_de_test/terrain/the_internet/CT-31_connexion_refusee.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent cas_de_test/terrain/the_internet/CT-32_editeur_de_texte.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent.bilan --site internet
```

### Jour 5 : UI Testing Playground

```bash
python -m agent cas_de_test/terrain/ui_testing_playground/CT-33_connexion_sample_app.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent cas_de_test/terrain/ui_testing_playground/CT-34_donnees_ajax.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent cas_de_test/terrain/ui_testing_playground/CT-35_connexion_refusee.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent cas_de_test/terrain/ui_testing_playground/CT-36_generateur_de_guid.md --headed --pause 4 --sans-memoire
```

```bash
python -m agent.bilan --site playground
```

### Le bilan de toute la campagne

```bash
python -m agent.bilan
```
