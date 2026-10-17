"""Génération d'un test pytest-playwright classique à partir d'une exécution.

L'agent IA sert à automatiser un cas de test la première fois. Ensuite, le
script généré rejoue les mêmes actions sans IA : rapide, gratuit et
déterministe, il peut entrer dans une suite de non-régression.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .browser import Element
    from .runner import RunResult

_TEMPLATE = '''"""Test généré automatiquement par agent-qa-playwright.

Cas de test : {title}
Rejoue les actions réussies de l'agent, sans IA. Lancement : pytest {filename}
"""

import re

from playwright.sync_api import Page, expect


def {function}(page: Page):
{body}
'''


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

    body = "\n".join(f"    {line}" for line in lines) or "    pass"
    return _TEMPLATE.format(
        title=run.case.title,
        filename=script_filename(run),
        function=f"test_{run.case.slug}",
        body=body,
    )


def write_script(run: RunResult) -> None:
    """Écrit le script dans le dossier du rapport."""
    path = run.out_dir / script_filename(run)
    path.write_text(generate_script(run), encoding="utf-8")
    run.script_path = path
