"""Lecture d'un cas de test écrit en Markdown, dans un format proche de Squash TM.

Format attendu :

    # CT-01 Connexion avec un utilisateur valide
    URL : https://www.saucedemo.com/

    ## Étapes
    1. ...

    ## Résultats attendus
    - ...

Seuls le titre, l'URL et deux lignes facultatives sont lus par le programme.
Le reste du texte est transmis tel quel au modèle d'IA, qui l'interprète comme
le ferait un testeur.

Les deux lignes facultatives servent à ne se connecter qu'une fois :

    Enregistrer la session : saucedemo    (le cas de connexion, une fois réussi, garde la session)
    Session : saucedemo                   (ce cas commence déjà connecté, avec cette session)

La session (cookies et stockage local du site) est rangée dans le dossier
sessions/, exclu de Git : elle donne accès au compte de test.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path

_SESSION = re.compile(r"^\s*Session\s*:\s*(\S+)\s*$", re.MULTILINE | re.IGNORECASE)
_SAVE_SESSION = re.compile(r"^\s*Enregistrer\s+la\s+session\s*:\s*(\S+)\s*$", re.MULTILINE | re.IGNORECASE)
SESSION_NAME = re.compile(r"[A-Za-z0-9][\w-]{0,39}")


@dataclass
class TestCase:
    """Un cas de test : son titre, son URL de départ et son texte complet."""

    __test__ = False  # empêche pytest de prendre cette classe pour une classe de test

    title: str
    url: str
    text: str
    session: str | None = None  # le cas commence avec cette session (déjà connecté)
    save_session: str | None = None  # le cas, une fois réussi, enregistre la session sous ce nom

    @property
    def slug(self) -> str:
        """Version du titre utilisable dans un nom de fichier ou de fonction."""
        return slugify(self.title)

    def with_url(self, url: str) -> TestCase:
        """Le même cas de test sur une autre adresse : autre site, recette, adresse IP.

        La ligne « URL : » du texte change aussi, pour que le modèle lise la bonne adresse.
        Le fichier du cas de test n'est pas modifié.
        """
        url = url.strip()
        if not re.fullmatch(r"https?://[^\s/?#]+\S*", url, flags=re.IGNORECASE):
            raise ValueError("L'adresse à tester doit commencer par http:// ou https://, par exemple https://www.saucedemo.com/")
        text = re.sub(r"^(\s*URL\s*:\s*)\S+", lambda m: m.group(1) + url, self.text, count=1,
                      flags=re.MULTILINE | re.IGNORECASE)
        return replace(self, url=url, text=text)


def session_names(text: str) -> tuple[str | None, str | None]:
    """Les lignes « Session : nom » et « Enregistrer la session : nom » du cas : (session reprise, session enregistrée)."""
    names = []
    for pattern in (_SESSION, _SAVE_SESSION):
        match = pattern.search(text)
        if match and not SESSION_NAME.fullmatch(match.group(1)):
            raise ValueError(f"Nom de session invalide : « {match.group(1)} ». Utilise des lettres, des chiffres, - ou _ "
                             "(40 caractères au plus), par exemple : Session : saucedemo")
        names.append(match.group(1) if match else None)
    return names[0], names[1]


def slugify(text: str, max_length: int = 60) -> str:
    """Transforme « CT-01 Connexion réussie » en « ct_01_connexion_reussie »."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_text).strip("_").lower()
    return slug[:max_length].rstrip("_") or "cas_de_test"


def parse_test_case(text: str) -> TestCase:
    """Extrait le titre (première ligne « # ») et l'URL (ligne « URL : »)."""
    title_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    url_match = re.search(r"^\s*URL\s*:\s*(\S+)", text, flags=re.MULTILINE | re.IGNORECASE)
    if not title_match:
        raise ValueError("Titre introuvable : la première ligne doit commencer par « # ».")
    if not url_match:
        raise ValueError("URL introuvable : ajoute une ligne « URL : https://... ».")
    session, save_session = session_names(text)
    return TestCase(title=title_match.group(1).strip(), url=url_match.group(1), text=text.strip(), session=session,
                    save_session=save_session)


def load_test_case(path: str | Path) -> TestCase:
    """Lit un fichier Markdown de cas de test."""
    return parse_test_case(Path(path).read_text(encoding="utf-8"))
