"""Les consignes données au modèle et le message envoyé à chaque tour."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .coffre import Coffre
from .masquage import mask

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
- click : {"ref": 3} clique sur l'élément numéro 3. Ajoute "double": true pour un double-clic, "button": "right" pour un clic droit.
- fill : {"ref": 1, "text": "..."} remplit un champ ou une zone de texte avec ce texte
- select : {"ref": 5, "value": "..."} choisit une option dans une liste déroulante (élément select)
- press : {"key": "Enter"} appuie sur une touche du clavier (Enter, Escape, Tab, ArrowDown, Backspace, Control+A...). Ajoute "ref": 4 pour la taper dans cet élément ; sans ref, elle va à la page.
- hover : {"ref": 6} survole un élément avec la souris, par exemple un menu qui s'ouvre au survol
- scroll : {"to": "bottom"} fait défiler la page jusqu'en bas ("top" pour remonter), ou {"ref": 9} jusqu'à cet élément
- upload : {"ref": 7, "file": "facture.txt"} joint un fichier du dossier de données de test au champ de fichier numéro 7
- drag : {"ref": 2, "to": 8} glisse l'élément numéro 2 et le dépose sur l'élément numéro 8
- switch_tab : {"index": 1} passe sur l'onglet numéro 1 (le premier) ; utile pour revenir au premier onglet
- wait : {"seconds": 15, "text": "..."} attend jusqu'à ce nombre de secondes (30 au plus) que ce texte s'affiche, pour un chargement lent. Sans "text", attend simplement ce nombre de secondes.
- dialog : {"accept": true | false} réponse à donner à la prochaine fenêtre JavaScript (alerte, confirmation), avant l'action qui l'ouvre. Ajoute "text": "..." pour remplir une fenêtre de saisie.
- check_text : {"text": "..."} vérifie qu'un texte est présent sur la page. Ajoute "absent": true pour vérifier qu'il n'y est pas.
- check_url : {"contains": "..."} vérifie que l'URL contient ce texte. Ajoute "absent": true pour vérifier qu'elle ne le contient pas.
- check_order : {"texts": ["...", "..."]} vérifie que ces textes apparaissent dans cet ordre sur la page (tri d'une liste)
- check_count : {"text": "...", "count": 2} vérifie que ce texte apparaît exactement ce nombre de fois sur la page
- check_value : {"ref": 4, "value": "..."} vérifie le contenu d'un champ, d'une zone de texte ou l'option choisie dans une liste. Ajoute "regex": true quand le résultat attendu est un format et non une valeur exacte (un code généré, une date) : "value" est alors une expression régulière.
- check_state : {"ref": 4, "state": "enabled" | "disabled" | "checked" | "unchecked"} vérifie qu'un élément est actif, grisé, coché ou décoché
- finish : {"status": "PASS" | "FAIL" | "BLOQUE", "summary": "..."} termine le test

Règles :
1. Suis les étapes du cas de test dans l'ordre, sans en ajouter.
2. Utilise uniquement les numéros « ref » de la liste actuelle : ils changent après chaque action.
3. Utilise exactement les données de test fournies. N'invente jamais d'identifiant, de mot de passe ou de valeur.
4. Vérifie chaque résultat attendu avant de terminer, avec la vérification la plus précise : check_order pour un ordre, check_count pour un nombre, check_value pour le contenu d'un champ, check_state pour un bouton grisé ou une case cochée, sinon check_text ou check_url. Pour un résultat attendu négatif (« ne s'affiche pas », « ne contient pas »), utilise "absent": true avec check_text ou check_url.
5. Termine avec finish : PASS si toutes les vérifications ont réussi, FAIL si une vérification a échoué, BLOQUE si une étape est impossible (élément introuvable après deux essais, erreur bloquante).
6. Si le cas de test est un scénario Gherkin, les lignes « Étant donné que » et « Quand » sont les étapes, les lignes « Alors » (et les « Et » qui les suivent) sont les résultats attendus.
7. Les éléments d'un cadre (iframe) ou d'un composant web (Shadow DOM) sont dans la liste comme les autres, avec la mention « dans le cadre » ou « dans le composant ». Une zone « éditable » se remplit avec fill et se vérifie avec check_value.
8. Une liste déroulante « maison », faite de div et non d'un élément select, se manipule avec click : clique sur la liste pour l'ouvrir, puis sur l'option au tour suivant.
9. Une fenêtre JavaScript (alerte, confirmation) reçoit OK par défaut, et son message apparaît dans le résultat de l'action qui l'a ouverte. Pour répondre Annuler, ou remplir une fenêtre de saisie, utilise dialog juste avant cette action.
10. Pour un résultat qui met du temps à s'afficher (chargement de plusieurs secondes), utilise wait avec le texte attendu, puis vérifie-le.
11. Les données personnelles de la page (e-mail, téléphone, carte, IBAN, nom, adresse) peuvent être remplacées par un repère comme « [e-mail masqué] » : ne saisis et ne vérifie jamais ce repère.
12. Un mot de passe du cas de test est remplacé par un repère : « [mot de passe] » (ou « [mot de passe 1] », « [mot de passe 2] »). Saisis ce repère tel quel avec fill : le programme tape le vrai mot de passe à sa place.
13. Un clic qui ouvre un nouvel onglet t'y fait passer : la page décrite est alors celle du nouvel onglet, et la liste des onglets ouverts est indiquée. Pour revenir au premier, utilise switch_tab.
14. Un bandeau de cookies est accepté par le programme avant ton tour (étape « cookies » de l'historique) : une étape qui demande de l'accepter est alors faite. S'il couvre encore la page, accepte-le toi-même.
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
    action, args = step.action, step.action.args
    target = f" [{args['ref']}]" if "ref" in args else ""
    typed = ' "' + args["text"] + '"' if "text" in args else ""
    if action.name == "wait":
        value = f"{args['seconds']} s{typed}"
    elif action.name == "press":
        value = f'"{args["key"]}"'
    elif action.name == "scroll":
        value = f'"{args["to"]}"' if "to" in args else ""
    elif action.name == "upload":
        value = f'"{args["file"]}"'
    elif action.name == "drag":
        value = f"-> [{args['to']}]"
    elif action.name == "switch_tab":
        value = f"onglet {args['index']}"
    elif action.name == "cookies":  # fait par le programme, sans le modèle
        value = f'"{args["bouton"]}"'
    elif action.name == "dialog":
        value = ("OK" if args["accept"] else "Annuler") + typed
    elif "texts" in args:
        value = " puis ".join(f'"{t}"' for t in args["texts"])
    else:
        value = next((args[k] for k in ("url", "text", "value", "contains", "state", "expected") if k in args), "")
        value = f'"{value}"' if value else ""
    if "count" in args:
        value += f" x{args['count']}"
    value = f" {value}" if value else ""
    if args.get("absent"):
        value += " (absent)"
    if args.get("regex"):
        value += " (motif)"
    if args.get("double"):
        value += " (double-clic)"
    if args.get("button") == "right":
        value += " (clic droit)"
    if result.check is True:
        status = "VÉRIFIÉ"
    elif result.check is False:
        status = "ÉCHEC DE LA VÉRIFICATION"
    else:
        status = "OK" if result.ok else "ERREUR"
    return f"{step.number}. {action.name}{target}{value} -> {status} : {result.detail}"


def build_user_message(case: TestCase, steps: list[Step], observation: Observation) -> str:
    """Message complet d'un tour : cas de test, historique et état de la page.

    Les données personnelles de la page (e-mail, téléphone, carte, IBAN, noms et adresses) sont
    masquées, sauf celles que le cas de test donne lui-même : voir masquage.py. Les mots de passe du
    cas de test sont remplacés par un repère, « [mot de passe] » : voir coffre.py.
    """
    history = "\n".join(describe_step(s) for s in steps) or "(aucune)"
    return (
        "## Cas de test\n"
        f"{Coffre.from_case(case.text).hide(case.text)}\n\n"
        "## Actions déjà réalisées\n"
        f"{mask(history, case.text)}\n\n"
        "## État actuel de la page\n"
        f"{mask(observation.to_prompt(), case.text)}\n\n"
        "Quelle est la prochaine action ? Réponds uniquement en JSON."
    )
