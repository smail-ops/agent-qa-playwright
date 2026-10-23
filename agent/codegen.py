"""Génération d'un test pytest-playwright classique à partir d'une exécution.

L'agent IA sert à automatiser un cas de test la première fois. Ensuite, le
script généré rejoue les mêmes actions sans IA : rapide, gratuit et
déterministe, il peut entrer dans une suite de non-régression.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .browser import Element
    from .runner import RunResult

_TEMPLATE = '''"""Test généré automatiquement par agent-qa-playwright.

Cas de test : {title}
Rejoue les actions réussies de l'agent, sans IA. Lancement : pytest {filename}
"""

{imports}
from playwright.sync_api import Page, expect
{helpers}

def {function}(page: Page):
{body}
'''

# Outils ajoutés au script seulement s'il compte des textes (check_count).
_COUNT_HELPERS = '''

def compte(page: Page, texte: str) -> int:
    """Nombre de fois où le texte apparaît dans la page visible (casse et espaces ignorés)."""
    visible = " ".join(page.locator("body").inner_text().split()).lower()
    return visible.count(" ".join(texte.split()).lower())


def attendre(condition, delai: float = 5.0) -> bool:
    """Attend qu'une condition devienne vraie, comme le fait expect de Playwright."""
    fin = time.monotonic() + delai
    while not condition():
        if time.monotonic() > fin:
            return False
        time.sleep(0.1)
    return True
'''

# Assertion Playwright pour chaque état vérifié par check_state.
_STATE_ASSERTIONS = {
    "enabled": "to_be_enabled()", "disabled": "to_be_disabled()", "checked": "to_be_checked()", "unchecked": "not_to_be_checked()",
}


def _nth(nth: int | None) -> str:
    return "" if nth is None else f".nth({nth})"


def scope_code(frame: list | None) -> str:
    """Code Playwright qui entre dans les cadres (iframes) de l'élément : page.frame_locator('#editeur')."""
    return "page" + "".join(f".frame_locator({hop['css']!r}){_nth(hop['nth'])}" for hop in frame or [])


def locator_code(element: Element) -> str:
    """Code Playwright qui retrouve l'élément avec un sélecteur lisible.

    Dans un composant web, on passe par son hôte : page.locator('generateur-code').locator('#generer').
    Playwright entre seul dans le Shadow DOM.
    """
    code = scope_code(element.frame)
    code += "".join(f".locator({hop['css']!r}){_nth(hop['nth'])}" for hop in element.shadow or [])
    return code + f".locator({element.selector!r}){_nth(element.nth)}"


def _text_code(text: str, frame: list | None, in_shadow: bool, timeout: str = "") -> str:
    """Le texte est affiché : dans la page, dans un cadre, ou dans un composant web (Shadow DOM)."""
    if in_shadow:  # innerText ne contient pas le texte des composants : on cherche l'élément qui l'affiche
        return f"expect(page.get_by_text(re.compile(re.escape({text!r}), re.IGNORECASE)).first).to_be_visible({timeout.lstrip(', ')})"
    return f"expect({scope_code(frame)}.locator('body')).to_contain_text({text!r}, ignore_case=True, use_inner_text=True{timeout})"


def _dialog_code(dialog: dict) -> list[str]:
    """La réponse de l'agent à une fenêtre JavaScript, préparée avant l'action qui l'ouvre."""
    if not dialog["accepted"]:
        answer, said = "dialog.dismiss()", "Annuler"
    elif dialog.get("text") is not None:
        answer, said = f"dialog.accept({dialog['text']!r})", f"OK, avec la saisie « {dialog['text']} »"
    else:
        answer, said = "dialog.accept()", "OK"
    message = " ".join(str(dialog["message"]).split())
    return [f"# Fenêtre « {message} » : réponse {said}", f"page.once('dialog', lambda dialog: {answer})"]


def script_filename(run: RunResult) -> str:
    return f"test_{run.case.slug}.py"


def generate_script(run: RunResult) -> str:
    """Construit le code du test à partir des étapes réussies."""
    lines: list[str] = []
    for step in run.steps:
        action, result = step.action, step.result
        if action is None or not result.ok:
            continue  # on ne rejoue que ce qui a fonctionné
        args = action.args
        if result.dialog is not None:  # Playwright fermerait la fenêtre avec Annuler : on donne la réponse de l'agent
            lines += _dialog_code(result.dialog)
        if action.name == "goto":
            lines.append(f"page.goto({args['url']!r})")
        elif action.name == "click":
            lines.append(f"{locator_code(result.element)}.click()")
        elif action.name == "fill":
            lines.append(f"{locator_code(result.element)}.fill({args['text']!r})")
        elif action.name == "select":
            lines.append(f"{locator_code(result.element)}.select_option({args['value']!r})")
        elif action.name == "wait":
            if "text" not in args:
                lines.append(f"page.wait_for_timeout({args['seconds'] * 1000})")
            else:
                lines.append(_text_code(args["text"], result.frame, result.in_shadow, f", timeout={args['seconds'] * 1000}"))
        elif action.name == "check_text":
            if args.get("absent"):
                lines.append(f"expect(page.locator('body')).not_to_contain_text({args['text']!r}, ignore_case=True, use_inner_text=True)")
            else:
                lines.append(_text_code(args["text"], result.frame, result.in_shadow))
        elif action.name == "check_url":
            assertion = "not_to_have_url" if args.get("absent") else "to_have_url"
            lines.append(f"expect(page).{assertion}(re.compile(re.escape({args['contains']!r})))")
        elif action.name == "check_order":
            # Chaque texte après le précédent ; la casse et les espaces ne comptent pas
            pattern = ".*".join(r"\s+".join(re.escape(word) for word in text.split()) for text in args["texts"])
            lines.append(f"expect(page.locator('body')).to_contain_text(re.compile({pattern!r}, re.IGNORECASE | re.DOTALL), use_inner_text=True)")
        elif action.name == "check_count":
            message = f"« {args['text']} » doit apparaître {args['count']} fois"
            lines.append(f"assert attendre(lambda: compte(page, {args['text']!r}) == {args['count']}), {message!r}")
        elif action.name == "check_value":
            element = result.element
            if args.get("regex"):  # un format, pas une valeur exacte
                expected = f"re.compile({args['value']!r})"
            else:  # vérification réussie : on reprend la valeur réelle (pour une liste, la valeur de l'option)
                expected = repr(result.value if result.check and result.value is not None else args["value"])
            if element.reads_text():  # zone de texte riche, liste « maison » : son texte affiché
                lines.append(f"expect({locator_code(element)}).to_have_text({expected}, use_inner_text=True)")
            elif element.tag == "select" and args.get("regex"):  # le motif porte sur le libellé de l'option choisie
                lines.append(f"expect({locator_code(element)}.locator('option:checked')).to_have_text({expected})")
            else:
                lines.append(f"expect({locator_code(element)}).to_have_value({expected})")
        elif action.name == "check_state":
            lines.append(f"expect({locator_code(result.element)}).{_STATE_ASSERTIONS[args['state']]}")
        elif action.name == "check_visual":  # avis du modèle sur une capture : rien à rejouer sans IA
            expected = " ".join(args["expected"].split())
            lines.append(f"# À automatiser : vérification visuelle faite par l'IA ({'conforme' if result.check else 'non conforme'}) : {expected}")

    counting = any(step.action and step.action.name == "check_count" and step.result.ok for step in run.steps)
    body = "\n".join(f"    {line}" for line in lines) or "    pass"
    return _TEMPLATE.format(
        title=run.case.title,
        filename=script_filename(run),
        imports="import re\nimport time\n" if counting else "import re\n",
        helpers=_COUNT_HELPERS if counting else "",
        function=f"test_{run.case.slug}",
        body=body,
    )


def write_script(run: RunResult) -> None:
    """Écrit le script dans le dossier du rapport."""
    path = run.out_dir / script_filename(run)
    path.write_text(generate_script(run), encoding="utf-8")
    run.script_path = path
