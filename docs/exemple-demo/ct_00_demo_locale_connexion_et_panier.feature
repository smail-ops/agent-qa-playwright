# language: fr
# Scénario généré automatiquement par agent-qa-playwright, à partir des actions réussies de l'agent.
# Version exécutable avec Robot Framework : robot ct_00_demo_locale_connexion_et_panier_bdd.robot
Fonctionnalité: CT-00 Démo locale : connexion et panier

  @CT-00
  Scénario: CT-00 Démo locale : connexion et panier
    Étant donné que je suis sur la page "http://localhost:8000/index.html"
    Quand je saisis "standard_user" dans le champ "Identifiant"
    Et je saisis le mot de passe "MOT_DE_PASSE_LOCALHOST" dans le champ "Mot de passe"
    Et je clique sur le bouton "Connexion"
    Alors l'URL contient "inventory"
    Et la page affiche "Produits"
    Quand je clique sur le bouton "Ajouter au panier" n°1
    Alors la page affiche "Panier (1)"
