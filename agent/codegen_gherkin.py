"""Génération d'un scénario Gherkin à partir d'une exécution, et de sa version exécutable.

Gherkin est le langage des scénarios BDD (Cucumber, Squash TM, Behave) : des
phrases que toute l'équipe peut lire, introduites par « Étant donné que » (le
contexte), « Quand » (les actions) et « Alors » (les résultats attendus).

    # language: fr
    Fonctionnalité: CT-01 Connexion avec un utilisateur valide

      @CT-01
      Scénario: CT-01 Connexion avec un utilisateur valide
        Étant donné que je suis sur la page "https://www.saucedemo.com/"
        Quand je saisis "standard_user" dans le champ "Username"
        Et je clique sur le bouton "Login"
        Alors l'URL contient "inventory"
        Et la page affiche "Products"

Robot Framework comprend ces mots-clés en français (ligne « language: fr ») :
le même scénario est écrit dans un fichier .robot, avec un mot-clé pour chaque
phrase. Les valeurs entre guillemets sont des arguments du mot-clé, qui rejoue
l'étape avec SeleniumLibrary. Le scénario Gherkin devient exécutable, sans IA.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .actions import STATE_LABELS
from .codegen_robot import _COUNT, _HIDES, _ORDER, _SHOWS, WINDOW_SIZE, _state_keyword, robot_locator

if TYPE_CHECKING:
    from .browser import Element
    from .runner import RunResult

SEP = "    "
_FEMININE = {"enabled": "active", "disabled": "grisée", "checked": "cochée", "unchecked": "décochée"}
_CODE = re.compile(r"^([A-Za-z]{2,}-\d+)\b")  # identifiant du cas de test, repris en étiquette (@CT-01)

_HEADER = """language: fr

*** Settings ***
Documentation     Scénario Gherkin généré automatiquement par agent-qa-playwright.
...               Cas de test : {title}
...               Même scénario que {feature} : Robot Framework comprend
...               Étant donné que, Quand, Alors et Et. Chaque phrase est un mot-clé, plus bas.
Library           SeleniumLibrary    timeout=${{DELAI}}    implicit_wait=${{DELAI}}
Test Teardown     Close Browser

*** Variables ***
${{BROWSER}}        chrome
# Attente maximale d'un élément, d'un texte ou d'une URL.
${{DELAI}}          5s

*** Test Cases ***
{name}
{body}

*** Keywords ***
"""

# Phrases communes à tous les scénarios : mot-clé -> lignes du mot-clé, et blocs dont il a besoin
_COMMON = {
    "open": ('je suis sur la page "${url}"',
             ["Open Browser    ${url}    ${BROWSER}", f"Set Window Size    {WINDOW_SIZE[0]}    {WINDOW_SIZE[1]}"], None),
    "goto": ('je vais sur la page "${url}"', ["Go To    ${url}"], None),
    "shows": ('la page affiche "${texte}"', ["La page affiche    ${texte}"], _SHOWS),
    "hides": ('la page n\'affiche pas "${texte}"', ["La page n'affiche pas    ${texte}"], _HIDES),
    "url": ('l\'URL contient "${texte}"', ["Wait Until Location Contains    ${texte}"], None),
    "no_url": ('l\'URL ne contient pas "${texte}"', ["Wait Until Location Does Not Contain    ${texte}"], None),
    # Les textes arrivent en une chaîne : "A", "B" et "C". On les sépare avant de vérifier l'ordre.
    "order": ("la page affiche dans cet ordre : ${textes}",
              ["${liste}=    Evaluate    re.findall(r'\"([^\"]*)\"', $textes)    modules=re",
               "La page affiche dans l'ordre    @{liste}"], _ORDER),
    "count": ('la page affiche ${nombre:\\d+} fois "${texte}"', ["La page affiche ce nombre de fois    ${texte}    ${nombre}"],
              _COUNT),
    # Vérification visuelle faite par l'IA sur une capture : signalée dans le journal, à automatiser
    "visual": ('l\'écran montre "${constat}"',
               ["Log    Vérification visuelle faite par l'IA, à automatiser : ${constat}    level=WARN"], None),
}


def feature_filename(run: RunResult) -> str:
    return f"{run.case.slug}.feature"


def bdd_filename(run: RunResult) -> str:
    return f"{run.case.slug}_bdd.robot"


def _clean(text: object) -> str:
    """Une seule ligne, espaces regroupés : une phrase Gherkin tient sur une ligne."""
    return " ".join(str(text).split())


def _safe(name: str) -> str:
    """Nom d'élément utilisable dans le nom d'un mot-clé Robot : ni variable, ni échappement."""
    return _clean(name).replace("\\", "/").replace("{", "(").replace("}", ")")


def _quote(value: str) -> str:
    """Valeur entre guillemets, sur une ligne. Les espaces sont gardés tels quels : c'est la donnée de test."""
    return '"' + re.sub(r"[\r\n\t]+", " ", str(value)) + '"'


def _cell(text: str) -> str:
    """Écrit une phrase dans une cellule Robot : \\, ${...} et les espaces multiples restent du texte."""
    text = text.replace("\\", "\\\\")
    text = re.sub(r"([$@&%])\{", r"\\\1{", text)
    return re.sub(r" {2,}", lambda m: " " + "${SPACE}" * (len(m.group()) - 1), text)


class _Names:
    """Un nom unique par élément, repris dans le scénario et dans le nom du mot-clé Robot."""

    def __init__(self) -> None:
        self._names: dict[tuple, tuple[str, bool]] = {}
        self._taken: set[str] = set()

    @staticmethod
    def _key(name: str) -> str:  # Robot ignore la casse, les espaces et les _ des noms de mots-clés
        return re.sub(r"[\s_]", "", name.lower())

    def get(self, element: Element) -> tuple[str, bool]:
        key = (element.selector, element.nth)
        if key not in self._names:
            noun, feminine = element.noun()
            base = f'{noun} "{_safe(element.label())}"'
            if element.nth is not None:  # plusieurs éléments identiques : « le bouton "Ajouter" n°2 »
                base += f" n°{element.nth + 1}"
            name, index = base, 2
            while self._key(name) in self._taken:
                name, index = f"{base} ({index})", index + 1
            self._taken.add(self._key(name))
            self._names[key] = (name, feminine)
        return self._names[key]


class _Scenario:
    """Phrases du scénario, et mots-clés Robot qui les rejouent."""

    def __init__(self) -> None:
        self.sentences: list[tuple[str, str]] = []  # (Étant donné que | Quand | Alors | Et, phrase)
        self.keywords: dict[str, list[str]] = {}  # nom du mot-clé -> lignes
        self.blocks: list[str] = []  # mots-clés de vérification de codegen_robot
        self._last: str | None = None

    def add(self, part: str, sentence: str) -> None:
        """part : contexte, action ou résultat. Deux phrases du même type à la suite : « Et »."""
        keyword = {"contexte": "Étant donné que", "action": "Quand", "résultat": "Alors"}[part]
        self.sentences.append(("Et" if part == self._last else keyword, sentence))
        self._last = part

    def define(self, name: str, lines: list[str], block: str | None = None) -> None:
        self.keywords.setdefault(name, lines)
        if block is not None and block not in self.blocks:
            self.blocks.append(block)

    def common(self, kind: str, part: str, sentence: str) -> None:
        name, lines, block = _COMMON[kind]
        self.define(name, lines, block)
        self.add(part, sentence)


def _build(run: RunResult) -> _Scenario:
    scenario, names = _Scenario(), _Names()
    opened = False
    for step in run.steps:
        action, result = step.action, step.result
        if action is None or not result.ok:
            continue  # comme les autres tests générés : seulement ce qui a fonctionné
        args = action.args
        if action.name == "goto":
            if opened:
                scenario.common("goto", "action", f"je vais sur la page {_quote(args['url'])}")
            else:
                scenario.common("open", "contexte", f"je suis sur la page {_quote(args['url'])}")
                opened = True
            continue
        if not opened:  # le navigateur doit être ouvert avant toute autre étape
            scenario.common("open", "contexte", f"je suis sur la page {_quote(run.case.url)}")
            opened = True

        if action.name == "check_text":
            if args.get("absent"):
                scenario.common("hides", "résultat", f"la page n'affiche pas {_quote(args['text'])}")
            else:
                scenario.common("shows", "résultat", f"la page affiche {_quote(result.value or args['text'])}")
        elif action.name == "check_url":
            kind, verb = ("no_url", "ne contient pas") if args.get("absent") else ("url", "contient")
            scenario.common(kind, "résultat", f"l'URL {verb} {_quote(args['contains'])}")
        elif action.name == "check_order":
            texts = [_quote(text) for text in args["texts"]]
            scenario.common("order", "résultat", f"la page affiche dans cet ordre : {', '.join(texts[:-1])} et {texts[-1]}")
        elif action.name == "check_count":
            scenario.common("count", "résultat", f"la page affiche {args['count']} fois {_quote(args['text'])}")
        elif action.name == "check_visual":
            scenario.common("visual", "résultat", f"l'écran montre {_quote(args['expected'])}")
        else:
            _element_step(scenario, names, action, result)
    return scenario


def _element_step(scenario: _Scenario, names: _Names, action, result) -> None:
    """Étape sur un élément : la phrase cite l'élément par son nom, le mot-clé connaît son localisateur."""
    element, args = result.element, action.args
    if element is None:
        return
    name, feminine = names.get(element)
    locator = robot_locator(element)
    if action.name == "click":
        sentence = f"je clique sur {name}"
        scenario.define(sentence, [f"Click Element    {locator}"])
        scenario.add("action", sentence)
    elif action.name == "fill":
        scenario.define(f'je saisis "${{texte}}" dans {name}', [f"Input Text    {locator}    ${{texte}}"])
        scenario.add("action", f"je saisis {_quote(args['text'])} dans {name}")
    elif action.name == "select":
        # Libellé (« Prix croissant ») ou valeur (« price ») : les deux sont acceptés, comme pour l'agent
        scenario.define(f'je choisis "${{option}}" dans {name}', [
            f"${{par_libelle}}=    Run Keyword And Return Status    Select From List By Label    {locator}    ${{option}}",
            f"IF    not ${{par_libelle}}    Select From List By Value    {locator}    ${{option}}",
        ])
        scenario.add("action", f"je choisis {_quote(args['value'])} dans {name}")
    elif action.name == "check_value":
        keyword = {"select": "List Selection Should Be", "textarea": "Textarea Value Should Be"}.get(
            element.tag, "Textfield Value Should Be")
        wait = "Wait Until Keyword Succeeds    ${DELAI}    200ms    "
        if args["value"] == "":
            sentence = f"{name} est vide"
            scenario.define(sentence, [f"{wait}{keyword}    {locator}    ${{EMPTY}}"])
            scenario.add("résultat", sentence)
        else:
            verb = "indique" if element.tag == "select" else "contient"
            scenario.define(f'{name} {verb} "${{valeur}}"', [f"{wait}{keyword}    {locator}    ${{valeur}}"])
            scenario.add("résultat", f"{name} {verb} {_quote(args['value'])}")
    elif action.name == "check_state":
        state = args["state"]
        sentence = f"{name} est {_FEMININE[state] if feminine else STATE_LABELS[state]}"
        keyword, *extra = _state_keyword(element, state)
        scenario.define(sentence, ["Wait Until Keyword Succeeds    ${DELAI}    200ms    " + SEP.join([keyword, locator, *extra])])
        scenario.add("résultat", sentence)


def generate_feature(run: RunResult) -> str:
    """Le scénario au format Gherkin, en français."""
    scenario = _build(run)
    match = _CODE.match(run.case.title)
    lines = [
        "# language: fr",
        "# Scénario généré automatiquement par agent-qa-playwright, à partir des actions réussies de l'agent.",
        f"# Version exécutable avec Robot Framework : robot {bdd_filename(run)}",
        f"Fonctionnalité: {_clean(run.case.title)}",
        "",
    ]
    if match:
        lines.append(f"  @{match.group(1).upper()}")
    lines.append(f"  Scénario: {_clean(run.case.title)}")
    lines += [f"    {keyword} {sentence}" for keyword, sentence in scenario.sentences]
    return "\n".join(lines) + "\n"


def generate_bdd_robot(run: RunResult) -> str:
    """Le même scénario, exécutable avec Robot Framework et SeleniumLibrary."""
    scenario = _build(run)
    match = _CODE.match(run.case.title)
    body = [f"{SEP}[Tags]{SEP}{match.group(1).upper()}"] if match else []
    body += [f"{SEP}{_cell(f'{keyword} {sentence}')}" for keyword, sentence in scenario.sentences]
    code = _HEADER.format(
        title=_cell(_clean(run.case.title)),
        feature=feature_filename(run),
        name=_cell(_clean(run.case.title)),
        body="\n".join(body) or f"{SEP}No Operation",
    )
    keywords = [name + "\n" + "\n".join(SEP + line for line in lines) + "\n" for name, lines in scenario.keywords.items()]
    return code + "\n".join(keywords + scenario.blocks)


def write_gherkin(run: RunResult) -> None:
    """Écrit le scénario .feature et sa version Robot Framework dans le dossier du rapport."""
    feature, bdd = run.out_dir / feature_filename(run), run.out_dir / bdd_filename(run)
    feature.write_text(generate_feature(run), encoding="utf-8")
    bdd.write_text(generate_bdd_robot(run), encoding="utf-8")
    run.feature_path, run.bdd_path = feature, bdd
