"""Import des cas de test dans Squash TM : un fichier Excel au format du modèle d'import officiel.

    python -m agent.squash
    python -m agent.squash cas_de_test/terrain --projet "Recette boutique" --sortie rapports/import_squash_tm.xlsx

Dans Squash TM : espace Cas de test, Importer, format Excel (fichier .xlsx). Le fichier suit le
modèle d'import de Squash TM : onglets TEST_CASES et STEPS, en-têtes de colonnes inchangés,
chemins qui commencent par le nom du projet (/Recette boutique/terrain/parabank/...). Les
dossiers manquants sont créés par Squash TM.

- Un cas Markdown devient un cas de test classique : sa référence est son code (CT-01), ses
  étapes deviennent des pas de test, ses résultats attendus le résultat attendu du dernier pas,
  ses préconditions le prérequis, son adresse la description.
- Un fichier Gherkin (.feature) devient un cas de test Gherkin, avec son script. Sa priorité
  (@priorite-haute) donne l'importance.

Les mots de passe écrits dans les cas ne sont pas recopiés : « [mot de passe] » les remplace,
comme dans tout ce que produit l'agent (voir coffre.py). Le prérequis indique où les trouver.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .coffre import Coffre
from .testcase import parse_test_case

TEST_CASES = ("ACTION", "TC_PATH", "TC_NUM", "TC_REFERENCE", "TC_NAME", "TC_WEIGHT_AUTO", "TC_WEIGHT", "TC_NATURE", "TC_TYPE",
              "TC_STATUS", "TC_DESCRIPTION", "TC_PRE_REQUISITE", "TC_KIND", "TC_SCRIPT", "TC_AUTOMATABLE")
STEPS = ("ACTION", "TC_OWNER_PATH", "TC_STEP_NUM", "TC_STEP_IS_CALL_STEP", "TC_STEP_ACTION", "TC_STEP_EXPECTED_RESULT")
SORTIE = Path("rapports") / "import_squash_tm.xlsx"
_SECTION = re.compile(r"^\s*#{2,}\s*(.+?)\s*$")
_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.*)$")
_CODE = re.compile(r"^\s*([A-Za-z]{2,}-\d+)\b[\s:.-]*")
_PRIORITE = {"haute": "HIGH", "moyenne": "MEDIUM", "basse": "LOW"}


@dataclass
class CasSquash:
    """Un cas de test, tel que Squash TM l'importe."""

    path: str  # /projet/dossier/nom
    reference: str
    description: str  # HTML
    prerequisite: str = ""  # HTML
    steps: list[tuple[str, str]] = field(default_factory=list)  # (action, résultat attendu), en HTML
    kind: str = "STANDARD"
    script: str = ""
    weight: str = "LOW"


def _html(text: str) -> str:
    return html.escape(" ".join(str(text).split()), quote=False)


def _paragraphs(lines: list[str]) -> str:
    return "".join(f"<p>{_html(line)}</p>" for line in lines if line.strip())


def _list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{_html(item)}</li>" for item in items) + "</ul>" if items else ""


def _name(text: str) -> str:
    """Un nom d'élément du chemin Squash TM : sans barre oblique, qui séparerait les dossiers."""
    return " ".join(text.replace("/", "-").replace("\\", "-").split()) or "Cas sans titre"


def sections(text: str) -> dict[str, list[str]]:
    """Les sections d'un cas Markdown : titre de section (sans accents, en minuscules) -> lignes."""
    found: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        heading = _SECTION.match(line)
        if heading:
            current = heading.group(1).lower().replace("é", "e").replace("è", "e").replace("ê", "e")
            found[current] = []
        elif current is not None and line.strip():
            item = _ITEM.match(line)
            found[current].append(item.group(1).strip() if item else line.strip())
    return found


def _first(found: dict[str, list[str]], *names: str) -> list[str]:
    return next((found[key] for key in found for name in names if key.startswith(name)), [])


def from_markdown(path: Path, folder: str, projet: str) -> CasSquash:
    """Un cas Markdown, prêt pour Squash TM. Les mots de passe sont remplacés par un repère."""
    text = path.read_text(encoding="utf-8")
    case = parse_test_case(text)
    coffre = Coffre.from_case(case.text)
    hidden = coffre.hide(case.text)
    code = _CODE.match(case.title)
    reference = code.group(1).upper() if code else ""
    name = case.title[code.end():].strip() if code else case.title
    found = sections(hidden)
    steps = _first(found, "etape", "steps", "actions")
    expected = _first(found, "resultat", "expected")
    prerequisite = _first(found, "precondition", "prerequis", "pre-requis")
    description = [f"Adresse : {case.url}", "Cas de test exécuté par agent-qa-playwright, l'agent de test IA."]
    if case.session:
        description.append(f"Commence déjà connecté : session « {case.session} » de l'agent.")
    if "{unique}" in hidden.lower():
        description.append("{unique} : une valeur nouvelle à chaque exécution (date et heure), pour créer des données "
                           "qui n'existent pas encore.")
    if coffre.secrets:
        prerequisite = prerequisite + ["[mot de passe] : le mot de passe du compte de test, gardé hors des fichiers "
                                       "(fichier .env du poste de test)."]
    pairs = [(f"<p>{_html(step)}</p>", "") for step in steps] or [("<p>Exécuter le cas de test.</p>", "")]
    if expected:
        pairs[-1] = (pairs[-1][0], _list(expected))
    return CasSquash(path=f"/{_name(projet)}/{folder}{_name(name)}", reference=reference,
                     description=_paragraphs(description), prerequisite=_paragraphs(prerequisite), steps=pairs)


def from_feature(path: Path, folder: str, projet: str) -> CasSquash:
    """Un fichier Gherkin, prêt pour Squash TM : un cas de test Gherkin, avec son script."""
    text = path.read_text(encoding="utf-8")
    coffre = Coffre.from_case(text)
    script = coffre.hide(text)
    feature = re.search(r"^\s*(?:Fonctionnalité|Feature)\s*:\s*(.+)$", script, re.MULTILINE | re.IGNORECASE)
    title = feature.group(1).strip() if feature else path.stem
    code = re.search(r"@([A-Za-z]{2,}-\d+)\b", script) or _CODE.match(title)
    reference = code.group(1).upper() if code else ""
    title_code = _CODE.match(title)
    name = title[title_code.end():].strip() if title_code else title
    priority = re.search(r"@priorite-(haute|moyenne|basse)\b", script, re.IGNORECASE)
    url = re.search(r"^\s*#\s*URL\s*:\s*(\S+)", script, re.MULTILINE | re.IGNORECASE)
    description = ([f"Adresse : {url.group(1)}"] if url else []) + ["Scénarios Gherkin exécutés par agent-qa-playwright, "
                                                                     "l'agent de test IA."]
    return CasSquash(path=f"/{_name(projet)}/{folder}{_name(name)}", reference=reference,
                     description=_paragraphs(description), kind="GHERKIN", script=script.strip() + "\n",
                     weight=_PRIORITE.get(priority.group(1).lower(), "LOW") if priority else "LOW")


def collect(source: Path, projet: str) -> tuple[list[CasSquash], list[str]]:
    """Les cas d'un dossier (ou d'un fichier), et les fichiers ignorés avec leur raison."""
    files = [source] if source.is_file() else sorted(p for p in source.rglob("*") if p.suffix in (".md", ".feature"))
    base = source.parent if source.is_file() else source
    cases, skipped = [], []
    for path in files:
        relative = path.parent.relative_to(base).as_posix()
        folder = "" if relative in ("", ".") else "/".join(_name(part) for part in relative.split("/")) + "/"
        try:
            cases.append(from_feature(path, folder, projet) if path.suffix == ".feature" else from_markdown(path, folder, projet))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            skipped.append(f"{path.as_posix()} : {exc}")  # pas un cas de test (campagne, notes...)
    paths = [case.path for case in cases]
    for case in cases:  # deux cas du même nom dans un dossier : la référence les distingue
        if paths.count(case.path) > 1 and case.reference:
            case.path = f"{case.path} ({case.reference})"
    return cases, skipped


def write_xlsx(cases: list[CasSquash], output: Path) -> Path:
    """Le fichier d'import : onglets TEST_CASES et STEPS, en-têtes du modèle de Squash TM."""
    try:
        from openpyxl import Workbook
    except ImportError:
        raise RuntimeError("openpyxl manque : lance pip install -r requirements.txt") from None
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "TEST_CASES"
    sheet.append(TEST_CASES)
    for case in cases:
        sheet.append(["C", case.path, None, case.reference, None, 0, case.weight, "NAT_FUNCTIONAL_TESTING", "TYP_UNDEFINED",
                      "WORK_IN_PROGRESS", case.description, case.prerequisite, case.kind, case.script or None, "Y"])
    steps = workbook.create_sheet("STEPS")
    steps.append(STEPS)
    for case in cases:
        for number, (action, expected) in enumerate(case.steps, 1):
            steps.append(["C", case.path, number, 0, action, expected])
    for worksheet in (sheet, steps):  # largeurs lisibles à l'ouverture dans Excel
        for column in worksheet.columns:
            worksheet.column_dimensions[column[0].column_letter].width = min(60, max(12, *(len(str(c.value or ""))
                                                                                          for c in column[:30])))
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.squash",
                                     description="Prépare l'import des cas de test dans Squash TM (fichier Excel).")
    parser.add_argument("source", nargs="?", default="cas_de_test", help="dossier ou fichier des cas (cas_de_test par défaut)")
    parser.add_argument("--projet", default="agent-qa-playwright",
                        help="nom du projet dans Squash TM, début de chaque chemin (agent-qa-playwright par défaut)")
    parser.add_argument("--sortie", default=str(SORTIE), help=f"fichier Excel produit ({SORTIE.as_posix()} par défaut)")
    args = parser.parse_args(argv)
    source = Path(args.source)
    if not source.exists():
        print(f"Erreur : {source} introuvable. Lance la commande depuis la racine du projet.", file=sys.stderr)
        return 3
    cases, skipped = collect(source, args.projet)
    if not cases:
        print(f"Erreur : aucun cas de test dans {source}.", file=sys.stderr)
        return 3
    try:
        output = write_xlsx(cases, Path(args.sortie))
    except (RuntimeError, OSError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 3
    gherkin = sum(1 for case in cases if case.kind == "GHERKIN")
    steps = sum(len(case.steps) for case in cases)
    print(f"Import Squash TM : {output.as_posix()}")
    print(f"Cas de test      : {len(cases) - gherkin} classiques ({steps} pas de test) et {gherkin} Gherkin")
    print(f"Dans Squash TM   : espace Cas de test, Importer, format Excel. Les chemins commencent par le projet "
          f"« {args.projet} » : il doit exister (sinon, relance avec --projet \"Nom du projet\").")
    for line in skipped:
        print(f"Ignoré           : {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
