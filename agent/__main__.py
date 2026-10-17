"""Point d'entrée en ligne de commande.

Exemples :
    python -m agent cas_de_test/CT-01_connexion_valide.md
    python -m agent cas_de_test/CT-01_connexion_valide.md --provider claude --headed
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from .execution import new_out_dir, run_case
from .providers import ProviderError, make_provider
from .report import STATUS_LABELS
from .testcase import load_test_case

EXIT_CODES = {"PASS": 0, "FAIL": 1, "BLOQUE": 2}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m agent",
        description="Agent IA qui exécute un cas de test écrit en français avec Playwright.",
    )
    parser.add_argument("cas_de_test", help="fichier Markdown du cas de test")
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # lit les clés d'API dans le fichier .env
    args = parse_args(argv)
    provider_name = args.provider or os.getenv("AGENT_PROVIDER", "gemini")
    pause = args.pause if args.pause is not None else float(os.getenv("AGENT_PAUSE", "0"))

    try:
        case = load_test_case(args.cas_de_test)
        if args.url:
            case = case.with_url(args.url)
        provider = make_provider(provider_name, model=args.model, scenario=args.scenario)
    except (OSError, ValueError, ProviderError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 3

    print(f"Cas de test : {case.title}")
    print(f"Modèle      : {provider.name} ({provider.model})")

    result = run_case(case, provider, new_out_dir(args.out, case), headed=args.headed,
                      max_steps=args.max_steps, pause_s=pause)

    print(f"Statut      : {STATUS_LABELS.get(result.status, result.status)}")
    print(f"Conclusion  : {result.summary}")
    print(f"Rapport     : {result.report_path}")
    print(f"Test pytest : {result.script_path}")
    print(f"Test Robot  : {result.robot_path}")
    return EXIT_CODES.get(result.status, 2)


if __name__ == "__main__":
    sys.exit(main())
