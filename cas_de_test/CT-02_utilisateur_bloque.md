# CT-02 Connexion refusée pour un utilisateur bloqué
URL : https://www.saucedemo.com/

## Préconditions
L'utilisateur « locked_out_user » est bloqué. Son mot de passe est « secret_sauce ».

## Étapes
1. Saisir « locked_out_user » dans le champ Username
2. Saisir « secret_sauce » dans le champ Password
3. Cliquer sur le bouton Login

## Résultats attendus
- Le message « Sorry, this user has been locked out. » s'affiche
- L'utilisateur reste sur la page de connexion : l'URL ne contient pas « inventory »
