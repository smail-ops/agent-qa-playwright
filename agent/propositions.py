"""Propositions de l'IA : des user stories à partir d'un site, des cas de test à partir d'une user story.

    adresse du site --(analyse, sans IA)--> relevé des pages --(1 requête)--> user stories et critères Gherkin
    user story --(1 requête)--> cas de test au format CT (comme dans Squash TM)

Le modèle propose, le programme contrôle : chaque proposition est relue
(format, mots-clés Gherkin, étapes, résultats attendus) avant d'être montrée,
et rien n'est enregistré sans l'accord du testeur.

En ligne de commande :

    python -m agent.propositions https://www.saucedemo.com/ --identifiant standard_user
    python -m agent.propositions https://www.saucedemo.com/ --user-story "En tant que client, je veux..."
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .actions import _extract_json
from .analyse import MAX_PAGES, AnalysisError, SiteAnalysis, analyse_site
from .providers import Provider, ProviderError, RateLimitError, make_provider
from .testcase import slugify
from .user_stories import UserStory, feature_filename, parse_stories

STORIES_PROMPT = """Tu es un testeur QA expérimenté (ISTQB). Tu reçois le relevé de quelques pages d'un site web, et parfois leurs captures d'écran.
Propose les user stories que ce site permet de tester, avec leurs critères d'acceptation écrits en Gherkin, en français.

Réponds uniquement avec un objet JSON :
{"stories": [{"titre": "...", "en_tant_que": "...", "je_veux": "...", "afin_de": "...", "priorite": "haute" | "moyenne" | "basse",
  "criteres": [{"scenario": "...", "etapes": ["Étant donné que ...", "Quand ...", "Et ...", "Alors ...", "Et ..."]}]}]}

Règles :
1. Ne parle que de ce que montre le relevé : pages, champs, listes, boutons, liens, textes. N'invente ni page, ni champ, ni texte de message.
2. Propose de 3 à 6 user stories, de la plus importante (priorité haute) à la moins importante.
3. Chaque user story a de 1 à 3 critères d'acceptation : le cas nominal d'abord, puis, si c'est utile, un cas d'erreur ou une limite.
4. Chaque critère est un scénario qu'un testeur peut exécuter tel quel : la première étape commence par « Étant donné que » et indique la page de départ, les actions commencent par « Quand » ou « Et », les résultats attendus par « Alors » ou « Et ». Écris les libellés du site entre guillemets, à l'identique : « Login », « Products ».
5. Données de test : celles affichées sur le site, ou l'identifiant du compte de test s'il est fourni. Le mot de passe du compte de test ne t'est pas donné : s'il n'est pas affiché sur le site, écris <mot de passe>.
6. Écris en français, sans tiret cadratin.
"""

CASES_PROMPT = """Tu es un testeur QA expérimenté (ISTQB). À partir d'une user story et de ses critères d'acceptation, rédige les cas de test qui la vérifient, au format des cas de test de Squash TM.

Réponds uniquement avec un objet JSON :
{"cas": [{"titre": "...", "preconditions": "...", "etapes": ["...", "..."], "resultats": ["...", "..."]}]}

Règles :
1. Rédige de 2 à 5 cas : le cas nominal d'abord, puis les cas d'erreur et les limites (champ vide, valeur incorrecte, bouton grisé...).
2. Une étape est une seule action, à l'infinitif : « Saisir « standard_user » dans le champ Username », « Cliquer sur le bouton Login ».
3. Un résultat attendu est observable : un texte affiché ou absent, une adresse, un ordre, un nombre, le contenu d'un champ, un bouton grisé, une case cochée.
4. Données de test : celles de la user story ou du site. N'invente pas d'identifiant. Si le mot de passe n'est pas connu, écris <mot de passe>.
5. Pas de numéro dans le titre : il est ajouté à l'enregistrement.
6. Écris en français, sans tiret cadratin.
"""

MAX_WAIT_S = 30  # au-delà, on ne bloque pas Alfred : le testeur réessaiera
MAX_LINE = 300
MAX_CASES = 6
DEMO_CODES = 90  # CT-90 et au-delà : réservés aux démos sans IA


def ask(provider: Provider, system: str, user: str, images: list[tuple[str, bytes]] = (), max_tokens: int = 8000,
        sleep: Callable[[float], None] = time.sleep) -> str:
    """Une requête au modèle. Quota par minute atteint : une seule nouvelle tentative, après une courte pause."""
    try:
        return provider.complete(system, user, list(images), max_tokens)
    except RateLimitError as exc:
        delay = exc.retry_after if exc.retry_after is not None else 20.0
        if exc.daily or exc.no_free_quota or delay > MAX_WAIT_S:
            raise
        sleep(delay)
        return provider.complete(system, user, list(images), max_tokens)


def propose_stories(analysis: SiteAnalysis, provider: Provider, vision: bool = False) -> list[UserStory]:
    """Les user stories que le modèle propose à partir du relevé du site (une requête)."""
    raw = ask(provider, STORIES_PROMPT, analysis.to_prompt(), analysis.images() if vision else [])
    return parse_stories(raw)


def story_text(story: UserStory) -> str:
    """Une user story et ses critères, telles qu'on les écrit dans un ticket."""
    lines = [story.sentence(), "", "Critères d'acceptation :"]
    for criterion in story.criteria:
        lines += [f"Scénario : {criterion.scenario}", *(f"  {step}" for step in criterion.steps)]
    return "\n".join(lines)


# --- Cas de test proposés ----------------------------------------------------------------


@dataclass
class CaseProposal:
    """Un cas de test proposé, avant qu'il reçoive son numéro."""

    title: str
    preconditions: str
    steps: list[str]
    expected: list[str]

    def markdown(self, code: str, url: str) -> str:
        """Le cas au format des fichiers cas_de_test/CT-xx_....md."""
        lines = [f"# {code} {self.title}", f"URL : {url}", ""]
        if self.preconditions:
            lines += ["## Préconditions", self.preconditions, ""]
        lines += ["## Étapes", *(f"{number}. {step}" for number, step in enumerate(self.steps, 1)), ""]
        lines += ["## Résultats attendus", *(f"- {result}" for result in self.expected)]
        return "\n".join(lines) + "\n"

    def to_dict(self) -> dict:
        return {"titre": self.title, "preconditions": self.preconditions, "etapes": self.steps, "resultats": self.expected}


def _line(value: object) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= MAX_LINE else text[: MAX_LINE - 1] + "…"


def _lines(value: object, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    # « 1. Saisir... » ou « - La page... » : la numérotation est refaite à l'écriture du fichier
    items = [re.sub(r"^\s*(?:\d+[.)]|[-*•])\s+", "", _line(item)) for item in value if not isinstance(item, (dict, list))]
    return [item for item in items if item][:limit]


def case_from_dict(data: object) -> CaseProposal:
    """Un cas proposé et contrôlé : un titre, au moins une étape et un résultat attendu."""
    if not isinstance(data, dict):
        raise ValueError("un cas de test doit être un objet JSON")
    title = _line(data.get("titre"))
    title = re.sub(r"^(?:[A-Z]{2,}-[\d?]+\s*[:-]?\s*|cas de test\s*:\s*)", "", title, flags=re.IGNORECASE).strip()
    steps = _lines(data.get("etapes") or data.get("étapes"), 20)
    expected = _lines(data.get("resultats") or data.get("résultats"), 12)
    if not title or not steps or not expected:
        raise ValueError("un cas de test a besoin d'un titre, d'étapes et de résultats attendus")
    return CaseProposal(title, _line(data.get("preconditions") or data.get("préconditions")), steps, expected)


def parse_cases(raw: str) -> list[CaseProposal]:
    """Les cas de la réponse du modèle. Les cas mal formés sont écartés, pas la réponse entière."""
    try:
        data = json.loads(_extract_json(raw))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"réponse illisible ({exc})") from exc
    items = data.get("cas") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("liste « cas » absente")
    cases = []
    for item in items[:MAX_CASES]:
        try:
            cases.append(case_from_dict(item))
        except ValueError:
            continue
    if not cases:
        raise ValueError("aucun cas de test utilisable")
    return cases


def propose_cases(story: str, url: str, provider: Provider, site: str = "") -> list[CaseProposal]:
    """Les cas de test que le modèle propose pour une user story (une requête)."""
    message = f"Adresse du site : {url}\n\nUser story :\n{story.strip()}"
    if site:
        message += f"\n\nRelevé du site (libellés exacts des champs et des boutons) :\n{site}"
    return parse_cases(ask(provider, CASES_PROMPT, message))


# --- Enregistrement dans cas_de_test -------------------------------------------------------

_CODE = re.compile(r"\b([A-Z]{2,})-(\d+)\b")


def used_numbers(cases_dir: Path, prefix: str) -> set[int]:
    """Numéros déjà pris pour ce préfixe (CT, US) : noms de fichiers, titres et étiquettes Gherkin."""
    numbers = set()
    if not Path(cases_dir).is_dir():
        return numbers
    for path in Path(cases_dir).rglob("*"):
        if path.suffix not in (".md", ".feature", ".json") or not path.is_file():
            continue
        found = _CODE.findall(path.name)
        if path.suffix != ".json":
            try:
                head = path.read_text(encoding="utf-8")[:2000]
            except (OSError, UnicodeDecodeError):
                head = ""
            found += _CODE.findall(head)
        numbers.update(int(number) for code, number in found if code == prefix)
    return numbers


def next_codes(cases_dir: Path, prefix: str, count: int, *others: Path) -> list[str]:
    """Les prochains numéros libres : CT-17, CT-18... (sans toucher aux numéros des démos).

    others : autres dossiers dont les numéros comptent aussi (cas_de_test, quand on écrit ailleurs).
    """
    used = used_numbers(cases_dir, prefix).union(*(used_numbers(folder, prefix) for folder in others))
    number = max((n for n in used if n < DEMO_CODES), default=0)
    codes = []
    while len(codes) < count:
        number += 1
        if number not in used:
            codes.append(f"{prefix}-{number:02d}")
    return codes


def _free_path(path: Path) -> Path:
    candidate, index = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        index += 1
    return candidate


def save_stories(stories: list[UserStory], url: str, cases_dir: Path, *numbering: Path) -> list[Path]:
    """Chaque user story devient un fichier .feature : la story en tête, ses critères en scénarios."""
    cases_dir = Path(cases_dir)
    cases_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for code, story in zip(next_codes(cases_dir, "US", len(stories), *numbering), stories):
        path = _free_path(cases_dir / feature_filename(code, story))
        path.write_text(story.to_feature(code, url), encoding="utf-8")
        paths.append(path)
    return paths


def save_cases(cases: list[CaseProposal], url: str, cases_dir: Path, *numbering: Path) -> list[Path]:
    """Chaque cas proposé devient un fichier CT-xx_....md, prêt à être lancé."""
    cases_dir = Path(cases_dir)
    cases_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for code, case in zip(next_codes(cases_dir, "CT", len(cases), *numbering), cases):
        path = _free_path(cases_dir / f"{code}_{slugify(case.title)}.md")
        path.write_text(case.markdown(code, url), encoding="utf-8")
        paths.append(path)
    return paths


# --- Ligne de commande ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="python -m agent.propositions",
        description="Propose des user stories (avec leurs critères en Gherkin) à partir d'un site, "
                    "ou des cas de test à partir d'une user story.",
    )
    parser.add_argument("url", help="adresse du site, par exemple https://www.saucedemo.com/")
    parser.add_argument("--user-story", help="texte d'une user story : propose alors des cas de test")
    parser.add_argument("--identifiant", help="identifiant d'un compte de test, pour analyser aussi les pages "
                                              "derrière la connexion (le mot de passe est demandé sans s'afficher)")
    parser.add_argument("--provider", choices=["gemini", "claude"], default=None,
                        help="modèle à utiliser (par défaut : AGENT_PROVIDER ou gemini)")
    parser.add_argument("--model", help="nom précis du modèle (sinon GEMINI_MODEL ou CLAUDE_MODEL)")
    parser.add_argument("--vision", action="store_true", help="envoie aussi les captures d'écran des pages au modèle")
    parser.add_argument("--pages", type=int, default=MAX_PAGES, help=f"pages analysées au plus ({MAX_PAGES} par défaut)")
    parser.add_argument("--dossier", default="propositions", help="dossier des propositions (propositions par défaut)")
    args = parser.parse_args(argv)
    folder = Path(args.dossier)
    try:
        provider = make_provider(args.provider or os.getenv("AGENT_PROVIDER", "gemini"), model=args.model)
        if args.user_story:
            cases = propose_cases(args.user_story, args.url, provider)
            paths = save_cases(cases, args.url, folder, Path("cas_de_test"))
            for path, case in zip(paths, cases):
                print(f"{path} : {case.title}")
        else:
            password = getpass.getpass("Mot de passe du compte de test (rien ne s'affiche) : ") if args.identifiant else None
            analysis = analyse_site(args.url, args.identifiant, password, max_pages=args.pages, vision=args.vision,
                                    on_page=lambda page: print(f"Page analysée : {page.url}"))
            if analysis.login:
                print(analysis.login)
            stories = propose_stories(analysis, provider, vision=args.vision)
            paths = save_stories(stories, args.url, folder, Path("cas_de_test"))
            for path, story in zip(paths, stories):
                print(f"{path} : {story.sentence()}")
    except (AnalysisError, ProviderError, ValueError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 3
    print("Relisez ces propositions, puis déplacez celles que vous gardez dans le dossier cas_de_test.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
