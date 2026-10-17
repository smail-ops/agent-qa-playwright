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
    args = parser.parse_args(argv)

    os.chdir(PROJECT_ROOT)  # cas_de_test/, rapports/ et .env sont lus depuis la racine du projet
    load_dotenv(PROJECT_ROOT / ".env")

    try:
        server = AlfredServer(("127.0.0.1", args.port), "cas_de_test", "rapports", args.max_steps)
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
    print("Ctrl + C pour arrêter Alfred.")

    if not args.no_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nAlfred s'arrête. À bientôt.")
    finally:
        server.server_close()
        if demo is not None:
            demo.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
