# CT-13 Commande refusée sans code postal
URL : https://www.saucedemo.com/

## Préconditions
L'utilisateur « standard_user » existe, avec le mot de passe « secret_sauce ».

## Étapes
1. Saisir « standard_user » dans le champ Username
2. Saisir « secret_sauce » dans le champ Password
3. Cliquer sur le bouton Login
4. Ajouter le produit « Sauce Labs Backpack » au panier
5. Ouvrir le panier, puis cliquer sur « Checkout »
6. Saisir « Jean » dans First Name et « Dupont » dans Last Name
7. Laisser le champ Zip/Postal Code vide
8. Cliquer sur « Continue »

## Résultats attendus
- Le message « Postal Code is required » s'affiche
- L'utilisateur reste sur le formulaire : l'URL ne contient pas « checkout-step-two »
