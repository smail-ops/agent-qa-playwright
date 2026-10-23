"""Reconnaissance du maître : à qui Alfred parle, sur cet ordinateur.

Le prénom est enregistré dans le fichier .env de l'ordinateur (ALFRED_MAITRE).
Chaque installation connaît donc son propre maître, quel que soit le réseau :
une adresse IP change d'un Wi-Fi à l'autre, la session de l'ordinateur non.
Au premier lancement, Alfred demande le prénom, en proposant celui du compte
de la session.

Au démarrage, Alfred salue son maître à voix haute avec la voix du Mac
(commande say). Le navigateur, lui, ne peut pas parler avant un premier clic.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

ENV_KEY = "ALFRED_MAITRE"
CREATEUR = "Smail Zaich"  # auteur du projet (voir le fichier LICENSE)
VOICE_KEY = "ALFRED_VOIX"  # nom d'une voix du Mac, ou « off » pour la couper
NAME_PATTERN = re.compile(r"[^\W\d_](?:[\w' .-]{0,38}[^\W\d_])?")  # lettres, espaces, apostrophes, traits d'union


def nom_du_maitre() -> str | None:
    """Prénom enregistré pour cet ordinateur, s'il y en a un."""
    return (os.getenv(ENV_KEY) or "").strip() or None


def suggestion() -> str | None:
    """Prénom probable, tiré du nom complet du compte de la session (macOS ou Linux)."""
    try:
        import pwd

        complet = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
    except (ImportError, KeyError, AttributeError):
        return None
    mot = complet.split()[0] if complet else ""
    return mot.capitalize() if NAME_PATTERN.fullmatch(mot) else None


def enregistrer(nom: str, env_path: Path) -> str:
    """Enregistre le prénom dans le fichier .env de cet ordinateur, sans toucher aux autres lignes."""
    nom = " ".join(str(nom).split())
    if not NAME_PATTERN.fullmatch(nom):
        raise ValueError("Indiquez un prénom de 1 à 40 lettres (espaces, apostrophes et traits d'union acceptés).")
    lignes = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    lignes = [ligne for ligne in lignes if not ligne.startswith(f"{ENV_KEY}=")]
    lignes.append(f"{ENV_KEY}={nom}")
    env_path.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    os.environ[ENV_KEY] = nom
    return nom


def salutation(nom: str | None, maintenant: datetime | None = None) -> str:
    """Ce qu'Alfred dit en arrivant : il se présente et salue son maître."""
    heure = (maintenant or datetime.now()).hour
    mot = "Bonsoir" if heure >= 18 or heure < 5 else "Bonjour"
    if nom:
        return f"{mot} {nom}. Ici Alfred, votre assistant de test. Quel cas de test dois-je exécuter ?"
    return f"{mot}. Je suis Alfred, votre assistant de test. Comment dois-je vous appeler ?"


def identite(nom: str | None) -> str:
    """Ce qu'Alfred répond à « qui es-tu ? », sans IA : la même réponse, avec ou sans clé d'API."""
    createur = nom is not None and nom.split()[0].lower() == CREATEUR.split()[0].lower()
    auteur = "que vous avez conçu" if createur else f"conçu par {CREATEUR}"
    return (f"Je suis Alfred, le majordome de l'agent de test {auteur}. Je lance vos cas de test dans un vrai navigateur, "
            "je vous commente chaque étape, puis je vous remets le verdict, le rapport et les tests automatisés : pytest, "
            "Robot Framework et Gherkin. Posez-moi vos questions, par exemple : pourquoi le dernier test a échoué ?")


def choisir_voix(liste: str) -> str | None:
    """Choisit une voix française dans la sortie de « say -v ? », Thomas de préférence."""
    voix = []
    for ligne in liste.splitlines():
        trouve = re.match(r"^(.+?)\s+(fr_[A-Z]{2})\b", ligne)
        if trouve:
            voix.append((trouve.group(1).strip(), trouve.group(2)))
    for nom, langue in voix:
        if nom.startswith("Thomas") and langue == "fr_FR":
            return nom
    francais = [nom for nom, langue in voix if langue == "fr_FR"] or [nom for nom, _ in voix]
    return francais[0] if francais else None


def saluer_a_voix_haute(texte: str) -> str:
    """Prononce la salutation avec la voix du Mac. Renvoie ce qui s'est passé, pour le terminal."""
    reglage = (os.getenv(VOICE_KEY) or "").strip()
    if reglage.lower() == "off":
        return "voix coupée (ALFRED_VOIX=off)"
    if not shutil.which("say"):
        return "pas de voix système sur cet ordinateur (la commande say n'existe que sur Mac)"
    voix = reglage
    if not voix:
        try:
            liste = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            liste = ""
        voix = choisir_voix(liste)
    if not voix:
        return "aucune voix française installée : Réglages Système, Accessibilité, Contenu énoncé"
    subprocess.Popen(["say", "-v", voix, texte], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"voix {voix}"
