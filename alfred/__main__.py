"""Lance Alfred : python -m alfred

Alfred sert aussi le site de démo sur http://localhost:8000, pour que le cas
CT-00 fonctionne sans ouvrir un second terminal.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import sys
import threading
import webbrowser
from pathlib import Path

from dotenv import load_dotenv

from . import maitre
from .server import AlfredServer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEMO_PORT = 8000


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def serve_demo_site(port: int = DEMO_PORT) -> http.server.ThreadingHTTPServer | None:
    """Sert demo_site/ sur 127.0.0.1:port, sauf si le port est déjà pris."""
    handler = functools.partial(_QuietHandler, directory=str(PROJECT_ROOT / "demo_site"))
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError:
        return None
    threading.Thread(target=server.serve_forever, name="site-de-demo", daemon=True).start()
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m alfred", description="Alfred, l'interface de l'agent QA.")
    parser.add_argument("--port", type=int, default=8765, help="port d'Alfred (8765 par défaut)")
    parser.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur au démarrage")
    parser.add_argument("--max-steps", type=int, default=20, help="nombre maximal d'actions par mission")
    parser.add_argument("--silencieux", action="store_true", help="pas de salutation à voix haute au démarrage")
    parser.add_argument("--arret-auto", nargs="?", const=3.0, type=float, default=None, metavar="MINUTES",
                        help="s'arrêter seul quand aucune fenêtre Alfred n'est ouverte depuis MINUTES "
                             "(3 par défaut), sauf pendant une mission : c'est le mode d'Alfred.app")
    args = parser.parse_args(argv)
    if args.arret_auto is not None and args.arret_auto <= 0:
        parser.error("--arret-auto attend un nombre de minutes positif, par exemple --arret-auto 3")

    os.chdir(PROJECT_ROOT)  # cas_de_test/, rapports/ et .env sont lus depuis la racine du projet
    load_dotenv(PROJECT_ROOT / ".env")

    arret_auto = args.arret_auto * 60 if args.arret_auto is not None else None
    try:
        server = AlfredServer(("127.0.0.1", args.port), "cas_de_test", "rapports", args.max_steps,
                              arret_auto=arret_auto)
    except OSError:
        print(f"Le port {args.port} est déjà utilisé : Alfred tourne peut-être déjà.", file=sys.stderr)
        print(f"Ouvrez http://127.0.0.1:{args.port} ou choisissez un autre port avec --port.", file=sys.stderr)
        return 1

    demo = serve_demo_site()
    print(f"Alfred est prêt : {server.url}")
    if demo is not None:
        print(f"Site de démo    : http://localhost:{DEMO_PORT} (servi par Alfred)")
    else:
        print(f"Site de démo    : le port {DEMO_PORT} est déjà pris, je suppose que la démo tourne déjà.")
    if arret_auto is not None:
        print(f"Arrêt auto      : {args.arret_auto:g} min après la fermeture de la dernière fenêtre Alfred")
    print("Ctrl + C, ou le bouton Quitter de la page, pour arrêter Alfred.")

    # Alfred se présente et salue son maître, avec la voix du Mac : le navigateur ne peut pas parler avant un clic.
    texte = maitre.salutation(maitre.nom_du_maitre())
    voix = "salutation silencieuse (--silencieux)" if args.silencieux else maitre.saluer_a_voix_haute(texte)
    print(f"Alfred          : « {texte} » ({voix})")

    if not args.no_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        print("Alfred s'arrête. À bientôt.", flush=True)
        server.arreter_mission()
        server.attendre_mission(20)  # la mission arrêtée termine son rapport avant la fin du programme
        server.server_close()
        if demo is not None:
            demo.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
