# CT-31 Connexion refusée avec un mot de passe incorrect
URL : https://the-internet.herokuapp.com/login

## Préconditions
L'utilisateur « tomsmith » existe, avec le mot de passe « SuperSecretPassword! ».

## Étapes
1. Saisir « tomsmith » dans le champ Username
2. Saisir « MauvaisMotDePasse! » dans le champ Password
3. Cliquer sur le bouton « Login »

## Résultats attendus
- Le message « Your password is invalid! » s'affiche
- L'utilisateur reste sur la page de connexion : l'URL contient « /login » et ne contient pas « /secure »
