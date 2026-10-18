# CT-23 Connexion refusée avec un mot de passe incorrect
URL : https://parabank.parasoft.com/parabank/index.htm

## Préconditions
Le client de démonstration « john » existe, avec le mot de passe « demo ».

## Étapes
1. Dans le bloc « Customer Login », saisir « john » dans le champ Username
2. Saisir « mauvais_mot_de_passe » dans le champ Password
3. Cliquer sur le bouton « Log In »

## Résultats attendus
- Le message « The username and password could not be verified. » s'affiche
- La page des comptes ne s'affiche pas : le texte « Accounts Overview » est absent
