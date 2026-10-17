"""Rapport d'exécution en Markdown, lisible directement sur GitHub."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .actions import STATE_LABELS
from .anomalie import GRAVITES, NATURES

if TYPE_CHECKING:
    from .runner import RunResult, Step

STATUS_LABELS = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "BLOQUE": "⚠️ BLOQUÉ"}
VISION_LABELS = {True: "oui : le modèle voyait aussi la capture d'écran", False: "non"}


def _cell(text: object) -> str:
    """Nettoie un texte pour une cellule de tableau Markdown."""
    return str(text).replace("|", "\\|").replace("\n", " ").strip() or " "


def _target(step: Step) -> str:
    action, result = step.action, step.result
    if action is None:
        return " "
    args = action.args
    if result.element is not None:
        selector = result.element.selector + (f" (n°{result.element.nth + 1})" if result.element.nth is not None else "")
        if "state" in args:
            return f"`{selector}` : {STATE_LABELS[args['state']]}"
        if action.name == "check_value" and args["value"] == "":
            return f"`{selector}` ← (vide)"
        value = args.get("text") or args.get("value")
        return f"`{selector}`" + (f" ← « {value} »" if value else "")
    if "texts" in args:  # check_order
        return " puis ".join(f"« {text} »" for text in args["texts"])
    if "count" in args:  # check_count
        return f"« {args['text']} » ({args['count']} fois)"
    for key in ("url", "text", "contains", "expected", "ref"):
        if key in action.args:
            suffix = " (absent)" if action.args.get("absent") else ""
            return f"« {action.args[key]} »{suffix}"
    return " "


def memory_note(run: RunResult) -> str | None:
    """Ce que la mémoire des parcours a apporté à cette exécution, en une phrase."""
    if run.healed_at is not None:
        note = f"Auto-réparation : le parcours mémorisé ne fonctionnait plus à l'étape {run.healed_at}, le modèle a pris le relais"
        return note + (". Le parcours réparé est enregistré." if run.memory_saved else ".")
    if run.memory_replayed:
        count = run.memory_replayed
        return f"Parcours rejoué depuis la mémoire, sans IA ({count} étape{'s' if count > 1 else ''})."
    if run.memory_saved:
        return "Parcours enregistré : la prochaine exécution se fera sans IA."
    return None


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
        f"| Appels au modèle | {'aucun (démo sans IA)' if run.provider == 'scripted' else run.model_calls} |",
        f"| Vision | {VISION_LABELS[run.vision]} |",
        f"| Date | {run.started_at:%d/%m/%Y %H:%M} |",
        f"| Durée | {run.duration_s:.0f} s |",
        f"| Actions | {len(run.steps)} |",
    ]
    if memory_note(run):
        lines.append(f"| Mémoire | {_cell(memory_note(run))} |")
    lines += [
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
        if step.source == "memoire":
            reason = f"Mémoire : {reason}"
        capture = f"[voir]({step.screenshot})" if step.screenshot else " "
        lines.append(
            f"| {step.number} | {action} | {_cell(_target(step))} | {_cell(_outcome(step))} | {_cell(reason)} | {capture} |"
        )

    lines += ["", "## Vérifications", ""]
    if run.checks:
        lines += [f"- {'✅' if s.result.check else '❌'} {s.result.detail}" for s in run.checks]
    else:
        lines.append("- Aucune vérification réalisée.")

    if run.anomaly_path is not None:
        lines += ["", "## Analyse de l'échec", ""]
        if run.diagnosis:
            nature, gravite = NATURES[run.diagnosis["nature"]], GRAVITES[run.diagnosis["gravite"]].lower()
            lines += [f"- Nature probable, selon l'IA : **{nature}**, gravité {gravite}.", f"- {run.diagnosis['explication']}"]
        lines.append(f"- [Fiche d'anomalie]({run.anomaly_path.name}) : étapes pour reproduire, résultat obtenu et attendu, capture.")

    generated = [
        (run.script_path, "pytest et Playwright", "pytest"),
        (run.robot_path, "Robot Framework et Selenium", "robot"),
        (run.feature_path, "scénario Gherkin en français (BDD), à lire ou à reprendre dans Squash TM ou Cucumber", None),
        (run.bdd_path, "le scénario Gherkin, exécutable avec Robot Framework", "robot"),
    ]
    generated = [(path, tools, command) for path, tools, command in generated if path is not None]
    if generated:
        lines += ["", "## Tests générés", "", "Ils rejouent les actions réussies de l'agent, sans IA :", ""]
        lines += [f"- [`{path.name}`]({path.name}) : {tools}" for path, tools, _ in generated]
        lines += ["", "```"] + [f"{command} {path.name}" for path, _, command in generated if command] + ["```"]

    lines += ["", "## Cas de test exécuté", "", "```markdown", run.case.text, "```", ""]
    return "\n".join(lines)


def write_report(run: RunResult) -> None:
    path = run.out_dir / "rapport.md"
    path.write_text(build_report(run), encoding="utf-8")
    run.report_path = path
