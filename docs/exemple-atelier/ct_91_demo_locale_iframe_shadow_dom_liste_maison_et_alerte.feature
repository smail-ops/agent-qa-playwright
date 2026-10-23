# language: fr
# Scénario généré automatiquement par agent-qa-playwright, à partir des actions réussies de l'agent.
# Version exécutable avec Robot Framework : robot ct_91_demo_locale_iframe_shadow_dom_liste_maison_et_alerte_bdd.robot
Fonctionnalité: CT-91 Démo locale : iframe, Shadow DOM, liste maison et alerte

  @CT-91
  Scénario: CT-91 Démo locale : iframe, Shadow DOM, liste maison et alerte
    Étant donné que je suis sur la page "http://localhost:8000/atelier.html"
    Quand je saisis "Livrer avant midi" dans la zone de texte "Note de commande"
    Alors la zone de texte "Note de commande" contient "Livrer avant midi"
    Quand je clique sur le bouton "Générer un code"
    Alors le champ "Code promo" correspond au motif "^PROMO-[0-9A-F]{4}-[0-9A-F]{4}$"
    Et la page affiche "Code prêt à copier"
    Quand je clique sur le champ "Rôle"
    Et je clique sur l'option "ESS"
    Alors le champ "Rôle" contient "ESS"
    Et la page affiche "Rôle : ESS"
    Quand je clique sur le bouton "Envoyer la commande"
    Et j'accepte la fenêtre "Confirmer l'envoi de la commande ?"
    Alors la page affiche "Commande envoyée"
    Quand je clique sur le bouton "Charger le stock"
    Et j'attends jusqu'à 10 secondes que la page affiche "Stock chargé"
    Alors la page affiche "Stock chargé : 12 articles"
