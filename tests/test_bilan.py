"""Bilan de la campagne terrain : les verdicts de l'agent comparés aux verdicts attendus."""

import json
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from agent import bilan
from agent.bilan import (
    A_RELANCER,
    BLOQUE_A_TORT,
    FAUX_FAIL,
    FAUX_PASS,
    JUSTE,
    NON_LANCE,
    Execution,
    comparer,
    ecart,
    lire_campagne,
    lire_executions,
    rendu_markdown,
    rendu_terminal,
    totaux,
)
from agent.providers import ScriptedProvider
from agent.resultat import write_result
from agent.runner import STOPPED_SUMMARY, Agent
from agent.testcase import parse_test_case
from alfred.server import case_code

TERRAIN = Path(__file__).resolve().parent.parent / "cas_de_test" / "terrain"
CAMPAGNE = TERRAIN / "campagne.md"
SITES = {  # site de la campagne : adresse de ses cas
    "Automation Exercise": "automationexercise.com",
    "ParaBank": "parabank.parasoft.com",
    "OrangeHRM": "opensource-demo.orangehrmlive.com",
    "The Internet": "the-internet.herokuapp.com",
    "UI Testing Playground": "uitestingplayground.com",
}

MINI = """# Campagne d'essai

| Cas | Site | Type | Vision | Verdict attendu | Raison |
|---|---|---|---|---|---|
| CT-17 | Site A | Parcours normal | | PASS | Le site fonctionne. |
| [CT-18](site_a/CT-18_editeur.md) | Site A | Piège | | **BLOQUÉ** | Iframe. |
| CT-19 | Site B | Parcours normal | oui | fail | Vrai défaut | avec une barre. |

Texte après le tableau.
"""


def execution(code="CT-17", statut="PASS", **champs) -> Execution:
    champs.setdefault("dossier", f"20261019-100000_{code.lower()}")
    champs.setdefault("debut", datetime(2026, 10, 19, 10, 0))
    return Execution(code=code, statut=statut, **champs)


# --- La vraie campagne -------------------------------------------------------------------


def test_campagne_terrain_vingt_cas_quatre_par_site():
    texte = CAMPAGNE.read_text(encoding="utf-8")
    cas = lire_campagne(texte)
    assert [c.code for c in cas] == [f"CT-{numero}" for numero in range(17, 37)]
    assert Counter(c.site for c in cas) == {site: 4 for site in SITES}
    for site in SITES:  # deux parcours normaux, un cas négatif et un piège par site
        assert Counter(c.type for c in cas if c.site == site) == {"Parcours normal": 2, "Cas négatif": 1, "Piège": 1}
    verdicts = Counter(c.verdict for c in cas)
    assert verdicts["FAIL"] >= 1 and verdicts["BLOQUE"] >= 1  # au moins un vrai défaut, et les limites connues
    assert f"En tout : {verdicts['PASS']} PASS, {verdicts['FAIL']} FAIL" in texte
    assert f"et {verdicts['BLOQUE']} BLOQUÉ attendus" in texte
    assert [c.code for c in cas if c.vision] == ["CT-30"]
    assert all(c.raison and c.fichier for c in cas)


def test_chaque_cas_de_la_campagne_a_son_fichier():
    cas = lire_campagne(CAMPAGNE.read_text(encoding="utf-8"))
    fichiers = sorted(path.resolve() for path in TERRAIN.rglob("CT-*.md"))
    assert sorted((CAMPAGNE.parent / c.fichier).resolve() for c in cas) == fichiers  # ni lien cassé, ni cas oublié
    for c in cas:
        test_case = parse_test_case((CAMPAGNE.parent / c.fichier).read_text(encoding="utf-8"))
        assert case_code(test_case.title) == c.code
        assert urlsplit(test_case.url).hostname == SITES[c.site], c.code


@pytest.mark.parametrize("fichier", sorted(TERRAIN.rglob("CT-*.md")), ids=lambda path: path.name[:5])
def test_un_cas_ne_trahit_pas_son_verdict(fichier):
    """Le modèle lit tout le texte du cas : il ne doit y trouver ni le verdict attendu, ni l'annonce d'un piège."""
    texte = fichier.read_text(encoding="utf-8")
    assert not re.search(r"\b(PASS|FAIL|BLOQU[EÉ])\b", texte)
    assert not re.search(r"verdict|piège|défaut|bug", texte, flags=re.IGNORECASE)


def test_bilan_de_la_campagne_sans_execution(tmp_path, capsys):
    assert bilan.main(["--campagne", str(CAMPAGNE), "--rapports", str(tmp_path / "vide")]) == 0
    texte = (tmp_path / "vide" / "bilan_campagne.md").read_text(encoding="utf-8")
    assert "| Cas exécutés | 0 sur 20 |" in texte and "Aucun cas exécuté pour l'instant." in texte
    assert "CT-17, CT-18, CT-19" in texte  # la liste des cas non lancés
    assert "Non lancés       : 20" in capsys.readouterr().out


# --- Le fichier de campagne --------------------------------------------------------------


def test_lecture_d_une_campagne():
    cas = lire_campagne(MINI)
    assert [(c.code, c.site, c.type, c.verdict, c.vision) for c in cas] == [
        ("CT-17", "Site A", "Parcours normal", "PASS", False),
        ("CT-18", "Site A", "Piège", "BLOQUE", False),
        ("CT-19", "Site B", "Parcours normal", "FAIL", True),
    ]
    assert cas[1].fichier == "site_a/CT-18_editeur.md" and cas[0].fichier == ""
    assert cas[2].raison == "Vrai défaut | avec une barre."  # une barre dans la raison ne décale pas les colonnes


@pytest.mark.parametrize("texte, message", [
    ("# Campagne\n\nPas encore de tableau.\n", "tableau des verdicts introuvable"),
    ("| Cas | Verdict attendu |\n|---|---|\n| CT-17 | PASS |\n", "colonne(s) absente(s)"),
    (MINI.replace("| PASS |", "| PEUT-ÊTRE |"), "verdict « PEUT-ÊTRE » inconnu"),
    (MINI.replace("CT-19", "CT-17"), "le cas CT-17 est en double"),
    (MINI.replace("| CT-17 |", "| sans code |"), "identifiant du cas introuvable"),
    (MINI.replace("| CT-17 | Site A |", "| CT-17 | |"), "le site du cas CT-17 est vide"),
    (MINI.split("| CT-17")[0], "aucun cas"),
], ids=["sans tableau", "colonnes absentes", "verdict inconnu", "doublon", "sans identifiant", "site vide",
        "tableau vide"])
def test_campagne_invalide(texte, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        lire_campagne(texte)


# --- La comparaison ----------------------------------------------------------------------


@pytest.mark.parametrize("attendu, obtenu, conclusion, resultat", [
    ("PASS", "PASS", "", JUSTE),
    ("FAIL", "FAIL", "", JUSTE),
    ("BLOQUE", "BLOQUE", "Élément introuvable après deux essais.", JUSTE),
    ("FAIL", "PASS", "", FAUX_PASS),
    ("BLOQUE", "PASS", "", FAUX_PASS),
    ("PASS", "FAIL", "", FAUX_FAIL),
    ("BLOQUE", "FAIL", "", FAUX_FAIL),
    ("PASS", "BLOQUE", "3 erreurs de suite : l'agent s'arrête.", BLOQUE_A_TORT),
    ("FAIL", "BLOQUE", "Nombre maximal d'actions atteint (20).", BLOQUE_A_TORT),
    ("PASS", "BLOQUE", "Le modèle n'a pas pu répondre : quota du jour épuisé", A_RELANCER),
    ("BLOQUE", "BLOQUE", STOPPED_SUMMARY, A_RELANCER),
])
def test_classement_d_une_execution(attendu, obtenu, conclusion, resultat):
    assert ecart(attendu, execution(statut=obtenu, conclusion=conclusion)) == resultat


def test_cas_sans_execution():
    assert ecart("PASS", None) == NON_LANCE


def test_execution_retenue_et_periode():
    campagne = lire_campagne(MINI)
    quota = "Le modèle n'a pas pu répondre : quota du jour épuisé"
    executions = [
        execution("CT-17", "FAIL", debut=datetime(2026, 10, 19, 9, 0), dossier="20261019-090000_ct_17"),
        execution("CT-17", "PASS", debut=datetime(2026, 10, 20, 9, 0), dossier="20261020-090000_ct_17"),
        execution("CT-17", "BLOQUE", conclusion=quota, debut=datetime(2026, 10, 21, 9, 0),
                  dossier="20261021-090000_ct_17"),
        execution("CT-18", "BLOQUE", conclusion=quota, debut=datetime(2026, 10, 21, 9, 0)),
        execution("CT-99", "PASS"),  # hors campagne : ignoré
    ]
    lignes = {ligne.cas.code: ligne for ligne in comparer(campagne, executions)}
    # La plus récente qui a donné un verdict : l'exécution coupée par le quota ne la remplace pas
    assert lignes["CT-17"].execution.dossier == "20261020-090000_ct_17" and lignes["CT-17"].ecart == JUSTE
    assert lignes["CT-17"].remarques == ["3 exécutions, celle du 20/10 à 09:00 est retenue"]
    assert lignes["CT-18"].ecart == A_RELANCER and lignes["CT-18"].remarques == [f"interrompue : {quota}"]
    assert lignes["CT-19"].ecart == NON_LANCE and lignes["CT-19"].execution is None

    avant = {ligne.cas.code: ligne for ligne in comparer(campagne, executions, jusqua=date(2026, 10, 19))}
    assert avant["CT-17"].ecart == FAUX_FAIL and avant["CT-18"].ecart == NON_LANCE
    apres = comparer(campagne, executions, depuis=date(2026, 10, 21))
    assert [ligne.ecart for ligne in apres] == [A_RELANCER, A_RELANCER, NON_LANCE]
    assert [ligne.cas.code for ligne in comparer(campagne, executions, site="site-b")] == ["CT-19"]


def test_bilan_relit_les_resultats_ecrits_par_l_agent(pw_page, demo_url, tmp_path):
    """Les vrais resultat.json : un verdict de l'agent, puis une exécution coupée par le quota du jour."""
    rapports = tmp_path / "rapports"
    case = parse_test_case(f"# CT-17 Page d'accueil\nURL : {demo_url}/index.html\n")
    reussite = [{"action": "check_text", "args": {"text": "Boutique Démo"}},
                {"action": "finish", "args": {"status": "PASS", "summary": "Accueil affiché"}}]
    premier = Agent(ScriptedProvider(reussite)).run(pw_page, case, rapports / "20261019-100000_ct_17_page_d_accueil")
    write_result(premier)
    coupe = Agent(ScriptedProvider([{"error": "rate_limit", "daily": True}])).run(
        pw_page, case, rapports / "20261019-110000_ct_17_page_d_accueil")
    write_result(coupe)
    assert coupe.status == "BLOQUE" and coupe.summary.startswith(bilan.MODEL_ERROR)

    executions = lire_executions(rapports)
    assert [(e.code, e.statut, e.interrompue, e.appels) for e in executions] == [
        ("CT-17", "PASS", False, 2), ("CT-17", "BLOQUE", True, 0)]
    ligne = comparer(lire_campagne(MINI), executions)[0]
    assert ligne.ecart == JUSTE and ligne.execution.dossier == "20261019-100000_ct_17_page_d_accueil"


# --- Le rendu et la ligne de commande ----------------------------------------------------


def test_totaux_et_bilan_markdown(tmp_path):
    modele = "gemini (gemini-flash-latest)"
    executions = [
        execution("CT-17", "PASS", appels=10, duree_s=90.0, modele=modele, memoire=4),
        execution("CT-18", "FAIL", appels=12, duree_s=150.0, modele=modele,
                  conclusion="Le texte « Bonjour Alfred » est absent.",
                  echecs=["Texte « Bonjour Alfred » absent de la page"],
                  diagnostic={"nature": "bug", "gravite": "majeure"}),
        execution("CT-19", "FAIL", appels=6, duree_s=30.0, modele=modele, conclusion="Deux images cassées.",
                  echecs=["Avis de l'IA sur la capture d'écran (non conforme) : deux images cassées"]),
    ]
    lignes = comparer(lire_campagne(MINI), executions)
    total = totaux(lignes)
    assert (total.executes, total.justes, total.faux_fail, total.faux_pass, total.appels, total.duree_s) == (
        3, 2, 1, 0, 28, 270.0)

    rapports = tmp_path / "rapports"
    texte = rendu_markdown(lignes, campagne=Path("cas_de_test/terrain/campagne.md"), rapports=rapports,
                           sortie=rapports / "bilan_campagne.md", maintenant=datetime(2026, 10, 23, 18, 5))
    assert "Bilan du 23/10/2026 à 18:05 : campagne `cas_de_test/terrain/campagne.md`" in texte
    assert "| Verdicts justes | 2 sur 3 (67 %) |" in texte
    assert "| Faux PASS | 0 |" in texte and "| Faux FAIL | 1 |" in texte
    assert "| Appels au modèle | 28, soit 9,3 par cas |" in texte
    assert "| Durée des exécutions | 4 min 30 s, soit 1 min 30 s par cas |" in texte
    assert f"| Modèle | {modele} |" in texte
    assert "| BLOQUÉ | 0 | 1 | 0 |" in texte  # BLOQUÉ attendu, FAIL obtenu
    assert "| Site A | 2 sur 2 | 1 | 0 | 1 | 0 | 0 | 22 | 4 min 00 s |" in texte
    assert "| [CT-17](20261019-100000_ct-17/rapport.md) | Site A |" in texte  # lien vers le rapport de l'exécution
    assert "rejouée(s) depuis la mémoire" in texte
    # L'écart, avec ce qu'il faut pour l'analyser
    assert "### CT-18 Site A : faux FAIL" in texte
    assert "- **Attendu** : BLOQUÉ. Iframe." in texte
    assert "- **Obtenu** : FAIL. Le texte « Bonjour Alfred » est absent." in texte
    assert "- **Vérifications en échec** : Texte « Bonjour Alfred » absent de la page" in texte
    assert "\n\n\n" not in texte
    assert "- **Avis de l'IA** : bug de l'application (à confirmer)" in texte
    # Un FAIL juste reste à confirmer : la bonne raison, et la vision qui manquait
    assert "### CT-19 Site B : FAIL attendu et obtenu" in texte and "lancé sans --vision" in texte

    terminal = rendu_terminal(lignes)
    assert "Verdicts justes  : 2 sur 3 (67 %)" in terminal
    assert re.search(r"CT-18 +Site A +attendu BLOQUÉ +obtenu FAIL +12 appels +2 min 30 s +❌ faux FAIL", terminal)


def test_ligne_de_commande(tmp_path, capsys):
    campagne = tmp_path / "campagne.md"
    campagne.write_text(MINI, encoding="utf-8")
    rapports = tmp_path / "rapports"
    dossier = rapports / "20261019-100000_ct_17_page"
    dossier.mkdir(parents=True)
    (dossier / "resultat.json").write_text(json.dumps({
        "titre": "CT-17 Page", "statut": "PASS", "conclusion": "Page affichée", "debut": "2026-10-19T10:00:00",
        "duree_s": 42.0, "appels_modele": 7, "modele": "gemini (gemini-flash-latest)"}), encoding="utf-8")
    illisible = rapports / "20261019-110000_ct_18_page"
    illisible.mkdir()
    (illisible / "resultat.json").write_text("{pas du json", encoding="utf-8")

    assert bilan.main(["--campagne", str(campagne), "--rapports", str(rapports)]) == 0
    sortie = rapports / "bilan_campagne.md"
    assert "| Cas exécutés | 1 sur 3 |" in sortie.read_text(encoding="utf-8")
    sortie_terminal = capsys.readouterr().out
    assert "✅ juste" in sortie_terminal and f"Bilan complet : {sortie}" in sortie_terminal

    autre = tmp_path / "bilan_site_b.md"
    assert bilan.main(["--campagne", str(campagne), "--rapports", str(rapports), "--site", "site b",
                       "--sortie", str(autre)]) == 0
    texte = autre.read_text(encoding="utf-8")
    assert "| Cas exécutés | 0 sur 1 |" in texte and "site « site b »" in texte


@pytest.mark.parametrize("options, message", [
    (["--campagne", "absente.md"], "fichier de campagne illisible"),
    (["--site", "inconnu"], "aucun site de la campagne ne correspond à « inconnu » (sites : Site A, Site B)"),
    (["--depuis", "19/10/2026"], "--depuis attend une date au format AAAA-MM-JJ"),
    (["--depuis", "2026-10-22", "--jusqua", "2026-10-19"], "la date de --depuis doit précéder celle de --jusqua"),
])
def test_ligne_de_commande_erreurs(tmp_path, capsys, options, message):
    campagne = tmp_path / "campagne.md"
    campagne.write_text(MINI, encoding="utf-8")
    assert bilan.main(["--campagne", str(campagne), "--rapports", str(tmp_path / "rapports"), *options]) == 3
    assert message in capsys.readouterr().err
    assert not (tmp_path / "rapports" / "bilan_campagne.md").exists()
