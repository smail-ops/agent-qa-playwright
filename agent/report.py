"""Rapport d'exécution en Markdown, lisible directement sur GitHub."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import RunResult, Step

STATUS_LABELS = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "BLOQUE": "⚠️ BLOQUÉ"}


def _cell(text: object) -> str:
    """Nettoie un texte pour une cellule de tableau Markdown."""
    return str(text).replace("|", "\\|").replace("\n", " ").strip() or " "


def _target(step: Step) -> str:
    action, result = step.action, step.result
    if action is None:
        return " "
    if result.element is not None:
        selector = result.element.selector + (f" (n°{result.element.nth + 1})" if result.element.nth is not None else "")
        value = action.args.get("text") or action.args.get("value")
        return f"`{selector}`" + (f" ← « {value} »" if value else "")
    for key in ("url", "text", "contains", "ref"):
        if key in action.args:
            suffix = " (absent)" if action.args.get("absent") else ""
            return f"« {action.args[key]} »{suffix}"
    return " "


def _outcome(step: Step) -> str:
    if step.result.check is True:
        return "✅ vérifié"
    if step.result.check is False:
        return "❌ échec"
    return "OK" if step.result.ok else f"⚠️ {step.result.detail}"


def build_report(run: RunResult) -> str:
    lines = [
        f"# Rapport : {run.case.title}",
        "",
        "| | |",
        "|---|---|",
        f"| Statut | **{STATUS_LABELS.get(run.status, run.status)}** |",
        f"| Modèle | {run.provider} ({run.model}) |",
        f"| Date | {run.started_at:%d/%m/%Y %H:%M} |",
        f"| Durée | {run.duration_s:.0f} s |",
        f"| Actions | {len(run.steps)} |",
        "",
        f"**Conclusion :** {run.summary or 'aucune'}",
        "",
        "## Étapes",
        "",
        "| N° | Action | Cible | Résultat | Raison donnée par l'agent | Capture |",
        "|---|---|---|---|---|---|",
    ]
    for step in run.steps:
        action = step.action.name if step.action else "réponse illisible"
        reason = step.action.reason if step.action else step.result.detail
        capture = f"[voir]({step.screenshot})" if step.screenshot else " "
        lines.append(
            f"| {step.number} | {action} | {_cell(_target(step))} | {_cell(_outcome(step))} | {_cell(reason)} | {capture} |"
        )

    lines += ["", "## Vérifications", ""]
    if run.checks:
        lines += [f"- {'✅' if s.result.check else '❌'} {s.result.detail}" for s in run.checks]
    else:
        lines.append("- Aucune vérification réalisée.")

    generated = [
        (run.script_path, "pytest et Playwright", "pytest"),
        (run.robot_path, "Robot Framework et Selenium", "robot"),
    ]
    generated = [(path, tools, command) for path, tools, command in generated if path is not None]
    if generated:
        lines += ["", "## Tests générés", "", "Ils rejouent les actions réussies de l'agent, sans IA :", ""]
        lines += [f"- [`{path.name}`]({path.name}) : {tools}" for path, tools, _ in generated]
        lines += ["", "```"] + [f"{command} {path.name}" for path, _, command in generated] + ["```"]

    lines += ["", "## Cas de test exécuté", "", "```markdown", run.case.text, "```", ""]
    return "\n".join(lines)


def write_report(run: RunResult) -> None:
    path = run.out_dir / "rapport.md"
    path.write_text(build_report(run), encoding="utf-8")
    run.report_path = path
