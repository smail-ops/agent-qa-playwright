"""Bilan d'une campagne : les verdicts de l'agent comparés aux verdicts attendus.

Le fichier de campagne (par défaut cas_de_test/terrain/campagne.md) donne, dans
un tableau Markdown, le verdict attendu de chaque cas et sa raison. Ce module
relit les resultat.json du dossier des rapports, retient pour chaque cas sa
dernière exécution, puis compte :

- les verdicts justes ;
- les faux PASS : l'agent conclut PASS alors qu'on attendait FAIL ou BLOQUÉ.
  C'est un défaut manqué, ou un succès annoncé sans pouvoir le constater :
  l'erreur la plus grave, car personne ne relit un test au vert ;
- les faux FAIL : l'agent conclut FAIL alors qu'on attendait PASS ou BLOQUÉ.
  Il accuse le site à tort, et un testeur perd du temps à le vérifier ;
- les BLOQUÉ à tort : l'agent ne conclut pas alors qu'on attendait PASS ou FAIL ;
- les appels au modèle et la durée des exécutions.

Une exécution interrompue (quota du jour épuisé, réseau coupé, arrêt demandé)
n'est pas un verdict de l'agent : le cas est noté « à relancer ».

Exemples :
    python -m agent.bilan
    python -m agent.bilan --site parabank
    python -m agent.bilan --jusqua 2026-10-22 --sortie rapports/bilan_avant.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .anomalie import NATURES
from .resultat import FILE as RESULT_FILE
from .runner import STOPPED_SUMMARY

CAMPAGNE = Path("cas_de_test") / "terrain" / "campagne.md"
RAPPORTS = Path("rapports")
SORTIE = "bilan_campagne.md"  # écrit dans le dossier des rapports, sauf --sortie
MODEL_ERROR = "Le modèle n'a pas pu"  # début de la conclusion quand le modèle n'a pas répondu (voir runner.py)

VERDICTS = ("PASS", "FAIL", "BLOQUE")
LIBELLES = {"PASS": "PASS", "FAIL": "FAIL", "BLOQUE": "BLOQUÉ"}

JUSTE = "juste"
FAUX_PASS = "faux PASS"
FAUX_FAIL = "faux FAIL"
BLOQUE_A_TORT = "BLOQUÉ à tort"
A_RELANCER = "à relancer"
NON_LANCE = "non lancé"
ERREURS = (FAUX_PASS, FAUX_FAIL, BLOQUE_A_TORT)  # les erreurs de l'agent, détaillées dans le bilan
MARQUES = {JUSTE: "✅", FAUX_PASS: "❌", FAUX_FAIL: "❌", BLOQUE_A_TORT: "⚠️", A_RELANCER: "⏸️", NON_LANCE: "·"}

_CODE = re.compile(r"\b([A-Z]{2,}-\d+)\b")
_TITLE_CODE = re.compile(r"^\s*([A-Z]{2,}-\d+)\b")
_LINK = re.compile(r"\]\(([^)\s]+)\)")
_FOLDER_DATE = re.compile(r"^(\d{8}-\d{6})_")
_SEPARATOR = re.compile(r":?-{3,}:?")

# --- Le fichier de campagne ----------------------------------------------------------------


@dataclass
class CasAttendu:
    """Un cas de la campagne et le verdict qu'on attend de l'agent."""

    code: str
    site: str
    type: str
    verdict: str  # PASS, FAIL ou BLOQUE
    raison: str
    vision: bool = False  # le cas se lance avec --vision
    fichier: str = ""  # lien vers le fichier du cas, relatif au fichier de campagne


def _sans_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _cle(text: str) -> str:
    """Forme de comparaison : « UI Testing Playground » et « ui-testing playground » donnent « uitestingplayground »."""
    return re.sub(r"[^a-z0-9]", "", _sans_accents(text).lower())


def _cellule(text: str) -> str:
    """Le texte d'une cellule, sans la mise en forme Markdown (gras, code)."""
    return " ".join(text.replace("**", "").replace("`", "").split())


def _cellules(ligne: str) -> list[str]:
    return [cell.strip() for cell in ligne.strip().strip("|").split("|")]


def verdict(text: str) -> str | None:
    """« PASS », « **BLOQUÉ** » ou « bloque » donnent PASS ou BLOQUE ; None si ce n'est pas un verdict."""
    value = _sans_accents(_cellule(text)).upper()
    return value if value in VERDICTS else None


_COLONNES = {"cas": "cas", "site": "site", "type": "type", "vision": "vision", "verdictattendu": "verdict",
             "raison": "raison"}
_OBLIGATOIRES = ("cas", "site", "type", "verdict", "raison")


def _entete(lignes: list[str]) -> tuple[int, list[str]]:
    """La ligne d'en-tête du tableau des verdicts : sa position et le nom de ses colonnes."""
    for position, ligne in enumerate(lignes):
        if ligne.lstrip().startswith("|"):
            noms = [_cle(_cellule(cell)) for cell in _cellules(ligne)]
            if "cas" in noms and "verdictattendu" in noms:
                return position, noms
    raise ValueError("tableau des verdicts introuvable : il faut une ligne d'en-tête avec les colonnes "
                     "« Cas » et « Verdict attendu ».")


def lire_campagne(texte: str) -> list[CasAttendu]:
    """Les cas du tableau des verdicts attendus.

    Colonnes : Cas, Site, Type, Vision (facultative), Verdict attendu, Raison.
    La cellule « Cas » peut être un lien vers le fichier du cas : [CT-17](site/CT-17_x.md).
    """
    lignes = texte.splitlines()
    debut, noms = _entete(lignes)
    colonnes = {_COLONNES[nom]: index for index, nom in enumerate(noms) if nom in _COLONNES}
    manquantes = [nom for nom in _OBLIGATOIRES if nom not in colonnes]
    if manquantes:
        raise ValueError(f"colonne(s) absente(s) du tableau des verdicts : {', '.join(manquantes)}.")

    cas: list[CasAttendu] = []
    for numero, ligne in enumerate(lignes[debut + 1 :], debut + 2):
        if not ligne.lstrip().startswith("|"):
            break  # fin du tableau
        cellules = _cellules(ligne)
        if all(_SEPARATOR.fullmatch(cell) for cell in cellules if cell):
            continue  # ligne |---|---|
        if len(cellules) > len(noms):  # une barre dans le texte de la dernière colonne : on la garde
            cellules = cellules[: len(noms) - 1] + [" | ".join(cellules[len(noms) - 1 :])]
        if len(cellules) < len(noms):
            raise ValueError(f"ligne {numero} : {len(cellules)} colonnes au lieu de {len(noms)}.")
        valeurs = {nom: _cellule(cellules[index]) for nom, index in colonnes.items()}

        code = _CODE.search(cellules[colonnes["cas"]])
        if code is None:
            raise ValueError(f"ligne {numero} : identifiant du cas introuvable (par exemple CT-17).")
        if any(c.code == code.group(1) for c in cas):
            raise ValueError(f"ligne {numero} : le cas {code.group(1)} est en double.")
        attendu = verdict(valeurs["verdict"])
        if attendu is None:
            raise ValueError(f"ligne {numero} : verdict « {valeurs['verdict']} » inconnu (PASS, FAIL ou BLOQUÉ).")
        if not valeurs["site"]:
            raise ValueError(f"ligne {numero} : le site du cas {code.group(1)} est vide.")
        lien = _LINK.search(cellules[colonnes["cas"]])
        cas.append(CasAttendu(code.group(1), valeurs["site"], valeurs["type"], attendu, valeurs["raison"],
                              vision=_cle(valeurs.get("vision", "")) in ("oui", "yes", "x"),
                              fichier=lien.group(1) if lien else ""))
    if not cas:
        raise ValueError("le tableau des verdicts ne contient aucun cas.")
    return cas


# --- Les exécutions (resultat.json) ----------------------------------------------------------


@dataclass
class Execution:
    """Une exécution d'un cas, lue dans son resultat.json."""

    dossier: str  # nom du dossier du rapport : 20261019-101500_ct_17_...
    code: str
    statut: str  # PASS, FAIL ou BLOQUE
    conclusion: str = ""
    debut: datetime | None = None
    duree_s: float = 0.0  # exécution par l'agent, sans la rédaction de la fiche d'anomalie
    appels: int = 0  # appels au modèle, diagnostic de la fiche d'anomalie compris
    vision: bool = False
    memoire: int = 0  # étapes rejouées depuis la mémoire, sans le modèle
    echecs: list[str] = field(default_factory=list)  # vérifications en échec
    diagnostic: dict | None = None  # avis de l'IA sur l'échec
    modele: str = ""

    @property
    def interrompue(self) -> bool:
        """Arrêt demandé, ou modèle muet (quota du jour, réseau) : ce n'est pas un verdict de l'agent."""
        coupee = self.conclusion == STOPPED_SUMMARY or self.conclusion.startswith(MODEL_ERROR)
        return self.statut == "BLOQUE" and coupee


def _entier(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _reel(value: object) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _debut(data: dict, dossier: str) -> datetime | None:
    try:  # heure locale, comme le nom du dossier : un fuseau horaire éventuel est ignoré pour comparer les dates
        return datetime.fromisoformat(str(data.get("debut") or "")).replace(tzinfo=None)
    except ValueError:
        stamp = _FOLDER_DATE.match(dossier)  # le nom du dossier commence par la date
        return datetime.strptime(stamp.group(1), "%Y%m%d-%H%M%S") if stamp else None


def execution_depuis(data: object, dossier: str) -> Execution | None:
    """Une exécution à partir du contenu d'un resultat.json, ou None s'il n'est pas exploitable."""
    if not isinstance(data, dict):
        return None
    code = _TITLE_CODE.match(str(data.get("titre") or ""))
    statut = verdict(str(data.get("statut") or ""))
    if code is None or statut is None:
        return None
    echecs = data.get("verifications_echouees")
    diagnostic = data.get("diagnostic")
    return Execution(
        dossier=dossier,
        code=code.group(1),
        statut=statut,
        conclusion=" ".join(str(data.get("conclusion") or "").split()),
        debut=_debut(data, dossier),
        duree_s=_reel(data.get("duree_s")),
        appels=_entier(data.get("appels_modele")),
        vision=data.get("vision") is True,
        memoire=_entier(data.get("etapes_rejouees_depuis_la_memoire")),
        echecs=[str(e) for e in echecs] if isinstance(echecs, list) else [],
        diagnostic=diagnostic if isinstance(diagnostic, dict) else None,
        modele=str(data.get("modele") or ""),
    )


def _ordre(execution: Execution) -> tuple[datetime, str]:
    return execution.debut or datetime.min, execution.dossier


def lire_executions(dossier: str | Path) -> list[Execution]:
    """Les exécutions qui ont un resultat.json lisible, de la plus ancienne à la plus récente."""
    executions = []
    for chemin in Path(dossier).glob(f"*/{RESULT_FILE}"):
        try:
            data = json.loads(chemin.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        execution = execution_depuis(data, chemin.parent.name)
        if execution is not None:
            executions.append(execution)
    return sorted(executions, key=_ordre)


# --- La comparaison ------------------------------------------------------------------------


def ecart(attendu: str, execution: Execution | None) -> str:
    """Juste, faux PASS, faux FAIL, BLOQUÉ à tort, à relancer ou non lancé."""
    if execution is None:
        return NON_LANCE
    if execution.interrompue:
        return A_RELANCER
    if execution.statut == attendu:
        return JUSTE
    if execution.statut == "PASS":
        return FAUX_PASS
    if execution.statut == "FAIL":
        return FAUX_FAIL
    return BLOQUE_A_TORT


def retenue(executions: list[Execution]) -> Execution | None:
    """La plus récente qui a donné un verdict ; à défaut, la plus récente interrompue."""
    candidates = [e for e in executions if not e.interrompue] or executions
    return max(candidates, key=_ordre) if candidates else None


@dataclass
class Ligne:
    """Un cas de la campagne, son exécution retenue et l'écart avec le verdict attendu."""

    cas: CasAttendu
    execution: Execution | None
    executions: int  # exécutions trouvées pour ce cas dans la période choisie
    ecart: str
    remarques: list[str] = field(default_factory=list)


def _remarques(cas: CasAttendu, execution: Execution | None, nombre: int) -> list[str]:
    if execution is None:
        return []
    remarques = []
    if nombre > 1:
        quand = f" du {execution.debut:%d/%m à %H:%M}" if execution.debut else ""
        remarques.append(f"{nombre} exécutions, celle{quand} est retenue")
    if execution.interrompue:
        remarques.append(f"interrompue : {execution.conclusion}")
    if cas.vision and not execution.vision:
        remarques.append("lancé sans --vision, alors que le cas en a besoin")
    if execution.memoire:
        remarques.append(f"{execution.memoire} étape(s) rejouée(s) depuis la mémoire : relancer avec --sans-memoire "
                         "pour mesurer le modèle")
    return remarques


def correspond(site: str, filtre: str | None) -> bool:
    """« parabank » correspond à « ParaBank », « playground » à « UI Testing Playground »."""
    return filtre is None or _cle(filtre) in _cle(site)


def comparer(campagne: list[CasAttendu], executions: list[Execution], *, depuis: date | None = None,
             jusqua: date | None = None, site: str | None = None) -> list[Ligne]:
    """Chaque cas de la campagne (ou d'un seul site), avec son exécution retenue dans la période choisie."""
    par_code: dict[str, list[Execution]] = {}
    for execution in executions:
        jour = execution.debut.date() if execution.debut else None
        if (depuis or jusqua) and jour is None:
            continue
        if (depuis and jour < depuis) or (jusqua and jour > jusqua):
            continue
        par_code.setdefault(execution.code, []).append(execution)
    lignes = []
    for cas in campagne:
        if not correspond(cas.site, site):
            continue
        trouvees = par_code.get(cas.code, [])
        execution = retenue(trouvees)
        lignes.append(Ligne(cas, execution, len(trouvees), ecart(cas.verdict, execution),
                            _remarques(cas, execution, len(trouvees))))
    return lignes


@dataclass
class Totaux:
    cas: int = 0
    executes: int = 0  # cas avec un verdict de l'agent (hors exécutions interrompues)
    justes: int = 0
    faux_pass: int = 0
    faux_fail: int = 0
    bloques_a_tort: int = 0
    a_relancer: int = 0
    non_lances: int = 0
    appels: int = 0
    duree_s: float = 0.0


def totaux(lignes: list[Ligne]) -> Totaux:
    total = Totaux(cas=len(lignes))
    compteurs = {JUSTE: "justes", FAUX_PASS: "faux_pass", FAUX_FAIL: "faux_fail", BLOQUE_A_TORT: "bloques_a_tort",
                 A_RELANCER: "a_relancer", NON_LANCE: "non_lances"}
    for ligne in lignes:
        setattr(total, compteurs[ligne.ecart], getattr(total, compteurs[ligne.ecart]) + 1)
        if ligne.ecart not in (A_RELANCER, NON_LANCE):
            total.executes += 1
            total.appels += ligne.execution.appels
            total.duree_s += ligne.execution.duree_s
    return total


# --- Le rendu ------------------------------------------------------------------------------


def duree(secondes: float) -> str:
    """45 s, 2 min 07 s, 1 h 05 min."""
    total = int(secondes + 0.5)
    if total < 60:
        return f"{total} s"
    if total < 3600:
        return f"{total // 60} min {total % 60:02d} s"
    return f"{total // 3600} h {total % 3600 // 60:02d} min"


def _decimal(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _part(nombre: int, total: int) -> str:
    return f"{nombre} sur {total} ({int(100 * nombre / total + 0.5)} %)" if total else "aucun cas exécuté"


def _moyenne(total: Totaux) -> tuple[str, str]:
    """Appels et durée, avec leur moyenne par cas exécuté."""
    if not total.executes:
        return "aucun", "aucune"
    return (f"{total.appels}, soit {_decimal(total.appels / total.executes)} par cas",
            f"{duree(total.duree_s)}, soit {duree(total.duree_s / total.executes)} par cas")


def _md(text: object) -> str:
    """Texte placé dans une cellule de tableau Markdown."""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _lien(chemin: Path, depuis: Path) -> str:
    """Chemin relatif, avec des barres obliques, d'un fichier du rapport vu depuis le fichier du bilan."""
    return os.path.relpath(chemin, depuis).replace(os.sep, "/")


def _obtenu(ligne: Ligne) -> str:
    return LIBELLES[ligne.execution.statut] if ligne.execution else "-"


def rendu_markdown(lignes: list[Ligne], *, campagne: Path, rapports: Path, sortie: Path, site: str | None = None,
                   depuis: date | None = None, jusqua: date | None = None, maintenant: datetime | None = None) -> str:
    """Le bilan complet, en Markdown."""
    total = totaux(lignes)
    maintenant = maintenant or datetime.now()
    portee = [f"campagne `{campagne.as_posix()}`", f"rapports `{rapports.as_posix()}`"]
    if site:
        portee.append(f"site « {site} »")
    if depuis:
        portee.append(f"exécutions à partir du {depuis:%d/%m/%Y}")
    if jusqua:
        portee.append(f"exécutions jusqu'au {jusqua:%d/%m/%Y}")
    appels, temps = _moyenne(total)
    modeles = sorted({ligne.execution.modele for ligne in lignes
                      if ligne.ecart not in (A_RELANCER, NON_LANCE) and ligne.execution.modele})
    out = [
        "# Bilan de la campagne",
        "",
        f"Bilan du {maintenant:%d/%m/%Y à %H:%M} : " + ", ".join(portee) + ".",
        "",
        "| Indicateur | Valeur |",
        "|---|---|",
        f"| Cas exécutés | {total.executes} sur {total.cas} |",
        f"| Verdicts justes | {_part(total.justes, total.executes)} |",
        f"| Faux PASS | {total.faux_pass} |",
        f"| Faux FAIL | {total.faux_fail} |",
        f"| BLOQUÉ à tort | {total.bloques_a_tort} |",
        f"| À relancer (quota, réseau, arrêt) | {total.a_relancer} |",
        f"| Non lancés | {total.non_lances} |",
        f"| Appels au modèle | {appels} |",
        f"| Durée des exécutions | {temps} |",
        f"| Modèle | {_md(', '.join(modeles)) or 'aucun'} |",
        "",
        "- **Faux PASS** : l'agent conclut PASS alors qu'on attendait FAIL ou BLOQUÉ (défaut manqué, ou succès annoncé "
        "sans pouvoir le constater).",
        "- **Faux FAIL** : l'agent conclut FAIL alors qu'on attendait PASS ou BLOQUÉ (il accuse le site à tort).",
        "- **BLOQUÉ à tort** : l'agent ne conclut pas alors qu'on attendait PASS ou FAIL.",
        "- Les appels au modèle comptent aussi le diagnostic de la fiche d'anomalie. La durée est celle de l'exécution "
        "par l'agent, sans la rédaction de la fiche.",
        "",
        "## Verdicts attendus et obtenus",
        "",
        "| Attendu | Obtenu PASS | Obtenu FAIL | Obtenu BLOQUÉ |",
        "|---|---|---|---|",
    ]
    jugees = [ligne for ligne in lignes if ligne.ecart not in (A_RELANCER, NON_LANCE)]
    for attendu in VERDICTS:
        comptes = [sum(1 for ligne in jugees if ligne.cas.verdict == attendu and ligne.execution.statut == obtenu)
                   for obtenu in VERDICTS]
        out.append(f"| {LIBELLES[attendu]} | " + " | ".join(str(nombre) for nombre in comptes) + " |")

    out += ["", "## Par site", "", "| Site | Exécutés | Justes | Faux PASS | Faux FAIL | BLOQUÉ à tort | À relancer "
            "| Appels | Durée |", "|---|---|---|---|---|---|---|---|---|"]
    for nom in dict.fromkeys(ligne.cas.site for ligne in lignes):
        sous_total = totaux([ligne for ligne in lignes if ligne.cas.site == nom])
        out.append(f"| {_md(nom)} | {sous_total.executes} sur {sous_total.cas} | {sous_total.justes} | "
                   f"{sous_total.faux_pass} | {sous_total.faux_fail} | {sous_total.bloques_a_tort} | "
                   f"{sous_total.a_relancer} | {sous_total.appels} | {duree(sous_total.duree_s)} |")

    out += ["", "## Par cas", "", "| Cas | Site | Type | Attendu | Obtenu | Résultat | Appels | Durée | Remarques |",
            "|---|---|---|---|---|---|---|---|---|"]
    for ligne in lignes:
        execution = ligne.execution
        cas = ligne.cas.code
        if execution is not None:
            cas = f"[{cas}]({_lien(rapports / execution.dossier / 'rapport.md', sortie.parent)})"
        appels_cas = str(execution.appels) if execution else ""
        duree_cas = duree(execution.duree_s) if execution else ""
        out.append(f"| {cas} | {_md(ligne.cas.site)} | {_md(ligne.cas.type)} | {LIBELLES[ligne.cas.verdict]} | "
                   f"{_obtenu(ligne)} | {MARQUES[ligne.ecart]} {ligne.ecart} | {appels_cas} | {duree_cas} | "
                   f"{_md(' ; '.join(ligne.remarques))} |")

    erreurs = [ligne for ligne in lignes if ligne.ecart in ERREURS]
    out += ["", "## Écarts à analyser", ""]
    if not jugees:
        out.append("Aucun cas exécuté pour l'instant.")
    elif not erreurs:
        out.append("Aucun écart : chaque verdict obtenu est le verdict attendu.")
    for ligne in erreurs:
        out += _detail(ligne, rapports, sortie, titre=f"### {ligne.cas.code} {ligne.cas.site} : {ligne.ecart}")

    a_confirmer = [ligne for ligne in lignes if ligne.ecart == JUSTE and ligne.cas.verdict != "PASS"]
    if a_confirmer:
        out += ["", "## Verdicts justes à confirmer", "",
                "Le verdict est le bon : vérifie qu'il l'est pour la bonne raison.", ""]
        for ligne in a_confirmer:
            titre = f"### {ligne.cas.code} {ligne.cas.site} : {LIBELLES[ligne.cas.verdict]} attendu et obtenu"
            out += _detail(ligne, rapports, sortie, titre=titre)

    relancer = [ligne for ligne in lignes if ligne.ecart == A_RELANCER]
    if relancer:
        out += ["", "## À relancer", ""]
        out += [f"- **{ligne.cas.code}** ({ligne.cas.site}) : {ligne.execution.conclusion}" for ligne in relancer]
    non_lances = [ligne.cas.code for ligne in lignes if ligne.ecart == NON_LANCE]
    if non_lances:
        out += ["", "## Non lancés", "", ", ".join(non_lances) + "."]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out).strip()) + "\n"  # une seule ligne vide entre deux blocs


def _detail(ligne: Ligne, rapports: Path, sortie: Path, titre: str) -> list[str]:
    """Pour analyser un cas : la raison attendue, la conclusion de l'agent, ses vérifications, l'avis de l'IA."""
    execution = ligne.execution
    out = [titre, "",
           f"- **Attendu** : {LIBELLES[ligne.cas.verdict]}. {ligne.cas.raison}",
           f"- **Obtenu** : {LIBELLES[execution.statut]}. {execution.conclusion or 'Aucune conclusion.'}"]
    if execution.echecs:
        out.append(f"- **Vérifications en échec** : {' ; '.join(execution.echecs)}")
    if execution.diagnostic and execution.diagnostic.get("nature") in NATURES:
        out.append(f"- **Avis de l'IA** : {NATURES[execution.diagnostic['nature']].lower()} (à confirmer)")
    if ligne.remarques:
        out.append(f"- **Remarques** : {' ; '.join(ligne.remarques)}")
    rapport = _lien(rapports / execution.dossier / "rapport.md", sortie.parent)
    out += [f"- **Rapport** : [{execution.dossier}]({rapport})", ""]
    return out


def rendu_terminal(lignes: list[Ligne]) -> str:
    """Le bilan en quelques lignes, pour le terminal."""
    total = totaux(lignes)
    largeur = max((len(ligne.cas.site) for ligne in lignes), default=0)
    out = []
    for ligne in lignes:
        execution = ligne.execution
        mesure = f"{execution.appels:>3} appels  {duree(execution.duree_s):>11}" if execution else " " * 23
        out.append(f"{ligne.cas.code:<6} {ligne.cas.site:<{largeur}}  attendu {LIBELLES[ligne.cas.verdict]:<6}  "
                   f"obtenu {_obtenu(ligne):<6}  {mesure}  {MARQUES[ligne.ecart]} {ligne.ecart}")
    appels, temps = _moyenne(total)
    out += [
        "",
        f"Cas exécutés     : {total.executes} sur {total.cas}",
        f"Verdicts justes  : {_part(total.justes, total.executes)}",
        f"Faux PASS        : {total.faux_pass}",
        f"Faux FAIL        : {total.faux_fail}",
        f"BLOQUÉ à tort    : {total.bloques_a_tort}",
        f"À relancer       : {total.a_relancer}",
        f"Non lancés       : {total.non_lances}",
        f"Appels au modèle : {appels}",
        f"Durée            : {temps}",
    ]
    return "\n".join(out)


# --- Ligne de commande ---------------------------------------------------------------------


def _jour(texte: str | None, option: str) -> date | None:
    if texte is None:
        return None
    try:
        return date.fromisoformat(texte)
    except ValueError:
        raise ValueError(f"{option} attend une date au format AAAA-MM-JJ, par exemple 2026-10-19.") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent.bilan",
        description="Bilan d'une campagne : les verdicts de l'agent comparés aux verdicts attendus.",
    )
    parser.add_argument("--campagne", default=str(CAMPAGNE),
                        help=f"fichier de campagne ({CAMPAGNE.as_posix()} par défaut)")
    parser.add_argument("--rapports", default=str(RAPPORTS), help="dossier des rapports (rapports par défaut)")
    parser.add_argument("--site", help="ne garder qu'un site de la campagne, par exemple parabank")
    parser.add_argument("--depuis", help="ne compter que les exécutions à partir de cette date (AAAA-MM-JJ)")
    parser.add_argument("--jusqua", help="ne compter que les exécutions jusqu'à cette date incluse (AAAA-MM-JJ)")
    parser.add_argument("--sortie",
                        help=f"fichier du bilan en Markdown ({SORTIE} dans le dossier des rapports par défaut)")
    args = parser.parse_args(argv)

    campagne_path, rapports = Path(args.campagne), Path(args.rapports)
    try:
        texte = campagne_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Erreur : fichier de campagne illisible ({exc}). Lance la commande depuis la racine du projet, "
              "ou indique le fichier avec --campagne.", file=sys.stderr)
        return 3
    try:
        campagne = lire_campagne(texte)
        depuis, jusqua = _jour(args.depuis, "--depuis"), _jour(args.jusqua, "--jusqua")
        if depuis and jusqua and depuis > jusqua:
            raise ValueError("la date de --depuis doit précéder celle de --jusqua.")
        if args.site and not any(correspond(cas.site, args.site) for cas in campagne):
            sites = ", ".join(dict.fromkeys(cas.site for cas in campagne))
            raise ValueError(f"aucun site de la campagne ne correspond à « {args.site} » (sites : {sites}).")
    except ValueError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 3

    lignes = comparer(campagne, lire_executions(rapports), depuis=depuis, jusqua=jusqua, site=args.site)
    sortie = Path(args.sortie) if args.sortie else rapports / SORTIE
    bilan = rendu_markdown(lignes, campagne=campagne_path, rapports=rapports, sortie=sortie, site=args.site,
                           depuis=depuis, jusqua=jusqua)
    try:
        sortie.parent.mkdir(parents=True, exist_ok=True)
        sortie.write_text(bilan, encoding="utf-8")
    except OSError as exc:
        print(f"Erreur : impossible d'écrire le bilan dans {sortie} ({exc}).", file=sys.stderr)
        return 3
    print(rendu_terminal(lignes))
    print(f"\nBilan complet : {sortie}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
