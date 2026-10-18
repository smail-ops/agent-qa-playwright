# CT-35 Connexion refusée avec un mot de passe incorrect
URL : http://uitestingplayground.com/sampleapp

## Préconditions
L'application accepte tout nom d'utilisateur non vide, mais seulement avec le mot de passe « pwd ».

## Étapes
1. Saisir « Alfred » dans le champ User Name
2. Saisir « motdepasse » dans le champ du mot de passe
3. Cliquer sur le bouton « Log In »

## Résultats attendus
- Le message « Invalid username/password » s'affiche
- Le message « Welcome, Alfred! » ne s'affiche pas
