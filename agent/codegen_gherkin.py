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

Un mot-clé qui agit dans un cadre (iframe) le sélectionne, puis revient à la
page : chaque phrase se suffit à elle-même.

{unique} reste écrit tel quel dans les phrases : le mot-clé le remplace par une
valeur nouvelle à chaque lancement (${UNIQUE}, créée avant le scénario).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .actions import STATE_LABELS, au, jusqua, key_label
from .codegen_robot import (_ADS, _ALERTS, _CLICK, _CLOSED, _COOKIES, _COOKIES_FRAME, _COUNT, _DRAG, _HIDES, _OPEN, _ORDER,
                            _SECRETS, _SESSION_LOAD, _SESSION_SAVE, _SHOWS, _UNIQUE, _VISIBLE, ADS_VARIABLE, CLOSED_VARIABLE,
                            SECRETS_SETUP, TEXT_BLOCKS, UNIQUE_SETUP, _state_keyword, frame_locator, open_block, robot_arg,
                            robot_locator, robot_variables, secret_cell, selenium_keys, typing_keyword, value_keyword)
from .coffre import env_names, split
from .donnees import UNIQUE, masquer

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
{setup}Test Teardown     Close Browser

{variables}
*** Test Cases ***
{name}
{body}

*** Keywords ***
"""

# Phrases communes à tous les scénarios : mot-clé -> lignes du mot-clé, et blocs dont il a besoin
_COMMON = {
    "open": ('je suis sur la page "${url}"', ["Ouvrir le navigateur    ${url}"], _OPEN),
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
    # Chargement lent : on attend le texte jusqu'au délai donné, ou simplement ce délai
    "wait": ('j\'attends jusqu\'à ${secondes:\\d+} secondes que la page affiche "${texte}"',
             ["Wait Until Keyword Succeeds    ${secondes}s    500ms    Le texte visible contient    ${texte}"], _SHOWS),
    "sleep": ("j'attends ${secondes:\\d+} secondes", ["Sleep    ${secondes}s"], None),
    # Fenêtres JavaScript ouvertes par l'étape précédente : même réponse que l'agent
    "accept": ('j\'accepte la fenêtre "${message}"', ["Accepter la fenêtre    ${message}"], _ALERTS),
    "dismiss": ('je refuse la fenêtre "${message}"', ["Refuser la fenêtre    ${message}"], _ALERTS),
    "prompt": ('je réponds "${texte}" à la fenêtre "${message}"',
               ["Log    Fenêtre : ${message}", "Input Text Into Alert    ${texte}    action=ACCEPT"], None),
    # Onglets : celui qu'ouvre l'étape précédente, ou un onglet déjà ouvert (le premier est le n°1)
    "new_tab": ("je passe sur le nouvel onglet", ["Switch Window    NEW"], None),
    "switch_tab": ("je reviens sur l'onglet n°${numero:\\d+}",
                   ["@{onglets}=    Get Window Handles", "${index}=    Evaluate    int($numero) - 1",
                    "Switch Window    ${onglets}[${index}]"], None),
    "bottom": ("je fais défiler la page jusqu'en bas", ["Execute Javascript    window.scrollTo(0, document.body.scrollHeight)"],
               None),
    "top": ("je remonte en haut de la page", ["Execute Javascript    window.scrollTo(0, 0)"], None),
    # Sessions : commencer déjà connecté, ou garder la session pour les scénarios suivants (dossier sessions/)
    "session_load": ('je reprends la session "${nom}"', ["Reprendre la session    ${nom}"], _SESSION_LOAD),
    "session_save": ('j\'enregistre la session "${nom}"', ["Enregistrer la session    ${nom}"], _SESSION_SAVE),
}

_EMBEDDED = re.compile(r"\$\{(\w+)(?::((?:[^{}]|\{[^{}]*\})*))?\}")  # argument intégré au nom d'un mot-clé : ${nom}, ${n:\d+}


def _keyword_pattern(name: str) -> re.Pattern:
    """L'expression régulière qui reconnaît les phrases de ce mot-clé (arguments intégrés au nom, comme Robot)."""
    parts, last = [], 0
    for match in _EMBEDDED.finditer(name):
        parts.append(re.escape(name[last:match.start()]))
        parts.append(f"({match.group(2)})" if match.group(2) else "(.*?)")
        last = match.end()
    parts.append(re.escape(name[last:]))
    return re.compile("".join(parts), re.IGNORECASE)


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
        key = (element.selector, element.nth, repr(_place(element.frame)), repr(_place(element.shadow)))
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


def _place(hops: list | None) -> tuple:
    return tuple((hop["css"], hop["nth"]) for hop in hops or [])


def _in_frame(frame: list | None, lines: list[str]) -> list[str]:
    """Lignes d'un mot-clé qui agit dans un cadre : sélection du cadre, puis retour à la page."""
    if not frame:
        return lines
    return [f"Select Frame    {robot_arg(frame_locator(hop))}" for hop in frame] + lines + ["Unselect Frame"]


class _Frames:
    """Un nom unique par cadre, repris dans les phrases : le cadre "Éditeur de note"."""

    def __init__(self) -> None:
        self._names: dict[tuple, str] = {}

    def get(self, frame: list) -> str:
        key = _place(frame)
        if key not in self._names:
            base = _safe(frame[-1].get("label") or frame[-1]["css"])
            name, index = base, 2
            while name in self._names.values():
                name, index = f"{base} ({index})", index + 1
            self._names[key] = name
        return self._names[key]


class _Scenario:
    """Phrases du scénario, et mots-clés Robot qui les rejouent."""

    def __init__(self, names: dict | None = None, unique: str | None = None) -> None:
        self.names = names or {}  # repère du mot de passe -> variable du fichier .env
        self.unique = unique  # valeur de {unique} pendant l'exécution de l'agent : les phrases gardent {unique}
        self.secrets = False  # le scénario saisit un mot de passe, lu dans le fichier .env
        self.sentences: list[tuple[str, str]] = []  # (Étant donné que | Quand | Alors | Et, phrase)
        self.keywords: dict[str, list[str]] = {}  # nom du mot-clé -> lignes
        self.blocks: list[str] = []  # mots-clés de vérification de codegen_robot
        self._last: str | None = None

    def quote(self, value: str) -> str:
        """Valeur entre guillemets pour une phrase : la valeur unique de l'agent redevient {unique}."""
        return _quote(masquer(str(value), self.unique))

    @property
    def uses_unique(self) -> bool:
        return any(UNIQUE in sentence for _, sentence in self.sentences)

    def resolve_unique(self) -> None:
        """Les mots-clés dont une phrase contient {unique} le remplacent par ${UNIQUE}, nouveau à chaque lancement."""
        sentences = [sentence for _, sentence in self.sentences if UNIQUE in sentence]
        for name, lines in self.keywords.items():
            arguments = [match.group(1) for match in _EMBEDDED.finditer(name) if not match.group(2)]
            if not arguments or not any(_keyword_pattern(name).fullmatch(sentence) for sentence in sentences):
                continue
            self.keywords[name] = [f'${{{arg}}}=    Evaluate    ${arg}.replace("{UNIQUE}", $UNIQUE)'
                                   for arg in arguments] + lines

    @property
    def last(self) -> str | None:
        """Type de la dernière phrase : contexte, action ou résultat."""
        return self._last

    def add(self, part: str, sentence: str) -> None:
        """part : contexte, action ou résultat. Deux phrases du même type à la suite : « Et »."""
        keyword = {"contexte": "Étant donné que", "action": "Quand", "résultat": "Alors"}[part]
        self.sentences.append(("Et" if part == self._last else keyword, sentence))
        self._last = part

    def define(self, name: str, lines: list[str], block: str | None = None) -> None:
        self.keywords.setdefault(name, lines)
        if block is not None and block not in self.blocks:
            self.blocks.append(block)
        if block in TEXT_BLOCKS and _VISIBLE not in self.blocks:
            self.blocks.append(_VISIBLE)

    def common(self, kind: str, part: str, sentence: str) -> None:
        name, lines, block = _COMMON[kind]
        self.define(name, lines, block)
        self.add(part, sentence)


def _build(run: RunResult) -> _Scenario:
    scenario, names, frames = _Scenario(env_names(run), getattr(run, "unique", None)), _Names(), _Frames()
    opened = False

    def open_page(url: str) -> None:
        nonlocal opened
        scenario.common("open", "contexte", f"je suis sur la page {scenario.quote(url)}")
        if run.case.session:  # déjà connecté : la session enregistrée par un autre scénario
            scenario.common("session_load", "contexte", f"je reprends la session {_quote(run.case.session)}")
        opened = True

    for step in run.steps:
        action, result = step.action, step.result
        if action is None or not result.ok:
            continue  # comme les autres tests générés : seulement ce qui a fonctionné
        args = action.args
        if action.name == "goto":
            if opened:
                scenario.common("goto", "action", f"je vais sur la page {scenario.quote(args['url'])}")
            else:
                open_page(args["url"])
        else:
            if not opened:  # le navigateur doit être ouvert avant toute autre étape
                open_page(run.case.url)
            _action_step(scenario, names, frames, action, result)
        if result.dialog is not None:  # fenêtre JavaScript ouverte par l'étape
            _dialog_step(scenario, result.dialog)
        if result.opened_tab:  # l'étape a ouvert un nouvel onglet : le scénario y passe, comme l'agent
            scenario.common("new_tab", "action", "je passe sur le nouvel onglet")
    if getattr(run, "session_saved", None) is not None and run.case.save_session:  # pour les scénarios déjà connectés
        scenario.common("session_save", "action", f"j'enregistre la session {_quote(run.case.save_session)}")
    if scenario.secrets:  # mots de passe : lus dans le fichier .env avant le scénario
        scenario.blocks.append(_SECRETS)
    opening = 'je suis sur la page "${url}"'
    if run.closed_shadow and opening in scenario.keywords:  # composants web fermés : ouverts avant d'agir
        scenario.keywords[opening] = scenario.keywords[opening] + ["Ouvrir les composants fermés"]
        scenario.blocks.append(_CLOSED)
    if getattr(run, "ads_blocked", 0) and opening in scenario.keywords:  # pas de publicité plein écran
        scenario.keywords[opening] = scenario.keywords[opening] + ["Bloquer les publicités"]
        scenario.blocks.append(_ADS)
    if open_block(run) != _OPEN:  # appareil émulé : « Ouvrir le navigateur » sans taille de fenêtre
        scenario.blocks = [open_block(run) if block == _OPEN else block for block in scenario.blocks]
    if scenario.uses_unique:  # {unique} : une valeur nouvelle à chaque lancement
        scenario.resolve_unique()
        scenario.blocks.append(_UNIQUE)
    return scenario


def _dialog_step(scenario: _Scenario, dialog: dict) -> None:
    message = scenario.quote(_clean(dialog["message"]))
    if dialog["accepted"] and dialog.get("text") is not None:
        scenario.common("prompt", "action", f"je réponds {scenario.quote(dialog['text'])} à la fenêtre {message}")
    elif dialog["accepted"]:
        scenario.common("accept", "action", f"j'accepte la fenêtre {message}")
    else:
        scenario.common("dismiss", "action", f"je refuse la fenêtre {message}")


def _action_step(scenario: _Scenario, names: _Names, frames: _Frames, action, result) -> None:
    """Phrase d'une action réussie, autre que l'ouverture d'une page."""
    args = action.args
    if action.name == "cookies":  # bandeau accepté par le programme : le scénario l'accepte s'il s'affiche
        lines = [f"Accepter le bandeau de cookies    {robot_arg(args['robot'])}"]
        if args.get("cadre"):  # dans un cadre de consentement (iframe)
            lines = [f"Accepter le bandeau de cookies du cadre    {robot_arg(frame_locator(args['cadre'][-1]))}    "
                     f"{robot_arg(args['robot'])}"]
            if _COOKIES_FRAME not in scenario.blocks:
                scenario.blocks.append(_COOKIES_FRAME)
        sentence = "j'accepte le bandeau de cookies"
        if scenario.keywords.get(sentence, lines) != lines:  # un second bandeau, différent (rare)
            sentence += f" {_quote(_safe(args['bouton']))}"
        scenario.define(sentence, lines, _COOKIES)
        scenario.add("contexte" if scenario.last == "contexte" else "action", sentence)
    elif action.name == "switch_tab":
        scenario.common("switch_tab", "action", f"je reviens sur l'onglet n°{args['index']}")
    elif action.name == "scroll" and "ref" not in args:
        if args["to"] == "bottom":
            scenario.common("bottom", "action", "je fais défiler la page jusqu'en bas")
        else:
            scenario.common("top", "action", "je remonte en haut de la page")
    elif action.name == "press" and "ref" not in args:
        sentence = f'j\'appuie sur la touche "{_safe(key_label(args["key"]))}"'
        scenario.define(sentence, [f"Press Keys    None    {robot_arg(selenium_keys(args['key']))}"])
        scenario.add("action", sentence)
    elif action.name == "wait":
        if "text" not in args:
            scenario.common("sleep", "action", f"j'attends {args['seconds']} secondes")
        elif result.frame:  # texte attendu dans un cadre
            frame = frames.get(result.frame)
            scenario.define(f'j\'attends jusqu\'à ${{secondes:\\d+}} secondes que le cadre "{frame}" affiche "${{texte}}"',
                            _in_frame(result.frame, ["Wait Until Keyword Succeeds    ${secondes}s    500ms    "
                                                     "Le texte visible contient    ${texte}"]), _SHOWS)
            scenario.add("action", f"j'attends jusqu'à {args['seconds']} secondes que le cadre \"{frame}\" affiche "
                                   f"{scenario.quote(result.value or args['text'])}")
        else:
            scenario.common("wait", "action", f"j'attends jusqu'à {args['seconds']} secondes que la page affiche "
                                              f"{scenario.quote(result.value or args['text'])}")
    elif action.name == "check_text":
        checked = getattr(result, "frames_checked", None) or []
        if args.get("absent") and checked:  # ni dans la page, ni dans ses cadres : comme l'agent
            names = [f'"{frames.get(frame)}"' for frame in checked]
            where = f"le cadre {names[0]}" if len(names) == 1 else f"les cadres {', '.join(names[:-1])} et {names[-1]}"
            lines = ["La page n'affiche pas    ${texte}"]
            for frame in checked:
                lines += _in_frame(frame, ["La page n'affiche pas    ${texte}"])
            scenario.define(f'la page et {where} n\'affichent pas "${{texte}}"', lines, _HIDES)
            scenario.add("résultat", f"la page et {where} n'affichent pas {scenario.quote(args['text'])}")
        elif args.get("absent"):
            scenario.common("hides", "résultat", f"la page n'affiche pas {scenario.quote(args['text'])}")
        elif result.frame:  # texte trouvé dans un cadre
            frame = frames.get(result.frame)
            scenario.define(f'le cadre "{frame}" affiche "${{texte}}"', _in_frame(result.frame, ["La page affiche    ${texte}"]),
                            _SHOWS)
            scenario.add("résultat", f"le cadre \"{frame}\" affiche {scenario.quote(result.value or args['text'])}")
        else:
            scenario.common("shows", "résultat", f"la page affiche {scenario.quote(result.value or args['text'])}")
    elif action.name == "check_url":
        kind, verb = ("no_url", "ne contient pas") if args.get("absent") else ("url", "contient")
        scenario.common(kind, "résultat", f"l'URL {verb} {scenario.quote(args['contains'])}")
    elif action.name == "check_order":
        texts = [scenario.quote(text) for text in args["texts"]]
        scenario.common("order", "résultat", f"la page affiche dans cet ordre : {', '.join(texts[:-1])} et {texts[-1]}")
    elif action.name == "check_count":
        scenario.common("count", "résultat", f"la page affiche {args['count']} fois {scenario.quote(args['text'])}")
    elif action.name == "check_visual":
        scenario.common("visual", "résultat", f"l'écran montre {scenario.quote(args['expected'])}")
    else:
        _element_step(scenario, names, action, result)


def _element_step(scenario: _Scenario, names: _Names, action, result) -> None:
    """Étape sur un élément : la phrase cite l'élément par son nom, le mot-clé connaît son localisateur (et son cadre)."""
    element, args = result.element, action.args
    if element is None:
        return
    name, feminine = names.get(element)
    locator = robot_arg(robot_locator(element))

    def define(keyword_name: str, lines: list[str], block: str | None = None) -> None:
        if element.shadow and action.name in ("click", "fill", "select", "hover", "press", "scroll", "upload", "drag"):
            # Selenium n'attend pas un élément désigné en JavaScript (dom:) : on attend qu'il apparaisse
            lines = [f"Wait Until Page Contains Element    {locator}"] + lines
        scenario.define(keyword_name, _in_frame(element.frame, lines), block)

    if action.name == "click":
        if args.get("double"):
            sentence, keyword = f"je double-clique sur {name}", "Double Click Element"
        elif args.get("button") == "right":
            sentence, keyword = f"je fais un clic droit sur {name}", "Open Context Menu"
        else:
            sentence, keyword = f"je clique sur {name}", "Cliquer"
        define(sentence, [f"{keyword}    {locator}"], _CLICK if keyword == "Cliquer" else None)
        scenario.add("action", sentence)
    elif action.name == "press":
        sentence = f'j\'appuie sur la touche "{_safe(key_label(args["key"]))}" dans {name}'
        define(sentence, [f"Press Keys    {locator}    {robot_arg(selenium_keys(args['key']))}"])
        scenario.add("action", sentence)
    elif action.name == "hover":
        sentence = f"je survole {name}"
        define(sentence, [f"Mouse Over    {locator}"])
        scenario.add("action", sentence)
    elif action.name == "scroll":
        sentence = f"je fais défiler la page {jusqua(name)}"
        define(sentence, [f"Scroll Element Into View    {locator}"])
        scenario.add("action", sentence)
    elif action.name == "upload":  # le fichier est copié à côté du test
        define(f'je joins le fichier "${{fichier}}" {au(name)}', [f"Choose File    {locator}    ${{CURDIR}}${{/}}${{fichier}}"])
        scenario.add("action", f"je joins le fichier {scenario.quote(args['file'])} {au(name)}")
    elif action.name == "drag" and result.target is not None:
        target_name, _ = names.get(result.target)
        target = robot_arg(robot_locator(result.target))
        sentence = f"je glisse {name} sur {target_name}"
        if element.draggable():  # glisser-déposer HTML5 : par les événements du navigateur
            define(sentence, [f"Glisser-déposer    {locator}    {target}"], _DRAG)
        else:
            define(sentence, [f"Drag And Drop    {locator}    {target}"])
        scenario.add("action", sentence)
    elif action.name == "fill":
        click = [f"Click Element    {locator}"] if element.editable() else []  # Chrome : un vrai clic avant d'écrire
        parts = split(args["text"], scenario.names)
        if len(parts) == 1 and parts[0][0]:  # un mot de passe : la phrase cite sa variable, jamais sa valeur
            scenario.secrets = True
            # %{${variable}} : la variable d'environnement, lue sans passer par une variable Robot (jamais journalisée)
            define(f'je saisis le mot de passe "${{variable}}" dans {name}',
                   click + [f"Input Password    {locator}    %{{${{variable}}}}"])
            scenario.add("action", f'je saisis le mot de passe "{parts[0][1]}" dans {name}')
        elif any(is_secret for is_secret, _ in parts):  # un mot de passe dans un texte : mot-clé sans argument
            scenario.secrets = True
            sentence = f"je saisis {scenario.quote(args['text'])} dans {name}"
            define(sentence, click + [f"Input Password    {locator}    "
                                      f"{secret_cell(args['text'], scenario.names, scenario.unique)}"])
            scenario.add("action", sentence)
        else:
            define(f'je saisis "${{texte}}" dans {name}', click + [f"{typing_keyword(element)}    {locator}    ${{texte}}"])
            scenario.add("action", f"je saisis {scenario.quote(args['text'])} dans {name}")
    elif action.name == "select":
        # Libellé (« Prix croissant ») ou valeur (« price ») : les deux sont acceptés, comme pour l'agent
        define(f'je choisis "${{option}}" dans {name}', [
            f"${{par_libelle}}=    Run Keyword And Return Status    Select From List By Label    {locator}    ${{option}}",
            f"IF    not ${{par_libelle}}    Select From List By Value    {locator}    ${{option}}",
        ])
        scenario.add("action", f"je choisis {scenario.quote(args['value'])} dans {name}")
    elif action.name == "check_value":
        regex = bool(args.get("regex"))
        keyword, block = value_keyword(element, regex)
        wait = "Wait Until Keyword Succeeds    ${DELAI}    200ms    "
        if regex:  # un format : un code généré, une date...
            define(f'{name} correspond au motif "${{motif}}"', [f"{wait}{keyword}    {locator}    ${{motif}}"], block)
            scenario.add("résultat", f"{name} correspond au motif {_quote(args['value'])}")
        elif args["value"] == "":
            sentence = f"{name} est vide"
            define(sentence, [f"{wait}{keyword}    {locator}    ${{EMPTY}}"], block)
            scenario.add("résultat", sentence)
        else:
            verb = "indique" if element.tag == "select" else "contient"
            define(f'{name} {verb} "${{valeur}}"', [f"{wait}{keyword}    {locator}    ${{valeur}}"], block)
            scenario.add("résultat", f"{name} {verb} {scenario.quote(args['value'])}")
    elif action.name == "check_state":
        state = args["state"]
        sentence = f"{name} est {_FEMININE[state] if feminine else STATE_LABELS[state]}"
        keyword, *extra = _state_keyword(element, state)
        define(sentence, ["Wait Until Keyword Succeeds    ${DELAI}    200ms    " + SEP.join([keyword, locator, *extra])])
        scenario.add("résultat", sentence)


def generate_feature(run: RunResult) -> str:
    """Le scénario au format Gherkin, en français."""
    scenario = _build(run)
    match = _CODE.match(run.case.title)
    lines = [
        "# language: fr",
        "# Scénario généré automatiquement par agent-qa-playwright, à partir des actions réussies de l'agent.",
        f"# Version exécutable avec Robot Framework : robot {bdd_filename(run)}",
    ]
    if scenario.uses_unique:
        lines.append("# {unique} : une valeur nouvelle à chaque lancement (date et heure), pour créer des données "
                     "qui n'existent pas encore.")
    lines += [f"Fonctionnalité: {_clean(run.case.title)}", ""]
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
        variables=robot_variables(run) + (CLOSED_VARIABLE if run.closed_shadow else "") + (
            ADS_VARIABLE if getattr(run, "ads_blocked", 0) else ""),
        setup=(SECRETS_SETUP if scenario.secrets else "") + (UNIQUE_SETUP if scenario.uses_unique else ""),
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
