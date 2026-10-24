"""Toute une campagne en une commande, avec reprise après le quota.

    python -m agent.campagne --headed --pause 4         toute la campagne
    python -m agent.campagne --site parabank            un seul site
    python -m agent.campagne --nouvelle                 repart de zéro
    python -m agent.campagne --attendre                 attend le retour du quota au lieu de s'arrêter

Les cas du fichier de campagne (cas_de_test/terrain/campagne.md par défaut) sont lancés l'un
après l'autre, sans la mémoire des parcours (pour mesurer le modèle), avec la vision quand le
tableau la demande. Chaque cas qui a déjà un verdict dans cette campagne est passé.

Si le quota du jour de l'API tombe au milieu, la campagne s'arrête proprement : la même commande,
relancée plus tard, reprend au cas interrompu. Le fichier rapports/campagne_en_cours.json garde la
date de début de la campagne en cours ; il disparaît quand tous les cas ont un verdict. Avec
--attendre, la commande patiente jusqu'au renouvellement du quota (minuit en Californie), puis
continue seule : l'ordinateur doit rester allumé (sur Mac : caffeinate -i python -m agent.campagne --attendre).

À la fin, le bilan de la campagne est écrit dans rapports/bilan_campagne.md (voir bilan.py).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from .__main__ import add_browser_options, browser_from, load_cases, show_events
from .bilan import (CAMPAGNE, LIBELLES, MARQUES, RAPPORTS, SORTIE, CasAttendu, comparer, correspond, duree, ecart,
                    lire_campagne, lire_executions, rendu_markdown, rendu_terminal, retenue)
from .execution import memory_for, new_out_dir, run_case
from .navigateur import NavigateurAbsent
from .providers import ProviderError, make_provider, quota_reset_time

ETAT = "campagne_en_cours.json"  # dans le dossier des rapports
RETRY_WAIT_S = 60  # le modèle ne répond plus (réseau, quota par minute) : une pause, puis un nouvel essai
RESET_MARGIN_S = 120  # après le renouvellement du quota, une petite marge
EXIT_QUOTA = 4  # code de sortie quand la campagne s'arrête sur le quota : elle reprendra plus tard


def seconds_until_reset(now: datetime | None = None) -> float:
    """Secondes jusqu'au renouvellement des quotas journaliers de Google (minuit en Californie), marge comprise."""
    now = now or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        pacific = now.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:  # base des fuseaux horaires absente : on réessaie dans une heure
        return 3600.0
    reset = (pacific + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (reset - pacific).total_seconds() + RESET_MARGIN_S


def case_path(campagne: Path, cas: CasAttendu) -> Path | None:
    """Le fichier du cas, d'après le lien de la cellule « Cas » (relatif au fichier de campagne)."""
    return (campagne.parent / cas.fichier).resolve() if cas.fichier else None


# --- La campagne en cours ----------------------------------------------------------------


def lire_etat(rapports: Path) -> dict | None:
    try:
        data = json.loads((rapports / ETAT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("debut") else None


def debut_campagne(rapports: Path, campagne: Path, site: str | None, nouvelle: bool = False,
                   depuis: datetime | None = None, maintenant: datetime | None = None) -> tuple[datetime, bool]:
    """Le début de la campagne : (date et heure, reprise d'une campagne commencée ?).

    Une campagne en cours pour le même fichier et le même site est reprise ; sinon, une nouvelle commence
    maintenant (ou à la date de --depuis). Le début est noté dans rapports/campagne_en_cours.json.
    """
    etat = lire_etat(rapports)
    same = etat is not None and etat.get("campagne") == campagne.as_posix() and etat.get("site") == site
    if depuis is None and not nouvelle and same:
        try:
            return datetime.fromisoformat(etat["debut"]), True
        except ValueError:
            pass
    debut = depuis or (maintenant or datetime.now()).replace(microsecond=0)
    rapports.mkdir(parents=True, exist_ok=True)
    (rapports / ETAT).write_text(json.dumps({"campagne": campagne.as_posix(), "site": site,
                                             "debut": debut.isoformat(timespec="seconds")}, ensure_ascii=False, indent=2)
                                 + "\n", encoding="utf-8")
    return debut, False


def a_faire(campagne: list[CasAttendu], rapports: Path, debut: datetime, site: str | None) -> list[tuple[CasAttendu, object]]:
    """Chaque cas de la campagne (ou du site), avec son exécution retenue depuis le début de la campagne (ou None)."""
    executions = [e for e in lire_executions(rapports) if e.debut is not None and e.debut >= debut]
    found = []
    for cas in campagne:
        if correspond(cas.site, site):
            found.append((cas, retenue([e for e in executions if e.code == cas.code])))
    return found


def fait(execution) -> bool:
    """Le cas a un verdict dans cette campagne : une exécution qui n'a pas été interrompue."""
    return execution is not None and not execution.interrompue


# --- Lancement ---------------------------------------------------------------------------


def _attendre(seconds: float, message: str) -> None:
    print(message, flush=True)
    time.sleep(seconds)


def _provider(args: argparse.Namespace, path: Path, cas: CasAttendu):
    """Le modèle pour ce cas. La démo sans IA rejoue scenario_<code>.json, rangé à côté du cas."""
    scenario = path.parent / f"scenario_{cas.code}.json" if args.provider == "scripted" else None
    return make_provider(args.provider, model=args.model, scenario=str(scenario) if scenario else None)


def lancer_cas(args: argparse.Namespace, cas: CasAttendu, path: Path, navigateur: str, appareil: str | None):
    """Lance un cas de la campagne et renvoie son résultat."""
    case = load_cases(str(path))[0]
    provider = _provider(args, path, cas)
    memory = memory_for(provider) if args.avec_memoire else None
    return run_case(case, provider, new_out_dir(args.rapports, case), headed=args.headed, max_steps=args.max_steps,
                    pause_s=args.pause, on_event=show_events, memory=memory, vision=cas.vision, navigateur=navigateur,
                    appareil=appareil)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m agent.campagne",
        description="Lance toute une campagne de cas de test en une commande, avec reprise après le quota.",
    )
    parser.add_argument("--campagne", default=str(CAMPAGNE), help=f"fichier de campagne ({CAMPAGNE.as_posix()} par défaut)")
    parser.add_argument("--site", help="ne lancer qu'un site de la campagne, par exemple parabank")
    parser.add_argument("--rapports", default=str(RAPPORTS), help="dossier des rapports (rapports par défaut)")
    parser.add_argument("--nouvelle", action="store_true", help="commence une nouvelle campagne, même si une autre est en cours")
    parser.add_argument("--depuis", help="compte comme faits les cas exécutés depuis cette date (AAAA-MM-JJ)")
    parser.add_argument("--attendre", action="store_true",
                        help="quand le quota du jour est épuisé, attend son renouvellement puis continue")
    parser.add_argument("--provider", choices=["gemini", "claude", "scripted"], default=None,
                        help="modèle à utiliser (par défaut : AGENT_PROVIDER ou gemini)")
    parser.add_argument("--model", help="nom précis du modèle (sinon GEMINI_MODEL ou CLAUDE_MODEL)")
    parser.add_argument("--max-steps", type=int, default=20, help="nombre maximal d'actions par cas (20 par défaut)")
    parser.add_argument("--pause", type=float, default=None,
                        help="pause en secondes entre deux actions (4 conseillé avec l'offre gratuite de Gemini)")
    parser.add_argument("--headed", action="store_true", help="affiche le navigateur pendant les tests")
    parser.add_argument("--avec-memoire", action="store_true",
                        help="rejoue les parcours mémorisés (par défaut, la campagne mesure le modèle, sans la mémoire)")
    add_browser_options(parser)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    args.provider = args.provider or os.getenv("AGENT_PROVIDER", "gemini")
    args.pause = args.pause if args.pause is not None else float(os.getenv("AGENT_PAUSE", "0"))
    campagne_path, rapports = Path(args.campagne), Path(args.rapports)
    try:
        campagne = lire_campagne(campagne_path.read_text(encoding="utf-8"))
        if args.site and not any(correspond(cas.site, args.site) for cas in campagne):
            sites = ", ".join(dict.fromkeys(cas.site for cas in campagne))
            raise ValueError(f"aucun site de la campagne ne correspond à « {args.site} » (sites : {sites}).")
        depuis = datetime.fromisoformat(args.depuis) if args.depuis else None
        navigateur, appareil = browser_from(args)
        if args.provider != "scripted":
            make_provider(args.provider, model=args.model)  # clé absente : on le dit tout de suite
    except OSError as exc:
        print(f"Erreur : fichier de campagne illisible ({exc}). Lance la commande depuis la racine du projet.", file=sys.stderr)
        return 3
    except (ValueError, ProviderError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 3

    debut, reprise = debut_campagne(rapports, campagne_path, args.site, args.nouvelle, depuis)
    liste = a_faire(campagne, rapports, debut, args.site)
    faits = sum(1 for _, execution in liste if fait(execution))
    quand = f"{debut:%d/%m à %H:%M}"
    print(f"Campagne    : {campagne_path.as_posix()}" + (f", site « {args.site} »" if args.site else ""))
    print(f"{'Reprise' if reprise else 'Début':<12}: campagne commencée le {quand}, {faits} cas déjà faits, "
          f"{len(liste) - faits} à lancer")
    print(f"Modèle      : {args.provider}" + (f" ({args.model})" if args.model else "") +
          (f", pause de {args.pause:g} s" if args.pause else "") + ("" if args.avec_memoire else ", sans la mémoire"))

    code = 0
    try:
        for position, (cas, execution) in enumerate(liste, 1):
            entete = f"\n[{position}/{len(liste)}] {cas.code} {cas.site}"
            if fait(execution):
                obtenu = LIBELLES[execution.statut]
                print(f"{entete} : déjà fait dans cette campagne ({obtenu}" +
                      (f", le {execution.debut:%d/%m à %H:%M})" if execution.debut else ")"))
                continue
            path = case_path(campagne_path, cas)
            if path is None or not path.is_file():
                print(f"{entete} : fichier du cas introuvable ({cas.fichier or 'pas de lien dans la cellule Cas'}), passé")
                continue
            print(f"{entete} : {path.name}" + (" (avec la vision)" if cas.vision else ""), flush=True)
            essais = 0
            while True:
                result = lancer_cas(args, cas, path, navigateur, appareil)
                if result.interruption == "quota_jour" and args.attendre:
                    _attendre(seconds_until_reset(), f"Quota du jour épuisé : reprise automatique {quota_reset_time()}. "
                                                     "Garde l'ordinateur allumé.")
                    continue
                if result.interruption in ("quota", "modele") and essais < 1:
                    essais += 1
                    _attendre(RETRY_WAIT_S, f"Le modèle ne répond pas ({result.summary}). Nouvel essai dans {RETRY_WAIT_S} s.")
                    continue
                break
            attendu = cas.verdict
            appels = "démo sans IA" if result.provider == "scripted" else \
                f"{result.model_calls} appel{'s' if result.model_calls > 1 else ''}"
            mesure = f"{appels}, {duree(result.duration_s)}"
            if result.interruption:
                print(f"       interrompu : {result.summary}")
            else:
                print(f"       {LIBELLES[result.status]} (attendu {LIBELLES[attendu]}) "
                      f"{MARQUES[ecart(attendu, _Vue(result))]} {ecart(attendu, _Vue(result))}, {mesure}")
            if result.interruption == "quota_jour":
                print(f"\nQuota du jour épuisé : la campagne s'arrête ici. Elle reprendra à {cas.code} : relance la même "
                      f"commande après le renouvellement du quota, {quota_reset_time()}.")
                code = EXIT_QUOTA
                break
            if result.interruption in ("quota", "modele"):
                print("\nLe modèle ne répond plus (réseau ou quota) : la campagne s'arrête ici. Relance la même commande "
                      f"plus tard, elle reprendra à {cas.code}.")
                code = EXIT_QUOTA
                break
    except NavigateurAbsent as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\nCampagne interrompue : relance la même commande pour reprendre là où elle s'est arrêtée.")
        return 130

    lignes = comparer(campagne, lire_executions(rapports), depuis=debut, site=args.site)
    reste = [cas for cas, execution in a_faire(campagne, rapports, debut, args.site) if not fait(execution)]
    if not reste and code == 0:
        (rapports / ETAT).unlink(missing_ok=True)  # tous les cas ont un verdict : la prochaine commande repart de zéro
    sortie = rapports / SORTIE
    sortie.write_text(rendu_markdown(lignes, campagne=campagne_path, rapports=rapports, sortie=sortie, site=args.site,
                                     depuis=debut), encoding="utf-8")
    print("\n" + rendu_terminal(lignes))
    print(f"\nBilan complet : {sortie}")
    if reste and code == 0:
        print(f"Cas sans verdict : {', '.join(cas.code for cas in reste)}. Relance la même commande pour les reprendre.")
    return code


class _Vue:
    """Le résultat d'une exécution vu comme une exécution du bilan, pour comparer au verdict attendu."""

    def __init__(self, result):
        self.statut, self.interrompue = result.status, bool(result.interruption)


if __name__ == "__main__":
    sys.exit(main())
