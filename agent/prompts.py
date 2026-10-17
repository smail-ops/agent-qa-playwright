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
- finish : {"status": "PASS" | "FAIL" | "BLOQUE", "summary": "..."} termine le test

Règles :
1. Suis les étapes du cas de test dans l'ordre, sans en ajouter.
2. Utilise uniquement les numéros « ref » de la liste actuelle : ils changent après chaque action.
3. Utilise exactement les données de test fournies. N'invente jamais d'identifiant, de mot de passe ou de valeur.
4. Vérifie chaque résultat attendu avec check_text ou check_url avant de terminer. Pour un résultat attendu négatif (« ne s'affiche pas », « ne contient pas »), utilise "absent": true.
5. Termine avec finish : PASS si toutes les vérifications ont réussi, FAIL si une vérification a échoué, BLOQUE si une étape est impossible (élément introuvable après deux essais, erreur bloquante).
"""


def describe_step(step: Step) -> str:
    """Résumé d'une étape déjà réalisée, pour l'historique envoyé au modèle."""
    result = step.result
    if step.action is None:
        return f"{step.number}. Réponse illisible : {result.detail}"
    action = step.action
    target = f" [{action.args['ref']}]" if "ref" in action.args else ""
    value = next((action.args[k] for k in ("url", "text", "value", "contains") if k in action.args), "")
    value = f' "{value}"' if value else ""
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
