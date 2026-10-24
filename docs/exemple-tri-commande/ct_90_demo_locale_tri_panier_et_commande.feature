# language: fr
# Scénario généré automatiquement par agent-qa-playwright, à partir des actions réussies de l'agent.
# Version exécutable avec Robot Framework : robot ct_90_demo_locale_tri_panier_et_commande_bdd.robot
Fonctionnalité: CT-90 Démo locale : tri, panier et commande

  @CT-90
  Scénario: CT-90 Démo locale : tri, panier et commande
    Étant donné que je suis sur la page "http://localhost:8000/index.html"
    Quand je saisis "standard_user" dans le champ "Identifiant"
    Et je saisis le mot de passe "MOT_DE_PASSE_LOCALHOST" dans le champ "Mot de passe"
    Et je clique sur le bouton "Connexion"
    Alors la page affiche 3 fois "Ajouter au panier"
    Quand je choisis "Prix croissant" dans la liste "Trier"
    Alors la page affiche dans cet ordre : "Lampe de vélo", "T-shirt" et "Sac à dos"
    Quand je clique sur le bouton "Ajouter au panier" n°1
    Alors la page affiche 1 fois "Retirer"
    Quand je clique sur le lien "Commander"
    Alors le bouton "Continuer" est grisé
    Quand je clique sur la case "J'accepte les conditions générales de vente"
    Alors la case "J'accepte les conditions générales de vente" est cochée
    Et le bouton "Continuer" est actif
    Et le champ "Code postal" est vide
