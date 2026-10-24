"""Point d'entrée en ligne de commande.

Exemples :
    python -m agent cas_de_test/CT-01_connexion_valide.md
    python -m agent cas_de_test/CT-01_connexion_valide.md --provider claude --headed
    python -m agent cas_de_test/CT-01_connexion_valide.md --navigateur webkit --appareil "iPhone 15"
    python -m agent cas_de_test/US-01_connexion.feature     (chaque scénario Gherkin, l'un après l'autre)

Pour toute une campagne en une commande, avec reprise après le quota : python -m agent.campagne
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .anomalie import NATURES
from .execution import memory_for, new_out_dir, run_case
from .navigateur import NavigateurAbsent, choisir
from .providers import ProviderError, make_provider
from .report import STATUS_LABELS
from .testcase import TestCase, load_test_case
from .user_stories import feature_cases

EXIT_CODES = {"PASS": 0, "FAIL": 1, "BLOQUE": 2}


def show_events(kind: str, data: dict) -> None:
    """Affiche les pauses imposées par le quota de l'API et l'usage de la mémoire, pour qu'on sache ce qui se passe."""
    if kind == "wait":
        print(f"Quota atteint : pause de {data['seconds']:.0f} s avant de réessayer ({data['attempt']}/{data['max_attempts']})")
    elif kind == "memory":
        count = data["steps"]
        print(f"Mémoire     : parcours connu ({count} étape{'s' if count > 1 else ''}), rejoué sans appel au modèle")
    elif kind == "heal":
        print(f"Réparation  : à l'étape {data['number']}, {data['reason']}. Le modèle prend le relais.")
    elif kind == "analysis":
        print("Analyse     : le test n'a pas réussi, le modèle rédige son diagnostic pour la fiche d'anomalie")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m agent",
        description="Agent IA qui exécute un cas de test écrit en français avec Playwright.",
    )
    parser.add_argument("cas_de_test", help="fichier du cas de test : Markdown (.md) ou scénarios Gherkin (.feature)")
    parser.add_argument("--numero", type=int, help="pour un fichier .feature : numéro du seul scénario à lancer")
    parser.add_argument("--provider", choices=["gemini", "claude", "scripted"], default=None,
                        help="modèle à utiliser (par défaut : AGENT_PROVIDER ou gemini)")
    parser.add_argument("--model", help="nom précis du modèle (sinon GEMINI_MODEL ou CLAUDE_MODEL)")
    parser.add_argument("--url", help="adresse à tester à la place de celle du cas de test (autre site, recette, adresse IP)")
    parser.add_argument("--scenario", help="fichier JSON du scénario, pour le fournisseur scripted")
    parser.add_argument("--max-steps", type=int, default=20, help="nombre maximal d'actions (20 par défaut)")
    parser.add_argument("--pause", type=float, default=None,
                        help="pause en secondes entre deux actions, utile avec l'offre gratuite de Gemini")
    parser.add_argument("--headed", action="store_true", help="affiche le navigateur pendant le test")
    parser.add_argument("--out", default="rapports", help="dossier des rapports (rapports par défaut)")
    parser.add_argument("--sans-memoire", action="store_true",
                        help="ignore le parcours mémorisé : tout le test passe par le modèle")
    parser.add_argument("--vision", action="store_true",
                        help="envoie aussi la capture d'écran au modèle (par défaut : AGENT_VISION dans .env)")
    add_browser_options(parser)
    return parser.parse_args(argv)


def add_browser_options(parser: argparse.ArgumentParser) -> None:
    """--navigateur et --appareil, communs à python -m agent et à la campagne."""
    parser.add_argument("--navigateur", choices=["chromium", "firefox", "webkit"], default=None,
                        help="moteur du navigateur (par défaut : AGENT_NAVIGATEUR ou chromium). WebKit est celui de Safari.")
    parser.add_argument("--appareil", default=None,
                        help="téléphone ou tablette à émuler, par exemple \"iPhone 15\", \"Pixel 7\" ou \"iPad Mini\" "
                             "(par défaut : AGENT_APPAREIL, sinon un ordinateur)")


def browser_from(args: argparse.Namespace) -> tuple[str, str | None]:
    """Le navigateur et l'appareil demandés (ligne de commande, sinon .env), vérifiés : ValueError s'ils sont inconnus."""
    return choisir(args.navigateur or os.getenv("AGENT_NAVIGATEUR") or "chromium",
                   args.appareil or os.getenv("AGENT_APPAREIL") or None)


def load_cases(path: str, number: int | None = None) -> list[TestCase]:
    """Un cas Markdown, ou les scénarios d'un fichier Gherkin (tous, ou le seul demandé)."""
    if not path.endswith(".feature"):
        return [load_test_case(path)]
    cases = feature_cases(Path(path).read_text(encoding="utf-8"))
    if not cases:
        raise ValueError("aucun scénario exécutable dans ce fichier : il faut une ligne « # URL : https://... », "
                         "une ligne « Fonctionnalité: » et au moins un « Scénario: » avec ses étapes.")
    if number is not None:
        if not 1 <= number <= len(cases):
            raise ValueError(f"ce fichier compte {len(cases)} scénario(s) : choisissez --numero entre 1 et {len(cases)}.")
        return [cases[number - 1]]
    return cases


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # lit les clés d'API dans le fichier .env
    args = parse_args(argv)
    provider_name = args.provider or os.getenv("AGENT_PROVIDER", "gemini")
    pause = args.pause if args.pause is not None else float(os.getenv("AGENT_PAUSE", "0"))

    try:
        cases = load_cases(args.cas_de_test, args.numero)
        if args.url:
            cases = [case.with_url(args.url) for case in cases]
        make_provider(provider_name, model=args.model, scenario=args.scenario)  # clé absente : on le dit tout de suite
        navigateur, appareil = browser_from(args)
    except (OSError, ValueError, ProviderError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 3

    vision = args.vision or os.getenv("AGENT_VISION", "").strip().lower() in ("1", "true", "oui")
    codes = []
    try:
        for index, case in enumerate(cases, 1):
            if len(cases) > 1:
                print(f"\n--- Scénario {index} sur {len(cases)} ---")
            provider = make_provider(provider_name, model=args.model, scenario=args.scenario)  # un faux modèle neuf par scénario
            codes.append(run_one(case, provider, args, pause, vision, navigateur, appareil))
    except NavigateurAbsent as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 3
    if len(cases) > 1:
        passed = codes.count(0)
        print(f"\nBilan       : {passed} scénario(s) réussi(s) sur {len(cases)}")
    return max(codes)


def run_one(case: TestCase, provider, args: argparse.Namespace, pause: float, vision: bool, navigateur: str = "chromium",
            appareil: str | None = None) -> int:
    print(f"Cas de test : {case.title}")
    print(f"Modèle      : {provider.name} ({provider.model})")
    memory = None if args.sans_memoire else memory_for(provider)
    if vision:
        print("Vision      : le modèle voit aussi la capture d'écran de la page")
    result = run_case(case, provider, new_out_dir(args.out, case), headed=args.headed, max_steps=args.max_steps,
                      pause_s=pause, on_event=show_events, memory=memory, vision=vision, navigateur=navigateur,
                      appareil=appareil)

    print(f"Navigateur  : {result.browser}")
    if result.unique:
        print(f"Valeur unique : {result.unique} (à la place de {{unique}} pour cette exécution)")
    if result.session_saved is not None:
        print(f"Session     : « {case.save_session} » enregistrée ({result.session_saved.as_posix()})")
    print(f"Statut      : {STATUS_LABELS.get(result.status, result.status)}")
    print(f"Conclusion  : {result.summary}")
    print(f"Rapport     : {result.report_path}")
    print(f"Test pytest : {result.script_path}")
    print(f"Test Robot  : {result.robot_path}")
    print(f"Gherkin     : {result.feature_path} (exécutable : {result.bdd_path.name})")
    if result.postman_path is not None:
        count = len({(call.method, call.url, call.body) for call in result.api_calls or []})
        print(f"Postman     : {result.postman_path} ({count} appel{'s' if count > 1 else ''} d'API, "
              f"environnement : {result.postman_env_path.name})")
    calls = "aucun (démo sans IA)" if result.provider == "scripted" else str(result.model_calls)
    print(f"Appels IA   : {calls}" + (" (parcours enregistré en mémoire)" if result.memory_saved else ""))
    if result.anomaly_path is not None:
        avis = f" (avis de l'IA : {NATURES[result.diagnosis['nature']].lower()})" if result.diagnosis else ""
        print(f"Anomalie    : {result.anomaly_path}{avis}")
    return EXIT_CODES.get(result.status, 2)


if __name__ == "__main__":
    sys.exit(main())
