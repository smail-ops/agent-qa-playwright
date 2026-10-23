"""Ce qu'Alfred affiche et dit, en français, à partir des actions de l'agent.

Deux registres : un libellé court pour le journal (« Clic sur le bouton
« Login » ») et une phrase à la première personne, lue à voix haute
(« Je clique sur le bouton « Login ». »). Les mots de passe ne sont jamais
affichés ni prononcés.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.actions import STATE_LABELS, du, jusqua, key_label
from agent.runner import STOPPED_SUMMARY

if TYPE_CHECKING:
    from agent.actions import Action
    from agent.browser import Element, Observation
    from agent.runner import RunResult, Step

MAX_SPOKEN = 220  # au-delà, la phrase lue à voix haute est coupée

# Nature probable de l'échec, selon le diagnostic du modèle
_NATURE_SPEECH = {"bug": "d'un bug de l'application", "test": "d'un problème du test lui-même",
                  "environnement": "d'un problème d'environnement, par exemple le site indisponible"}

# Accord au féminin des états vérifiés (« la liste est grisée »)
_STATE_FEMININE = {"enabled": "active", "disabled": "grisée", "checked": "cochée", "unchecked": "décochée"}


def q(text: str) -> str:
    """Guillemets français avec espaces insécables : « inventory » ne se coupe pas en fin de ligne."""
    return f"«\u00a0{text}\u00a0»"


def find_element(action: Action, observation: Observation | None, arg: str = "ref") -> Element | None:
    """Élément visé par l'action (ou, avec arg="to", celui où déposer), retrouvé grâce à son numéro."""
    if observation is None or arg not in action.args:
        return None
    return next((e for e in observation.elements if e.ref == action.args[arg]), None)


def is_secret(element: Element | None) -> bool:
    return element is not None and element.tag == "input" and (element.type or "").lower() == "password"


def element_name(element: Element | None) -> str:
    """« le champ « Username » », « le bouton « Login » », « le lien « Panier » »..."""
    if element is None:
        return "l'élément demandé"
    attrs = element.attrs or {}
    text = (element.text or "").strip()
    kind = (element.type or "").lower()
    if element.tag == "input" and kind in ("checkbox", "radio"):
        name = attrs.get("label") or attrs.get("aria") or attrs.get("name") or attrs.get("id") or attrs.get("test_id")
        noun = "la case" if kind == "checkbox" else "l'option"
        return f"{noun} {q(name)}" if name else noun
    if element.tag in ("input", "textarea") and kind not in ("submit", "button", "reset"):
        name = (attrs.get("label") or attrs.get("aria") or attrs.get("placeholder") or attrs.get("name")
                or attrs.get("id") or attrs.get("test_id"))
        return f"le champ {q(name)}" if name else "le champ"
    if element.tag == "select":
        name = attrs.get("label") or attrs.get("aria") or attrs.get("name") or attrs.get("id") or attrs.get("test_id")
        return f"la liste {q(name)}" if name else "la liste"
    if element.editable() or element.is_field():  # zone de texte riche, liste « maison » (une div focalisable)
        noun, _ = element.noun()
        return f"{noun} {q(element.label())}"
    # Un bouton dont le texte n'est qu'une icône (⚙, ×) se nomme par son aria-label
    shown = text if any(ch.isalnum() for ch in text) else ""
    label = shown or attrs.get("aria") or attrs.get("title") or text or attrs.get("test_id") or attrs.get("id")
    kind, _ = element.noun()  # le bouton, le lien, l'option d'une liste « maison », l'onglet...
    return f"{kind} {q(label)}" if label else kind


def state_word(state: str, element: Element | None) -> str:
    """« grisé » ou « grisée », selon le nom de l'élément."""
    feminine = element_name(element).startswith(("la ", "l'option"))
    return _STATE_FEMININE[state] if feminine else STATE_LABELS[state]


def times(count: int) -> str:
    return "une fois" if count == 1 else f"{count} fois"


def action_label(action: Action | None, element: Element | None = None, target: Element | None = None) -> str:
    """Libellé court d'une action, pour le journal de mission. target : l'élément où déposer (glisser-déposer)."""
    if action is None:
        return "Réponse illisible du modèle"
    args = action.args
    if action.name == "click" and args.get("double"):
        return f"Double-clic sur {element_name(element)}"
    if action.name == "click" and args.get("button") == "right":
        return f"Clic droit sur {element_name(element)}"
    if action.name == "press":
        where = f" dans {element_name(element)}" if "ref" in args else ""
        return f"Touche {q(key_label(args['key']))}{where}"
    if action.name == "hover":
        return f"Survol {du(element_name(element))}"
    if action.name == "scroll":
        if "ref" in args:
            return f"Défilement {jusqua(element_name(element))}"
        return "Défilement jusqu'en bas de la page" if args["to"] == "bottom" else "Retour en haut de la page"
    if action.name == "upload":
        return f"Fichier {q(args['file'])} joint dans {element_name(element)}"
    if action.name == "drag":
        return f"Glisser-déposer {du(element_name(element))} sur {element_name(target)}"
    if action.name == "switch_tab":
        return f"Passage à l'onglet n°{args['index']}"
    if action.name == "goto":
        return f"Ouverture de {args['url']}"
    if action.name == "cookies":
        return f"Bandeau de cookies accepté : {q(args['bouton'])}"
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
    if action.name == "check_order":
        return "Vérification de l'ordre : " + ", puis ".join(q(text) for text in args["texts"])
    if action.name == "check_count":
        if args["count"] == 0:
            return f"Vérification : {q(args['text'])} n'apparaît pas"
        return f"Vérification : {q(args['text'])} apparaît {times(args['count'])}"
    if action.name == "wait":
        if "text" in args:
            return f"Attente de {q(args['text'])}, {args['seconds']} s au plus"
        return f"Pause de {args['seconds']} s"
    if action.name == "dialog":
        answer = "OK" if args["accept"] else "Annuler"
        typed = f", avec la saisie {q(args['text'])}" if "text" in args else ""
        return f"Réponse prévue à la prochaine fenêtre : {answer}{typed}"
    if action.name == "check_value":
        if is_secret(element):
            return f"Vérification du mot de passe saisi dans {element_name(element)}"
        if args.get("regex"):
            return f"Vérification : {element_name(element)} suit le motif {q(args['value'])}"
        if args["value"] == "":
            return f"Vérification : {element_name(element)} est vide"
        verb = "indique" if element is not None and element.tag == "select" else "contient"
        return f"Vérification : {element_name(element)} {verb} {q(args['value'])}"
    if action.name == "check_state":
        return f"Vérification : {element_name(element)} est {state_word(args['state'], element)}"
    if action.name == "check_visual":
        return f"Vérification visuelle (avis de l'IA) : {q(args['expected'])}"
    return action.name


def action_speech(action: Action, element: Element | None = None, number: int = 0) -> str:
    """Ce qu'Alfred annonce avant d'agir."""
    args = action.args
    if action.name == "click" and args.get("double"):
        return f"Je double-clique sur {element_name(element)}."
    if action.name == "click" and args.get("button") == "right":
        return f"Je fais un clic droit sur {element_name(element)}."
    if action.name == "press":
        return f"J'appuie sur la touche {key_label(args['key'])}."
    if action.name == "hover":
        return f"Je survole {element_name(element)}."
    if action.name == "scroll":
        return "Je fais défiler la page." if args.get("to") != "top" else "Je remonte en haut de la page."
    if action.name == "upload":
        return f"Je joins le fichier {q(args['file'])}."
    if action.name == "drag":
        return f"Je glisse {element_name(element)} pour le déposer."
    if action.name == "switch_tab":
        return f"Je passe sur l'onglet {args['index']}."
    if action.name == "goto":
        return "J'ouvre la page du cas de test." if number == 1 else "J'ouvre une nouvelle page."
    if action.name == "cookies":
        return "J'accepte le bandeau de cookies."
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
    if action.name == "check_order":
        texts = args["texts"]
        if len(texts) == 2:
            return f"Je vérifie que {q(texts[0])} apparaît avant {q(texts[1])}."
        return f"Je vérifie l'ordre de {len(texts)} éléments, en commençant par {q(texts[0])}."
    if action.name == "check_count":
        if args["count"] == 0:
            return f"Je vérifie que {q(args['text'])} n'apparaît pas."
        return f"Je vérifie que {q(args['text'])} apparaît {times(args['count'])}."
    if action.name == "wait":
        if "text" in args:
            return f"J'attends que {q(args['text'])} s'affiche."
        return f"J'attends {args['seconds']} secondes."
    if action.name == "dialog":
        return f"À la prochaine fenêtre, je répondrai {'OK' if args['accept'] else 'Annuler'}."
    if action.name == "check_value":
        if is_secret(element):
            return "Je vérifie le mot de passe saisi."
        if args.get("regex"):
            return f"Je vérifie le format de {element_name(element)}."
        if args["value"] == "":
            return f"Je vérifie que {element_name(element)} est vide."
        return f"Je vérifie le contenu de {element_name(element)}."
    if action.name == "check_state":
        return f"Je vérifie que {element_name(element)} est {state_word(args['state'], element)}."
    if action.name == "check_visual":
        return f"Je regarde l'écran : {args['expected']}."
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
    if result.opened_tab:
        return "Un nouvel onglet s'est ouvert : je continue dedans."
    if result.dialog is not None:  # fenêtre JavaScript ouverte par l'action
        answer = "OK" if result.dialog["accepted"] else "Annuler"
        return _cut(f"Une fenêtre affichait {q(' '.join(str(result.dialog['message']).split()))}. J'ai répondu {answer}.")
    return None


def memory_speech(steps: int) -> str:
    """Annonce du rejeu d'un parcours mémorisé."""
    return f"Je connais déjà ce parcours, en {steps} étape{'s' if steps > 1 else ''}. Je le rejoue sans faire appel à l'IA."


def heal_speech(number: int, provider_label: str) -> str:
    """Annonce de l'auto-réparation : le site a changé, le modèle prend le relais."""
    return f"Le site a changé à l'étape {number}. Je demande à {provider_label} de trouver le nouveau chemin."


def analysis_speech() -> str:
    return "J'analyse l'échec pour rédiger la fiche d'anomalie."


def wait_speech(seconds: float) -> str:
    """Annonce d'une pause imposée par le quota de l'API."""
    n = max(1, round(seconds))
    return f"Le quota du modèle est atteint pour cette minute. Je patiente {n} seconde{'s' if n > 1 else ''}, puis je reprends."


def start_speech(title: str, provider_label: str) -> str:
    return f"Très bien. Je lance {title}, avec {provider_label}."


def done_speech(run: RunResult, stopped: bool) -> str:
    """Annonce du verdict."""
    if stopped and run.summary == STOPPED_SUMMARY:
        return "Mission arrêtée, comme demandé. Le rapport des étapes réalisées est prêt."
    if run.status == "PASS" and run.healed_at is not None:
        return "Mission accomplie. J'ai réparé le parcours et mis ma mémoire à jour. Les tests générés sont prêts."
    if run.status == "PASS" and run.memory_replayed and run.model_calls == 0:
        return "Mission accomplie, sans aucun appel à l'IA : j'ai rejoué le parcours en mémoire."
    if run.status == "PASS":
        return "Mission accomplie : le test est réussi. Les tests pytest, Robot Framework et Gherkin sont prêts."
    opening = "Le test a échoué." if run.status == "FAIL" else "Je n'ai pas pu aller au bout."
    sheet = "La fiche d'anomalie est prête." if run.status == "FAIL" else "L'analyse du blocage est prête."
    if run.diagnosis:
        return _cut(f"{opening} D'après mon analyse, il s'agit probablement {_NATURE_SPEECH[run.diagnosis['nature']]}. {sheet}")
    if run.anomaly_path is not None:
        return _cut(f"{opening} {sheet} {run.summary}")
    return _cut(f"{opening} {run.summary}")


def _cut(text: str) -> str:
    return text if len(text) <= MAX_SPOKEN else text[: MAX_SPOKEN - 1].rsplit(" ", 1)[0] + "…"


# --- Propositions : user stories et cas de test -------------------------------------------


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'s' if count > 1 else ''}"


def stories_speech(analysis, count: int) -> str:
    """Après l'analyse d'un site : ce qu'Alfred a vu, et ce qu'il propose."""
    pages = len(analysis.pages)
    text = f"J'ai analysé {_plural(pages, 'page')} et je vous propose {count} user stor{'ies' if count > 1 else 'y'}."
    if analysis.username and not analysis.logged_in:
        text += " La connexion avec le compte de test n'a pas abouti : je n'ai vu que les pages publiques."
    return text + " Cochez celles que vous voulez garder."


def cases_speech(count: int, story: str = "") -> str:
    target = f" pour {q(story)}" if story else ""
    if count == 1:
        return f"Voici un cas de test{target}. Gardez-le s'il vous convient."
    return f"Voici {count} cas de test{target}. Gardez ceux qui vous conviennent."


def saved_speech(stories: int, cases: int) -> str:
    parts = []
    if stories:
        parts.append(f"{stories} user stor{'ies' if stories > 1 else 'y'} en Gherkin")
    if cases:
        parts.append(f"{cases} cas de test")  # « cas » ne prend pas de s
    return f"C'est enregistré : {' et '.join(parts)}, dans le dossier cas de test. Vous pouvez les lancer."
