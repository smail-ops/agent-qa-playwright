"""User stories et scénarios Gherkin : lecture, contrôle, écriture.

Une user story dit qui veut quoi, et pourquoi :

    En tant que client, je veux trier les produits par prix, afin de trouver le moins cher.

Ses critères d'acceptation sont des scénarios Gherkin (Étant donné que, Quand,
Alors). Ce module :

- contrôle les user stories proposées par le modèle (format, mots-clés Gherkin) ;
- les écrit en fichiers .feature, le format des outils BDD (Cucumber, Squash TM) ;
- relit un fichier .feature et transforme chaque scénario en cas de test, que
  l'agent exécute comme un cas écrit en Markdown.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .actions import _extract_json
from .testcase import TestCase, slugify

PRIORITIES = ("haute", "moyenne", "basse")
_PRIORITY_ALIASES = {"high": "haute", "medium": "moyenne", "moyen": "moyenne", "low": "basse", "élevée": "haute",
                     "forte": "haute", "faible": "basse", "critique": "haute"}
# Mots-clés d'étape du Gherkin en français (dialecte « fr » de Cucumber), du plus long au plus court
STEP_KEYWORDS = ("Étant donné que", "Étant donné qu'", "Étant donnée", "Étant donnés", "Étant données", "Étant donné",
                 "Etant donné que", "Etant donné qu'", "Etant donné", "Sachant que", "Sachant qu'", "Sachant", "Soit",
                 "Lorsque", "Lorsqu'", "Quand", "Alors", "Donc", "Et que", "Et qu'", "Et", "Mais que", "Mais qu'", "Mais")
# « Lorsqu'il » : après une apostrophe, le mot suit sans espace (apostrophe droite ou typographique)
_ELIDED = [re.escape(k[:-1]) + "['’]" for k in STEP_KEYWORDS if k.endswith("'")]
_WORDS = [re.escape(k) for k in STEP_KEYWORDS if not k.endswith("'")]
_STEP = re.compile(r"^(?:(?:" + "|".join(_ELIDED) + r")|(?:" + "|".join(_WORDS) + r")(?=\s|$))", re.IGNORECASE)
_CONJUNCTION = re.compile(r"^(?:Et|Mais)\b", re.IGNORECASE)
MAX_TEXT = 300


@dataclass
class Criterion:
    """Un critère d'acceptation : un scénario Gherkin."""

    scenario: str
    steps: list[str]


@dataclass
class UserStory:
    title: str
    role: str
    want: str
    benefit: str
    priority: str = "moyenne"
    criteria: list[Criterion] = field(default_factory=list)

    def sentence(self) -> str:
        return f"En tant que {self.role}, je veux {self.want}, afin {_de(self.benefit)}."

    def to_dict(self) -> dict:
        return {"titre": self.title, "en_tant_que": self.role, "je_veux": self.want, "afin_de": self.benefit,
                "priorite": self.priority, "phrase": self.sentence(),
                "criteres": [{"scenario": c.scenario, "etapes": c.steps} for c in self.criteria]}

    def to_feature(self, code: str, url: str) -> str:
        """Le fichier .feature : la user story en tête, ses critères d'acceptation en scénarios."""
        lines = [
            "# language: fr",
            f"# URL : {url}",
            "# User story proposée par agent-qa-playwright à partir de l'analyse du site : à relire avant usage.",
            f"@{code} @priorite-{self.priority}",
            f"Fonctionnalité: {code} {self.title}",
            f"  En tant que {self.role}",
            f"  Je veux {self.want}",
            f"  Afin {_de(self.benefit)}",
        ]
        for criterion in self.criteria:
            lines += ["", f"  Scénario: {criterion.scenario}"]
            lines += [f"    {step}" for step in criterion.steps]
        return "\n".join(lines) + "\n"


def _de(text: str) -> str:
    """« de trouver », mais « d'accéder » : l'élision devant une voyelle ou un h."""
    return f"d'{text}" if text[:1].lower() in "aâàeéèêëiîïoôuûùyh" else f"de {text}"


def _text(value: object, name: str, prefixes: tuple[str, ...] = ()) -> str:
    """Texte d'une ligne, sans le début que le modèle répète parfois (« je veux je veux... »)."""
    text = " ".join(str(value or "").split())
    for prefix in prefixes:
        if text.lower().startswith(prefix):
            text = text[len(prefix):].lstrip(" ,:")
    if not text:
        raise ValueError(f"« {name} » vide")
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def normalize_step(step: object) -> str | None:
    """Une étape Gherkin d'une ligne, ou None si elle ne commence pas par un mot-clé."""
    text = " ".join(str(step or "").split())
    if not text or not _STEP.match(text):
        return None
    return text[0].upper() + text[1:]


def _criterion(data: object) -> Criterion | None:
    """Un critère valable : un nom, puis au moins deux étapes Gherkin, la première n'étant pas « Et » ou « Mais »."""
    if not isinstance(data, dict):
        return None
    name = " ".join(str(data.get("scenario") or data.get("titre") or "").split())
    raw_steps = data.get("etapes") or data.get("étapes") or []
    if not name or not isinstance(raw_steps, list):
        return None
    steps = [normalize_step(step) for step in raw_steps]
    if len(steps) < 2 or any(step is None for step in steps) or _CONJUNCTION.match(steps[0]):
        return None
    return Criterion(name[:MAX_TEXT], steps[:15])


def story_from_dict(data: object) -> UserStory:
    """Une user story contrôlée. Lève ValueError si elle est inutilisable."""
    if not isinstance(data, dict):
        raise ValueError("une user story doit être un objet JSON")
    priority = str(data.get("priorite") or data.get("priorité") or "moyenne").strip().lower()
    priority = _PRIORITY_ALIASES.get(priority, priority)
    criteria = [c for c in (_criterion(item) for item in data.get("criteres") or data.get("critères") or []) if c]
    if not criteria:
        raise ValueError("aucun critère d'acceptation valable")
    return UserStory(
        title=_text(data.get("titre"), "titre"),
        role=_text(data.get("en_tant_que"), "en_tant_que", ("en tant que", "en tant qu'")),
        want=_text(data.get("je_veux"), "je_veux", ("je veux",)),
        benefit=_text(data.get("afin_de"), "afin_de", ("afin de", "afin d'")),
        priority=priority if priority in PRIORITIES else "moyenne",
        criteria=criteria[:6],
    )


def parse_stories(raw: str) -> list[UserStory]:
    """Les user stories de la réponse du modèle. Les stories mal formées sont écartées, pas la réponse entière."""
    try:
        data = json.loads(_extract_json(raw))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"réponse illisible ({exc})") from exc
    items = data.get("stories") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("liste « stories » absente")
    stories = []
    for item in items[:10]:
        try:
            stories.append(story_from_dict(item))
        except ValueError:
            continue
    if not stories:
        raise ValueError("aucune user story utilisable")
    return stories


# --- Lecture d'un fichier .feature ------------------------------------------------------

_FEATURE = re.compile(r"^\s*(?:Fonctionnalité|Feature)\s*:\s*(.+)$", re.IGNORECASE)
_SCENARIO = re.compile(r"^\s*(?:Scénario|Scenario|Exemple|Example)\s*:\s*(.+)$", re.IGNORECASE)
_OUTLINE = re.compile(r"^\s*(?:Plan du scénario|Plan du Scénario|Scenario Outline)\s*:", re.IGNORECASE)
_BACKGROUND = re.compile(r"^\s*(?:Contexte|Background)\s*:", re.IGNORECASE)
_URL_COMMENT = re.compile(r"^\s*#\s*URL\s*:\s*(\S+)", re.IGNORECASE)
_CODE_TAG = re.compile(r"@([A-Za-z]{2,}-\d+)\b")
_HTTP = re.compile(r"https?://[^\s\"'»]+")


def feature_cases(text: str) -> list[TestCase]:
    """Un cas de test par scénario du fichier .feature, avec la user story et le contexte communs.

    L'adresse vient d'un commentaire « # URL : ... », sinon de la première adresse
    citée dans les étapes. Les plans de scénario (avec Exemples) ne sont pas repris.
    """
    feature, code, url = "", "", None
    narrative: list[str] = []
    background: list[str] = []
    scenarios: list[tuple[str, list[str]]] = []
    section = "entete"
    for raw in text.splitlines():
        line = raw.strip()
        if url is None and (match := _URL_COMMENT.match(line)):
            url = match.group(1)
        if not line or line.startswith("#"):
            continue
        if line.startswith("@"):
            code = code or next(iter(_CODE_TAG.findall(line)), "")
            continue
        if match := _FEATURE.match(line):
            feature, section = match.group(1).strip(), "recit"
        elif _BACKGROUND.match(line):
            section = "contexte"
        elif _OUTLINE.match(line):
            section = "ignore"
        elif match := _SCENARIO.match(line):
            scenarios.append((match.group(1).strip(), []))
            section = "scenario"
        elif section == "recit":
            narrative.append(line)
        elif section == "contexte" and normalize_step(line):
            background.append(line)
        elif section == "scenario" and normalize_step(line):
            scenarios[-1][1].append(line)
    if url is None:
        url = next((m.group(0) for _, steps in scenarios for step in background + steps if (m := _HTTP.search(step))), None)
    if not feature or url is None:
        return []
    name = feature if not code or feature.upper().startswith(code.upper()) else f"{code.upper()} {feature}"
    cases = []
    for scenario, steps in scenarios:
        if not steps:
            continue
        title = f"{name} : {scenario}"
        body = [f"# {title}", f"URL : {url}", ""]
        if narrative:
            body += ["## User story", *narrative, ""]
        body += ["## Scénario Gherkin", *background, *steps]
        cases.append(TestCase(title=title, url=url, text="\n".join(body)))
    return cases


def feature_filename(code: str, story: UserStory) -> str:
    return f"{code}_{slugify(story.title)}.feature"
