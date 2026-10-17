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


def locator_code(element: Element) -> str:
    """Code Playwright qui retrouve l'élément avec un sélecteur lisible."""
    code = f"page.locator({element.selector!r})"
    if element.nth is not None:
        code += f".nth({element.nth})"
    return code


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
        if action.name == "goto":
            lines.append(f"page.goto({args['url']!r})")
        elif action.name == "click":
            lines.append(f"{locator_code(result.element)}.click()")
        elif action.name == "fill":
            lines.append(f"{locator_code(result.element)}.fill({args['text']!r})")
        elif action.name == "select":
            lines.append(f"{locator_code(result.element)}.select_option({args['value']!r})")
        elif action.name == "check_text":
            assertion = "not_to_contain_text" if args.get("absent") else "to_contain_text"
            lines.append(f"expect(page.locator('body')).{assertion}({args['text']!r}, ignore_case=True, use_inner_text=True)")
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
            # Vérification réussie : on reprend la valeur réelle (pour une liste, la valeur de l'option)
            expected = result.value if result.check and result.value is not None else args["value"]
            lines.append(f"expect({locator_code(result.element)}).to_have_value({expected!r})")
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
