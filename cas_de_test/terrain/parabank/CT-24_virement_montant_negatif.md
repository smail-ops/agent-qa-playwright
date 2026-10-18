# CT-24 Virement d'un montant négatif
URL : https://parabank.parasoft.com/parabank/index.htm

## Préconditions
Le client de démonstration « john » existe, avec le mot de passe « demo », et possède au moins un compte.

## Étapes
1. Dans le bloc « Customer Login », saisir « john » dans le champ Username
2. Saisir « demo » dans le champ Password
3. Cliquer sur le bouton « Log In »
4. Cliquer sur le lien « Transfer Funds »
5. Saisir « -50 » dans le champ Amount
6. Garder les comptes proposés dans les listes « From account # » et « to account # »
7. Cliquer sur le bouton « Transfer »

## Résultats attendus
- Le virement est refusé : le message « Transfer Complete! » ne s'affiche pas
- Un message d'erreur indique que le montant doit être positif
