"""Ce qu'Alfred affiche et dit, en français, à partir des actions de l'agent.

Deux registres : un libellé court pour le journal (« Clic sur le bouton
« Login » ») et une phrase à la première personne, lue à voix haute
(« Je clique sur le bouton « Login ». »). Les mots de passe ne sont jamais
affichés ni prononcés.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.runner import STOPPED_SUMMARY

if TYPE_CHECKING:
    from agent.actions import Action
    from agent.browser import Element, Observation
    from agent.runner import RunResult, Step

MAX_SPOKEN = 220  # au-delà, la phrase lue à voix haute est coupée


def q(text: str) -> str:
    """Guillemets français avec espaces insécables : « inventory » ne se coupe pas en fin de ligne."""
    return f"«\u00a0{text}\u00a0»"


def find_element(action: Action, observation: Observation | None) -> Element | None:
    """Élément visé par l'action, retrouvé dans la page observée grâce à son numéro."""
    if observation is None or "ref" not in action.args:
        return None
    return next((e for e in observation.elements if e.ref == action.args["ref"]), None)


def is_secret(element: Element | None) -> bool:
    return element is not None and element.tag == "input" and (element.type or "").lower() == "password"


def element_name(element: Element | None) -> str:
    """« le champ « Username » », « le bouton « Login » », « le lien « Panier » »..."""
    if element is None:
        return "l'élément demandé"
    attrs = element.attrs or {}
    text = (element.text or "").strip()
    if element.tag in ("input", "textarea") and (element.type or "") not in ("submit", "button", "reset"):
        name = attrs.get("placeholder") or attrs.get("aria") or attrs.get("name") or attrs.get("id") or attrs.get("test_id")
        return f"le champ {q(name)}" if name else "le champ"
    if element.tag == "select":
        name = attrs.get("aria") or attrs.get("name") or attrs.get("id") or attrs.get("test_id")
        return f"la liste {q(name)}" if name else "la liste"
    label = text or attrs.get("aria") or attrs.get("test_id") or attrs.get("id")
    kind = "le lien" if element.tag == "a" else "le bouton"
    return f"{kind} {q(label)}" if label else kind


def action_label(action: Action | None, element: Element | None = None) -> str:
    """Libellé court d'une action, pour le journal de mission."""
    if action is None:
        return "Réponse illisible du modèle"
    args = action.args
    if action.name == "goto":
        return f"Ouverture de {args['url']}"
    if action.name == "fill":
        if is_secret(element):
            return f"Saisie du mot de passe dans {element_name(element)}"
        return f"Saisie de {q(args['text'])} dans {element_name(element)}"
    if action.name == "click":
        return f"Clic sur {element_name(element)}"
    if action.name == "select":
        return f"Choix de {q(args['value'])} dans {element_name(element)}"
    if action.name == "check_text":
        verb = "ne s'affiche pas" if args.get("absent") else "s'affiche"
        return f"Vérification : {q(args['text'])} {verb}"
    if action.name == "check_url":
        verb = "ne contient pas" if args.get("absent") else "contient"
        return f"Vérification : l'URL {verb} {q(args['contains'])}"
    return action.name


def action_speech(action: Action, element: Element | None = None, number: int = 0) -> str:
    """Ce qu'Alfred annonce avant d'agir."""
    args = action.args
    if action.name == "goto":
        return "J'ouvre la page du cas de test." if number == 1 else "J'ouvre une nouvelle page."
    if action.name == "fill":
        if is_secret(element):
            return "Je saisis le mot de passe."
        return f"Je saisis {q(args['text'])} dans {element_name(element)}."
    if action.name == "click":
        return f"Je clique sur {element_name(element)}."
    if action.name == "select":
        return f"Je choisis {q(args['value'])}."
    if action.name == "check_text":
        verb = "ne s'affiche pas" if args.get("absent") else "s'affiche"
        return f"Je vérifie que {q(args['text'])} {verb}."
    if action.name == "check_url":
        return "Je vérifie l'adresse de la page."
    return ""


def step_speech(step: Step) -> str | None:
    """Ce qu'Alfred dit une fois l'étape réalisée : seulement ce qui compte."""
    result = step.result
    if step.action is None:
        return "Je n'ai pas compris la réponse du modèle. Je lui redemande."
    if result.check is True:
        return "C'est vérifié."
    if result.check is False:
        return f"La vérification a échoué. {result.detail}"
    if not result.ok:
        return "L'action n'a pas abouti. Je cherche une autre solution."
    return None


def start_speech(title: str, provider_label: str) -> str:
    return f"Très bien. Je lance {title}, avec {provider_label}."


def done_speech(run: RunResult, stopped: bool) -> str:
    """Annonce du verdict."""
    if stopped and run.summary == STOPPED_SUMMARY:
        return "Mission arrêtée, comme demandé. Le rapport des étapes réalisées est prêt."
    if run.status == "PASS":
        return "Mission accomplie : le test est réussi. Les tests pytest et Robot Framework sont prêts."
    if run.status == "FAIL":
        return _cut(f"Le test a échoué. {run.summary}")
    return _cut(f"Je n'ai pas pu aller au bout. {run.summary}")


def _cut(text: str) -> str:
    return text if len(text) <= MAX_SPOKEN else text[: MAX_SPOKEN - 1].rsplit(" ", 1)[0] + "…"
