"""Conversation libre avec Alfred : contexte envoyé au modèle, réponse et action contrôlées."""

import json
from datetime import datetime

from alfred.conversation import (
    build_context,
    build_message,
    converse,
    parse_reply,
    redact,
    secrets_of,
)

CAS = [
    {"id": "demo/CT-00_demo_locale.md", "code": "CT-00", "title": "CT-00 Démo locale", "url": "http://localhost:8000/index.html",
     "host": "localhost:8000", "text": "Saisir « secret_sauce » dans le champ Mot de passe", "demo": True, "memory": None},
    {"id": "CT-01_connexion_valide.md", "code": "CT-01", "title": "CT-01 Connexion avec un utilisateur valide",
     "url": "https://www.saucedemo.com/", "host": "www.saucedemo.com",
     "text": "L'utilisateur « standard_user » existe, avec le mot de passe « secret_sauce ».", "memory": {"steps": 4}},
    {"id": "US-01_connexion.feature#1", "code": "US-01", "title": "US-01 Connexion : Connexion réussie", "gherkin": True,
     "url": "https://www.saucedemo.com/", "host": "www.saucedemo.com", "text": ""},
    {"id": "US-01_connexion.feature#2", "code": "US-01", "title": "US-01 Connexion : Mot de passe incorrect", "gherkin": True,
     "url": "https://www.saucedemo.com/", "host": "www.saucedemo.com", "text": ""},
]
RESULTATS = [
    {"titre": "CT-14 Commande avec l'utilisateur problem_user", "statut": "FAIL", "debut": "2026-10-17T23:41:05",
     "dossier": "20261017-234105_ct_14", "fichiers": {"rapport": "rapport.md", "anomalie": "anomalie.md"},
     "conclusion": "Le nom de famille n'est pas gardé.", "verifications_echouees": ["Texte « Dupont » absent de la page"],
     "diagnostic": {"nature": "bug", "gravite": "majeure", "titre": "Le nom de famille est effacé",
                    "explication": "Le champ Last Name se vide."}},
    {"titre": "CT-01 Connexion avec un utilisateur valide", "statut": "PASS", "debut": "2026-10-17T23:30:00",
     "appels_modele": 0, "etapes_rejouees_depuis_la_memoire": 4, "dossier": "20261017-233000_ct_01",
     "fichiers": {"rapport": "rapport.md", "pytest": "test_ct_01.py"}},
]


def reply(action, reponse="Très bien.", cases_dir=None, tmp_path=None):
    raw = json.dumps({"reponse": reponse, "action": action}, ensure_ascii=False)
    return parse_reply(raw, CAS, cases_dir or tmp_path, "https://www.saucedemo.com/", {"secret_sauce"}, RESULTATS)


def test_contexte_envoye_au_modele():
    context = build_context(CAS, RESULTATS, selection=CAS[1], mission=None, maitre="Smail", files=["rapport", "anomalie"],
                            now=datetime(2026, 10, 18, 9, 5))
    lines = context.splitlines()
    assert lines[:5] == ["Date : dimanche 18 octobre 2026, 09:05", "Maître (le testeur) : Smail",
                         "Site par défaut : https://www.saucedemo.com/",
                         "Cas affiché à l'écran : CT-01 CT-01 Connexion avec un utilisateur valide", "Mission en cours : aucune"]
    assert "- CT-01 | Connexion avec un utilisateur valide | www.saucedemo.com | parcours en mémoire, rejoué sans IA" in lines
    # Deux scénarios partagent le code US-01 : le modèle reçoit leurs identifiants
    assert "- US-01_connexion.feature#2 | US-01 Connexion : Mot de passe incorrect | www.saucedemo.com | scénario Gherkin" in lines
    echec = next(line for line in lines if "CT-14" in line)
    assert "FAIL" in echec and "Texte « Dupont » absent de la page" in echec and "Le nom de famille est effacé" in echec
    assert "rejoué depuis la mémoire, sans IA" in next(line for line in lines if line.startswith("- 17/10 23:30"))
    assert lines[-1] == "Fichiers de la dernière mission : rapport, anomalie"
    assert "secret_sauce" not in context  # le texte des cas n'est pas envoyé


def test_message_avec_les_derniers_echanges():
    history = [{"role": "testeur", "texte": f"phrase {i}"} for i in range(8)] + [{"role": "alfred", "texte": "Bien."}]
    message = build_message("et le suivant ?", history, "CONTEXTE")
    assert message.startswith("CONTEXTE\n\nÉchanges précédents :")
    assert "phrase 2" not in message and "Testeur : phrase 3" in message and "Alfred : Bien." in message
    assert message.endswith("Phrase du testeur : « et le suivant ? »")


def test_lancer_et_selectionner(tmp_path):
    lancer = reply({"type": "lancer", "cas": "ct-1", "url": "https://recette.saucedemo.com/", "vision": True}, tmp_path=tmp_path)
    assert lancer["action"] == {"type": "lancer", "cas": "CT-01_connexion_valide.md", "code": "CT-01",
                                "url": "https://recette.saucedemo.com/", "vision": True}
    choix = reply({"type": "selectionner", "cas": "US-01_connexion.feature#2"}, tmp_path=tmp_path)
    assert choix["action"]["cas"] == "US-01_connexion.feature#2"


def test_actions_refusees(tmp_path):
    inconnu = reply({"type": "lancer", "cas": "CT-99"}, "Je lance CT-99.", tmp_path=tmp_path)
    assert inconnu == {"reponse": "Je ne trouve pas le cas CT-99 parmi vos cas de test.", "action": {"type": "aucune"}}
    ambigu = reply({"type": "lancer", "cas": "US-01"}, tmp_path=tmp_path)
    assert ambigu["action"]["type"] == "aucune" and "Plusieurs scénarios portent le code US-01" in ambigu["reponse"]
    adresse = reply({"type": "lancer", "cas": "CT-01", "url": "ftp://recette.fr"}, tmp_path=tmp_path)
    assert adresse["action"]["type"] == "aucune" and "adresse" in adresse["reponse"]
    assert reply({"type": "ouvrir", "fichier": ".env"}, tmp_path=tmp_path)["action"] == {"type": "aucune"}
    assert reply({"type": "supprimer", "cas": "CT-01"}, tmp_path=tmp_path)["action"] == {"type": "aucune"}
    assert reply("lancer", tmp_path=tmp_path)["action"] == {"type": "aucune"}


def test_fichiers_des_resultats_recents(tmp_path):
    """« Ouvre la fiche d'anomalie » : la plus récente ; « le rapport de CT-01 » : celui de ce cas."""
    anomalie = reply({"type": "ouvrir", "fichier": "anomalie"}, tmp_path=tmp_path)["action"]
    assert anomalie == {"type": "ouvrir", "fichier": "anomalie", "url": "/rapports/20261017-234105_ct_14/anomalie.md",
                        "titre": "CT-14 Commande avec l'utilisateur problem_user"}
    rapport = reply({"type": "ouvrir", "fichier": "rapport", "cas": "ct 1"}, tmp_path=tmp_path)["action"]
    assert rapport["url"] == "/rapports/20261017-233000_ct_01/rapport.md"
    absent = reply({"type": "ouvrir", "fichier": "gherkin"}, tmp_path=tmp_path)
    assert absent == {"reponse": "Je n'ai pas encore le scénario Gherkin à vous montrer.", "action": {"type": "aucune"}}


def test_cas_dicte(tmp_path):
    (tmp_path / "CT-03_panier.md").write_text("# CT-03 Panier\nURL : https://www.saucedemo.com/\n", encoding="utf-8")
    dicte = reply({"type": "rediger", "titre": "Commande sans code postal",
                   "etapes": ["Se connecter avec « standard_user »", "Ouvrir le panier, puis cliquer sur « Checkout »"],
                   "resultats": ["Le message « Error: Postal Code is required » s'affiche"]},
                  "Voici le cas. Dites vas-y pour le lancer.", tmp_path=tmp_path)
    action = dicte["action"]
    assert action["type"] == "rediger" and action["titre"] == "CT-04 Commande sans code postal"
    assert action["markdown"].startswith("# CT-04 Commande sans code postal\nURL : https://www.saucedemo.com/\n")
    raté = reply({"type": "rediger", "titre": "Vague"}, tmp_path=tmp_path)
    assert raté["action"]["type"] == "aucune" and "Précisez-moi les étapes" in raté["reponse"]
    propose = reply({"type": "proposer"}, tmp_path=tmp_path)
    assert propose["action"] == {"type": "proposer", "url": "https://www.saucedemo.com/"}
    domaine = reply({"type": "proposer", "url": "demoblaze.com"}, tmp_path=tmp_path)  # « analyse demoblaze.com »
    assert domaine["action"] == {"type": "proposer", "url": "https://demoblaze.com"}


def test_jamais_de_mot_de_passe_prononce(tmp_path):
    assert secrets_of([c["text"] for c in CAS]) == {"secret_sauce"}
    dit = reply({"type": "aucune"}, "Le mot de passe est **secret_sauce**, Monsieur.", tmp_path=tmp_path)
    assert dit["reponse"] == "Le mot de passe est le mot de passe, Monsieur."
    assert redact("SECRET_SAUCE", {"secret_sauce"}) == "le mot de passe"
    long = reply({"type": "aucune"}, "mot " * 200, tmp_path=tmp_path)["reponse"]
    assert len(long) <= 450 and long.endswith("…")


def test_une_requete_au_modele(tmp_path):
    sent = {}

    class Faux:
        name, model = "faux", "faux"

        def complete(self, system, user, images=(), max_tokens=None):
            sent.update(system=system, user=user)
            return '{"reponse": "Le dernier test, CT-14, a échoué.", "action": {"type": "ouvrir", "fichier": "anomalie"}}'

    answer = converse(Faux(), "pourquoi le dernier test a échoué ?", [], "CONTEXTE", CAS, tmp_path, "https://www.saucedemo.com/",
                      results=RESULTATS)
    assert answer["reponse"] == "Le dernier test, CT-14, a échoué."
    assert answer["action"]["url"] == "/rapports/20261017-234105_ct_14/anomalie.md"
    assert "Tu es Alfred" in sent["system"] and sent["user"].endswith("« pourquoi le dernier test a échoué ? »")
