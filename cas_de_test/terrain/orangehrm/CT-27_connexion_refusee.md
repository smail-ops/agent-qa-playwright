# CT-27 Connexion refusée avec un mot de passe incorrect
URL : https://opensource-demo.orangehrmlive.com/web/index.php/auth/login

## Préconditions
Le compte de démonstration « Admin » existe, avec le mot de passe « admin123 ».

## Étapes
1. Saisir « Admin » dans le champ Username
2. Saisir « mauvais123 » dans le champ Password
3. Cliquer sur le bouton « Login »

## Résultats attendus
- Le message « Invalid credentials » s'affiche
- L'utilisateur reste sur la page de connexion : l'URL contient « auth/login »
