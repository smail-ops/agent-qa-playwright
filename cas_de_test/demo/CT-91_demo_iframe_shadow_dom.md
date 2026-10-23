# CT-91 Démo locale : iframe, Shadow DOM, liste maison et alerte
URL : http://localhost:8000/atelier.html

## Étapes
1. Saisir « Livrer avant midi » dans la note de commande (un éditeur placé dans un cadre)
2. Cliquer sur le bouton « Générer un code » du code promo
3. Ouvrir la liste « Rôle » et choisir « ESS »
4. Cliquer sur « Envoyer la commande », puis confirmer l'envoi en cliquant sur « OK »
5. Cliquer sur « Charger le stock » et attendre la fin du chargement, qui prend quelques secondes

## Résultats attendus
- La note de commande contient « Livrer avant midi »
- Le code promo suit le format PROMO-XXXX-XXXX (chiffres, et lettres de A à F)
- Le message « Code prêt à copier » s'affiche
- La liste « Rôle » indique « ESS », et « Rôle : ESS » s'affiche
- « Commande envoyée » s'affiche
- « Stock chargé : 12 articles » s'affiche
