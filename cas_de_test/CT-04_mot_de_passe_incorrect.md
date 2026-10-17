# CT-04 Connexion refusée avec un mot de passe incorrect
URL : https://www.saucedemo.com/

## Préconditions
L'utilisateur « standard_user » existe, avec le mot de passe « secret_sauce ».

## Étapes
1. Saisir « standard_user » dans le champ Username
2. Saisir « mauvais_mot_de_passe » dans le champ Password
3. Cliquer sur le bouton Login

## Résultats attendus
- Le message « Username and password do not match any user in this service » s'affiche
- L'utilisateur reste sur la page de connexion : l'URL ne contient pas « inventory »
