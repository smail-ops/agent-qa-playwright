"""Analyse d'un test en échec, et fiche d'anomalie prête à reporter dans Jira, Mantis ou Squash TM.

Quand un test échoue (FAIL) ou reste bloqué, l'agent rédige une fiche
d'anomalie, comme le ferait un testeur :

- un titre et un résumé ;
- l'environnement (site, navigateur, système) ;
- les étapes pour reproduire, tirées des actions réellement réalisées ;
- le résultat obtenu et le résultat attendu ;
- la capture d'écran au moment de l'échec.

Cette partie ne dépend pas de l'IA. En plus, si un vrai modèle a mené le
test, il reçoit les faits (cas de test, historique, état final de la page)
et propose un diagnostic : bug de l'application, problème du test ou de
l'environnement, avec une gravité et une explication. C'est une proposition,
présentée comme telle : le testeur garde la décision.
"""

from __future__ import annotations

import json
import platform
import re
from typing import TYPE_CHECKING

from . import documentation
from .actions import _extract_json, au, jusqua, key_label
from .masquage import mask
from .prompts import describe_step
from .providers import ProviderError
from .runner import STOPPED_SUMMARY

if TYPE_CHECKING:
    from .providers import Provider
    from .runner import RunResult, Step

NATURES = {"bug": "Bug de l'application", "test": "Problème du test", "environnement": "Problème d'environnement"}
GRAVITES = {"bloquante": "Bloquante", "majeure": "Majeure", "mineure": "Mineure"}
_ALIASES = {"application": "bug", "anomalie": "bug", "défaut": "bug", "env": "environnement", "environment": "environnement",
            "critique": "bloquante", "bloquant": "bloquante", "majeur": "majeure", "mineur": "mineure"}
MAX_FIELD = 600  # caractères gardés par champ du diagnostic

DIAGNOSIS_PROMPT = """Tu es un testeur logiciel expérimenté. Un test automatisé vient d'échouer ou de rester bloqué.
À partir des faits fournis, et seulement d'eux, rédige le diagnostic. Réponds uniquement avec un objet JSON :
{"nature": "bug" | "test" | "environnement", "gravite": "bloquante" | "majeure" | "mineure", "titre": "...", "explication": "...", "suggestion": "..."}

- nature : "bug" si l'application ne se comporte pas comme le cas de test l'attend ; "test" si le cas de test ou l'exécution est en cause (étape mal suivie, mauvais élément, résultat attendu ambigu ou dépassé) ; "environnement" si le site était indisponible, lent ou inhabituel (page d'erreur, maintenance, réseau).
- gravite : "bloquante" si une fonction essentielle est impossible, "majeure" si elle donne un résultat faux, "mineure" sinon.
- titre : titre de la fiche d'anomalie, une phrase courte et factuelle, comme dans Jira.
- explication : deux ou trois phrases qui relient les faits observés à ta conclusion.
- suggestion : la prochaine action conseillée au testeur, en une phrase.
- sources (facultatif) : si des extraits numérotés de la documentation officielle sont fournis et t'ont servi, leurs numéros, par exemple [1, 2].
N'invente aucun fait. Écris en français."""


def needs_analysis(run: RunResult) -> bool:
    """Fiche d'anomalie pour un échec, ou pour un blocage qui ne vient ni d'un arrêt demandé ni du modèle."""
    if run.status == "FAIL":
        return True
    return run.status == "BLOQUE" and run.summary != STOPPED_SUMMARY and not run.summary.startswith("Le modèle n'a pas pu")


def expected_results(case_text: str) -> list[str]:
    """Les lignes de la partie « Résultats attendus » du cas de test."""
    lines, inside = [], False
    for raw in case_text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            inside = bool(re.match(r"#+\s*r[ée]sultats?\s+attendus?", line, flags=re.IGNORECASE))
            continue
        if inside and line:
            lines.append(re.sub(r"^([-*]|\d+[.)])\s+", "", line))
    return lines


def _failure_index(run: RunResult) -> int:
    """Position de l'étape qui fait échouer le test : première vérification en échec, sinon dernière erreur."""
    for index, step in enumerate(run.steps):
        if step.result.check is False:
            return index
    errors = [index for index, step in enumerate(run.steps) if not step.result.ok]
    return errors[-1] if errors else len(run.steps) - 1


def _quoted(value: str) -> str:
    return f"« {value} »"


def reproduction_step(step: Step) -> str | None:
    """Une étape pour reproduire, à l'infinitif comme dans un cas de test. None pour une vérification.

    La réponse à une fenêtre JavaScript accompagne l'action qui l'a ouverte.
    """
    action, result = step.action, step.result
    if action is None or action.name.startswith("check_") or action.name in ("finish", "dialog"):
        return None
    line = _action_line(action, result)
    if line is not None and result.opened_tab:
        line += ", ce qui ouvre un nouvel onglet"
    if line is not None and result.dialog is not None:
        answer = "OK" if result.dialog["accepted"] else "Annuler"
        line += f", puis répondre {answer} à la fenêtre {_quoted(' '.join(str(result.dialog['message']).split()))}"
    return line


def _action_line(action, result) -> str | None:
    args = action.args
    if action.name == "goto":
        return f"Ouvrir {args['url']}"
    if action.name == "cookies":
        return f"Accepter le bandeau de cookies ({_quoted(args['bouton'])})"
    if action.name == "switch_tab":
        return f"Revenir à l'onglet n°{args['index']}"
    if action.name == "scroll" and "ref" not in args:
        return "Faire défiler la page jusqu'en bas" if args["to"] == "bottom" else "Remonter en haut de la page"
    if action.name == "press" and "ref" not in args:
        return f"Appuyer sur la touche {key_label(args['key'])}"
    if action.name == "wait":
        if "text" in args:
            return f"Attendre l'affichage de {_quoted(args['text'])} ({args['seconds']} s au plus)"
        return f"Attendre {args['seconds']} secondes"
    element = result.element
    if element is not None:
        noun = element.noun()[0]
        target = f"{noun} {_quoted(element.label())}"
        if element.frame:
            target += f" du cadre {_quoted(element.frame_label())}"
    else:
        target = f"l'élément [{args.get('ref')}]"
    if action.name == "fill":
        return f"Saisir {_quoted(args['text'])} dans {target}"
    if action.name == "click":
        if args.get("double"):
            return f"Double-cliquer sur {target}"
        if args.get("button") == "right":
            return f"Faire un clic droit sur {target}"
        return f"Cliquer sur {target}"
    if action.name == "press":
        return f"Appuyer sur la touche {key_label(args['key'])} dans {target}"
    if action.name == "hover":
        return f"Survoler {target}"
    if action.name == "scroll":
        return f"Faire défiler la page {jusqua(target)}"
    if action.name == "upload":
        return f"Joindre le fichier {_quoted(args['file'])} {au(target)}"
    if action.name == "drag":
        destination = result.target
        where = f"{destination.noun()[0]} {_quoted(destination.label())}" if destination is not None else f"l'élément [{args['to']}]"
        return f"Glisser {target} sur {where}"
    if action.name == "select":
        return f"Choisir {_quoted(args['value'])} dans {target}"
    return None


def reproduction_steps(run: RunResult) -> list[str]:
    """Les actions réalisées jusqu'à l'échec. L'action qui échoue est gardée, avec son erreur."""
    stop = _failure_index(run)
    lines = []
    for step in run.steps[: stop + 1]:
        line = reproduction_step(step)
        if line is None or (not step.result.ok and step is not run.steps[stop]):
            continue  # une tentative ratée, puis corrigée par l'agent, n'aide pas à reproduire
        if not step.result.ok:
            line += f" (l'action échoue : {step.result.detail})"
        lines.append(line)
    return lines


def _system() -> str:
    if platform.system() == "Darwin" and platform.mac_ver()[0]:
        return f"macOS {platform.mac_ver()[0]}"
    return f"{platform.system()} {platform.release()}".strip()


def technical_error(run: RunResult) -> str | None:
    """Le message de la dernière erreur technique de Playwright pendant le test (délai dépassé, élément masqué...)."""
    errors = [step.result.detail for step in run.steps if not step.result.ok and "Erreur Playwright" in step.result.detail]
    return errors[-1] if errors else None


def read_documentation(run: RunResult) -> documentation.Lecture | None:
    """Pour une erreur technique, les extraits de la documentation officielle de Playwright qui en parlent."""
    error = technical_error(run)
    pages = documentation.error_pages(error) if error else []
    return documentation.read(error, pages) if pages else None


def build_diagnosis_request(run: RunResult, lecture: documentation.Lecture | None = None) -> str:
    """Les faits envoyés au modèle. Comme pendant le test, les données personnelles de la page sont masquées."""
    history = "\n".join(describe_step(step) for step in run.steps)
    failed = [step.result.detail for step in run.steps if step.result.check is False]
    page = run.final_page.to_prompt(max_text=2500) if run.final_page is not None else "(non disponible)"
    facts = (
        f"## Statut du test : {run.status}\n{run.summary}\n\n"
        f"## Actions réalisées\n{history}\n\n"
        f"## Vérifications en échec\n" + ("\n".join(f"- {d}" for d in failed) or "(aucune)") + "\n\n"
        f"## Page à la fin du test\n{page}\n\n"
    )
    docs = ""
    if lecture is not None and lecture.extraits:
        docs = ("## Documentation officielle de Playwright (extraits numérotés)\n\n"
                + "\n\n".join(extrait.to_prompt() for extrait in lecture.extraits) + "\n\n")
    case_text = run.coffre.hide(run.case.text) if run.coffre is not None else run.case.text  # sans mot de passe
    return f"## Cas de test\n{case_text}\n\n{mask(facts, run.case.text)}{docs}Rédige le diagnostic en JSON."


def parse_diagnosis(raw: str) -> dict:
    """Contrôle la réponse du modèle : champs attendus, valeurs connues, longueur raisonnable."""
    try:
        data = json.loads(_extract_json(raw))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"réponse illisible ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError("un objet JSON est attendu")
    nature = str(data.get("nature", "")).strip().lower()
    gravite = str(data.get("gravite", data.get("gravité", ""))).strip().lower()
    nature, gravite = _ALIASES.get(nature, nature), _ALIASES.get(gravite, gravite)
    if nature not in NATURES:
        raise ValueError(f"nature « {nature} » inconnue")
    if gravite not in GRAVITES:
        raise ValueError(f"gravité « {gravite} » inconnue")
    fields = {}
    for name in ("titre", "explication", "suggestion"):
        value = " ".join(str(data.get(name) or "").split())
        if not value:
            raise ValueError(f"champ « {name} » vide")
        fields[name] = value if len(value) <= MAX_FIELD else value[: MAX_FIELD - 1] + "…"
    numbers = data.get("sources")
    cited = [n for n in numbers if isinstance(n, int) and not isinstance(n, bool)] if isinstance(numbers, list) else []
    return {"nature": nature, "gravite": gravite, **fields, "sources": cited}


def _failure_capture(run: RunResult) -> list[tuple[str, bytes]]:
    """Avec la vision, le modèle voit aussi la capture de l'étape qui échoue."""
    if not run.vision or not run.steps:
        return []
    step = run.steps[_failure_index(run)]
    try:
        return [("image/png", (run.out_dir / step.screenshot).read_bytes())] if step.screenshot else []
    except OSError:
        return []


def diagnose(run: RunResult, provider: Provider) -> tuple[dict | None, str]:
    """Demande le diagnostic au modèle. Renvoie (diagnostic, raison s'il manque)."""
    if provider.name == "scripted":
        return None, "démo sans IA : pas de diagnostic du modèle"
    lecture = read_documentation(run)  # erreur technique : la documentation officielle, lue par le programme
    try:
        raw = provider.complete(DIAGNOSIS_PROMPT, build_diagnosis_request(run, lecture), _failure_capture(run), 4000)
        run.model_calls += 1
        diagnosis = parse_diagnosis(raw)
        # Les liens viennent des extraits fournis par le programme : le modèle ne peut citer que ceux-là
        diagnosis["sources"] = documentation.cited(lecture, diagnosis["sources"]) if lecture is not None else []
        return diagnosis, ""
    except ProviderError as exc:  # quota, réseau : la fiche reste utile sans le diagnostic
        return None, f"le modèle n'a pas pu répondre ({str(exc)[:200]})"
    except ValueError as exc:
        return None, f"diagnostic inutilisable : {exc}"


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip() or " "


def build_anomaly(run: RunResult, diagnosis: dict | None, missing: str = "") -> str:
    """La fiche d'anomalie en Markdown."""
    failing = run.steps[_failure_index(run)] if run.steps else None
    failed_checks = [step.result.detail for step in run.steps if step.result.check is False]
    kind = "Fiche d'anomalie" if run.status == "FAIL" else "Analyse du blocage"
    title = diagnosis["titre"] if diagnosis else (failed_checks[0] if failed_checks else run.summary)
    nature = f"{NATURES[diagnosis['nature']]} (avis de l'IA)" if diagnosis else "À qualifier"
    gravite = GRAVITES[diagnosis["gravite"]] if diagnosis else "À qualifier"
    environment = ", ".join([run.browser or "Chromium (Playwright)", "fenêtre 1280 × 800", _system()])
    page = run.final_page

    lines = [
        f"# {kind} : {title}",
        "",
        "| | |",
        "|---|---|",
        f"| Cas de test | {_cell(run.case.title)} |",
        f"| Statut du test | {'❌ FAIL' if run.status == 'FAIL' else '⚠️ BLOQUÉ'} |",
        f"| Gravité proposée | {gravite} |",
        f"| Nature probable | {nature} |",
        f"| Date | {run.started_at:%d/%m/%Y %H:%M} |",
        f"| Site testé | {_cell(run.case.url)} |",
        f"| Environnement | {_cell(environment)} |",
        "",
        "## Résumé",
        "",
        run.summary or "Aucune conclusion.",
        "",
        "## Étapes pour reproduire",
        "",
    ]
    steps = reproduction_steps(run)
    lines += [f"{number}. {line}" for number, line in enumerate(steps, 1)] or ["1. Ouvrir " + run.case.url]

    lines += ["", "## Résultat obtenu", ""]
    if failed_checks:
        lines += [f"- {detail}" for detail in failed_checks]
    elif failing is not None:
        lines.append(f"- {failing.result.detail}")
    if page is not None:
        lines.append(f"- Page affichée à la fin : {page.url} (titre : « {page.title} »)")

    lines += ["", "## Résultat attendu", ""]
    case_text = run.coffre.hide(run.case.text) if run.coffre is not None else run.case.text  # sans mot de passe
    lines += [f"- {line}" for line in expected_results(case_text)] or ["- Voir le cas de test ci-dessous."]

    if failing is not None and failing.screenshot:
        lines += ["", "## Capture au moment de l'échec", "", f"![Étape {failing.number}]({failing.screenshot})"]

    lines += ["", "## Diagnostic proposé par l'IA", ""]
    if diagnosis:
        lines += [
            f"**{NATURES[diagnosis['nature']]}**, gravité {GRAVITES[diagnosis['gravite']].lower()}.",
            "",
            diagnosis["explication"],
            "",
            f"**Suggestion :** {diagnosis['suggestion']}",
            "",
        ]
        if diagnosis.get("sources"):
            lines += ["**Dans la documentation officielle :**", ""]
            lines += [f"- [{source['titre']}]({source['url']})" for source in diagnosis["sources"]] + [""]
        lines.append("_Diagnostic proposé par le modèle à partir des faits ci-dessus : à confirmer par un testeur._")
    else:
        lines.append(f"Pas de diagnostic : {missing or 'non demandé'}.")

    lines += ["", "## Pièces jointes", "", "- [Rapport complet de l'exécution](rapport.md)"]
    for path, label in ((run.script_path, "Test pytest"), (run.robot_path, "Test Robot Framework")):
        if path is not None:
            lines.append(f"- [{label}]({path.name}) : il rejoue le parcours et reproduit l'échec")
    if run.feature_path is not None:
        lines.append(f"- [Scénario Gherkin]({run.feature_path.name}) : le parcours en langage métier, à joindre au ticket")
    lines += ["", "## Cas de test exécuté", "", "```markdown", case_text, "```", ""]
    return "\n".join(lines)


def write_anomaly(run: RunResult, diagnosis: dict | None, missing: str = "") -> None:
    path = run.out_dir / "anomalie.md"
    path.write_text(build_anomaly(run, diagnosis, missing), encoding="utf-8")
    run.anomaly_path, run.diagnosis = path, diagnosis
