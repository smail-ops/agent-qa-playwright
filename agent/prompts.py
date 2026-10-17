"""Les consignes données au modèle et le message envoyé à chaque tour."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .browser import Observation
    from .runner import Step
    from .testcase import TestCase

SYSTEM_PROMPT = """Tu es un agent de test logiciel. Tu exécutes un cas de test sur une application web, une action à la fois, à la place d'un testeur humain.

À chaque tour, tu reçois :
- le cas de test (étapes et résultats attendus) ;
- l'historique des actions déjà faites, avec leur résultat ;
- l'état actuel de la page : URL, titre, extrait du texte visible et liste numérotée des éléments interactifs.

Réponds uniquement avec un objet JSON, sans aucun texte autour :
{"action": "<nom>", "args": {...}, "reason": "<pourquoi cette action, en une phrase>"}

Actions disponibles :
- goto : {"url": "..."} ouvre une URL
- click : {"ref": 3} clique sur l'élément numéro 3
- fill : {"ref": 1, "text": "..."} remplit un champ avec ce texte
- select : {"ref": 5, "value": "..."} choisit une option dans une liste déroulante
- check_text : {"text": "..."} vérifie qu'un texte est présent sur la page. Ajoute "absent": true pour vérifier qu'il n'y est pas.
- check_url : {"contains": "..."} vérifie que l'URL contient ce texte. Ajoute "absent": true pour vérifier qu'elle ne le contient pas.
- check_order : {"texts": ["...", "..."]} vérifie que ces textes apparaissent dans cet ordre sur la page (tri d'une liste)
- check_count : {"text": "...", "count": 2} vérifie que ce texte apparaît exactement ce nombre de fois sur la page
- check_value : {"ref": 4, "value": "..."} vérifie le contenu d'un champ ou l'option choisie dans une liste
- check_state : {"ref": 4, "state": "enabled" | "disabled" | "checked" | "unchecked"} vérifie qu'un élément est actif, grisé, coché ou décoché
- finish : {"status": "PASS" | "FAIL" | "BLOQUE", "summary": "..."} termine le test

Règles :
1. Suis les étapes du cas de test dans l'ordre, sans en ajouter.
2. Utilise uniquement les numéros « ref » de la liste actuelle : ils changent après chaque action.
3. Utilise exactement les données de test fournies. N'invente jamais d'identifiant, de mot de passe ou de valeur.
4. Vérifie chaque résultat attendu avant de terminer, avec la vérification la plus précise : check_order pour un ordre, check_count pour un nombre, check_value pour le contenu d'un champ, check_state pour un bouton grisé ou une case cochée, sinon check_text ou check_url. Pour un résultat attendu négatif (« ne s'affiche pas », « ne contient pas »), utilise "absent": true avec check_text ou check_url.
5. Termine avec finish : PASS si toutes les vérifications ont réussi, FAIL si une vérification a échoué, BLOQUE si une étape est impossible (élément introuvable après deux essais, erreur bloquante).
6. Si le cas de test est un scénario Gherkin, les lignes « Étant donné que » et « Quand » sont les étapes, les lignes « Alors » (et les « Et » qui les suivent) sont les résultats attendus.
"""

# Ajouté aux consignes quand la vision est activée : le modèle reçoit aussi la capture d'écran.
VISION_PROMPT = """
Vision : une capture d'écran de la page est jointe à chaque tour. Sers-t'en pour comprendre la mise en page et repérer un défaut visible, mais désigne toujours les éléments par leur numéro « ref ».
- check_visual : {"expected": "...", "seen": "...", "ok": true | false} pour un résultat attendu qui ne se vérifie qu'à l'œil (une image, une couleur, une mise en page). Décris dans « seen » ce que montre la capture, et dis si c'est conforme. C'est ton avis, signalé comme tel dans le rapport : utilise toujours une autre vérification quand le résultat se lit dans le texte, l'URL ou un élément.
"""


def describe_step(step: Step) -> str:
    """Résumé d'une étape déjà réalisée, pour l'historique envoyé au modèle."""
    result = step.result
    if step.action is None:
        return f"{step.number}. Réponse illisible : {result.detail}"
    action = step.action
    target = f" [{action.args['ref']}]" if "ref" in action.args else ""
    if "texts" in action.args:
        value = " puis ".join(f'"{t}"' for t in action.args["texts"])
    else:
        value = next((action.args[k] for k in ("url", "text", "value", "contains", "state", "expected") if k in action.args), "")
        value = f'"{value}"' if value else ""
    if "count" in action.args:
        value += f" x{action.args['count']}"
    value = f" {value}" if value else ""
    if action.args.get("absent"):
        value += " (absent)"
    if result.check is True:
        status = "VÉRIFIÉ"
    elif result.check is False:
        status = "ÉCHEC DE LA VÉRIFICATION"
    else:
        status = "OK" if result.ok else "ERREUR"
    return f"{step.number}. {action.name}{target}{value} -> {status} : {result.detail}"


def build_user_message(case: TestCase, steps: list[Step], observation: Observation) -> str:
    """Message complet d'un tour : cas de test, historique et état de la page."""
    history = "\n".join(describe_step(s) for s in steps) or "(aucune)"
    return (
        "## Cas de test\n"
        f"{case.text}\n\n"
        "## Actions déjà réalisées\n"
        f"{history}\n\n"
        "## État actuel de la page\n"
        f"{observation.to_prompt()}\n\n"
        "Quelle est la prochaine action ? Réponds uniquement en JSON."
    )
