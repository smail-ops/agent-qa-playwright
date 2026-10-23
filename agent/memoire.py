"""Mémoire des parcours réussis, et rejeu sans IA avec auto-réparation.

La première exécution d'un cas de test passe par le modèle d'IA. Si le test
réussit, le parcours (les actions et les éléments visés) est enregistré dans
le dossier memoire/. Les exécutions suivantes le rejouent directement, sans
appeler le modèle : c'est plus rapide, gratuit, et le quota de l'API reste
intact.

Si le site a changé et qu'un élément du parcours n'existe plus, l'agent ne
s'arrête pas : le modèle reprend la main à partir de cette étape et trouve le
nouveau chemin (auto-réparation). Si le test réussit, la mémoire est mise à
jour avec le parcours réparé.

Une vérification qui échoue pendant le rejeu donne un FAIL : c'est le
comportement du site qui a changé, pas le chemin. La mémoire ne garde que les
parcours réussis, et chaque parcours est lié au texte exact du cas de test et
à son adresse : modifier le cas (ou tester un autre site) repart de l'IA.

Le dossier memoire/ contient les données de test saisies (comme les tests
générés) : il est exclu du dépôt Git par le fichier .gitignore.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .actions import Action, parse_action

if TYPE_CHECKING:
    from .browser import Element, Observation
    from .runner import Step
    from .testcase import TestCase

VERSION = 1
_ELEMENT_FIELDS = ("selector", "nth", "xpath", "tag", "type", "text", "shadow", "frame")


def _place(hops: list | None) -> tuple:
    """Cadres ou composants qui contiennent l'élément, sans leur nom affiché (qui peut changer)."""
    return tuple((hop.get("css"), hop.get("nth")) for hop in hops or [] if isinstance(hop, dict))


def fingerprint(case: TestCase) -> str:
    """Empreinte du texte du cas de test (son adresse comprise) : un cas modifié n'a plus la même."""
    text = "\n".join(line.rstrip() for line in case.text.strip().splitlines())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class Memory:
    """Les parcours réussis, un fichier JSON par cas de test et par adresse."""

    def __init__(self, folder: str | Path = "memoire"):
        self.folder = Path(folder)

    def path(self, case: TestCase) -> Path:
        return self.folder / f"{case.slug}_{fingerprint(case)}.json"

    def load(self, case: TestCase) -> dict | None:
        """Le parcours mémorisé pour ce cas, ou None s'il n'y en a pas (ou s'il est illisible)."""
        try:
            data = json.loads(self.path(case).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("steps"), list):
            return None
        return data if data["steps"] else None

    def info(self, case: TestCase) -> dict | None:
        """Résumé pour l'interface : date d'enregistrement et nombre d'étapes."""
        data = self.load(case)
        return {"saved_at": data.get("saved_at"), "steps": len(data["steps"])} if data else None

    def save(self, case: TestCase, steps: list[Step], model: str, summary: str) -> Path:
        """Enregistre le parcours d'une exécution réussie : seulement les étapes qui ont fonctionné."""
        saved = []
        for step in steps[1:]:  # la première étape, l'ouverture de l'URL, est refaite à chaque exécution
            action, result = step.action, step.result
            if action is None or not result.ok or action.name == "finish":
                continue
            entry = {"action": action.name, "args": {k: v for k, v in action.args.items() if k != "ref"},
                     "reason": action.reason}
            if "ref" in action.args:
                if result.element is None:
                    continue
                entry["element"] = {name: getattr(result.element, name) for name in _ELEMENT_FIELDS}
                entry["element"]["label"] = result.element.label()
            saved.append(entry)
        data = {
            "version": VERSION,
            "case": case.title,
            "url": case.url,
            "fingerprint": fingerprint(case),
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "model": model,
            "summary": summary,
            "steps": saved,
        }
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.path(case)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def forget(self, case: TestCase) -> bool:
        try:
            self.path(case).unlink()
        except FileNotFoundError:
            return False
        return True


def find_again(saved: dict, observation: Observation) -> Element | None:
    """Retrouve dans la page actuelle l'élément mémorisé.

    Seule une correspondance exacte compte : même sélecteur (et même rang s'il y a
    plusieurs éléments identiques), ou même XPath, dans le même cadre et le même
    composant. Au moindre doute, mieux vaut laisser le modèle chercher que
    cliquer au mauvais endroit.
    """
    place = (_place(saved.get("frame")), _place(saved.get("shadow")))
    here = [e for e in observation.elements if (_place(e.frame), _place(e.shadow)) == place]
    for element in here:
        if element.selector == saved.get("selector") and element.nth == saved.get("nth"):
            return element
    if saved.get("xpath"):
        for element in here:
            if element.xpath == saved["xpath"]:
                return element
    return None


def replay_action(entry: dict, observation: Observation) -> tuple[Action | None, str]:
    """L'action mémorisée, avec le numéro de l'élément dans la page actuelle.

    Renvoie (None, raison) si l'élément n'est plus là : le modèle prendra le relais.
    """
    args = dict(entry.get("args") or {}) if isinstance(entry.get("args"), dict) else {}
    reason = entry.get("reason") or "Étape du parcours enregistré"
    saved = entry.get("element")
    if isinstance(saved, dict):
        element = find_again(saved, observation)
        if element is None:
            name = saved.get("label") or saved.get("text") or saved.get("selector")
            return None, f"élément « {name} » introuvable sur la page"
        args["ref"] = element.ref
    try:  # un fichier de mémoire modifié à la main passe par les mêmes contrôles qu'une réponse du modèle
        action = parse_action(json.dumps({"action": entry.get("action"), "args": args, "reason": reason}))
    except ValueError as exc:
        return None, f"étape mémorisée illisible ({exc})"
    if action.name == "finish":
        return None, "étape mémorisée illisible"
    return action, ""
