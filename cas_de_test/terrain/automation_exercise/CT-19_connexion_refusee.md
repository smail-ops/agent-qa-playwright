# CT-19 Connexion refusée avec des identifiants inconnus
URL : https://automationexercise.com/login

## Préconditions
Aucun compte n'existe avec l'adresse « alfred.campagne@example.com ».

## Étapes
1. Si une fenêtre de consentement aux cookies s'affiche, cliquer sur « Consent » (ou « Autoriser »)
2. Dans le bloc « Login to your account », saisir « alfred.campagne@example.com » dans le champ Email Address
3. Dans le même bloc, saisir « MauvaisMotDePasse1 » dans le champ Password
4. Cliquer sur le bouton « Login »

## Résultats attendus
- Le message « Your email or password is incorrect! » s'affiche
- L'utilisateur n'est pas connecté : le texte « Logged in as » ne s'affiche pas
