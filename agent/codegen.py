"""Génération d'un test pytest-playwright classique à partir d'une exécution.

L'agent IA sert à automatiser un cas de test la première fois. Ensuite, le
script généré rejoue les mêmes actions sans IA : rapide, gratuit et
déterministe, il peut entrer dans une suite de non-régression.

Le test désigne les éléments comme le recommande Playwright, par ce que voit
l'utilisateur : page.get_by_role('button', name='Connexion', exact=True),
get_by_label, get_by_placeholder... L'agent a vérifié dans la page que ce
localisateur trouve exactement l'élément ; sinon, le test garde un sélecteur
CSS stable (data-test, id, name).

Il reprend aussi les conditions de l'exécution : l'appareil émulé (fixture
browser_context_args), la session reprise ou enregistrée (dossier sessions/)
et la valeur {unique}, recréée à chaque lancement.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .browser import AD_URLS, OPEN_SHADOW_JS
from .coffre import env_names, split
from .donnees import FORMAT, masquer, morceaux

if TYPE_CHECKING:
    from .browser import Element
    from .runner import RunResult

_TEMPLATE = '''"""Test généré automatiquement par agent-qa-playwright.

Cas de test : {title}
Rejoue les actions réussies de l'agent, sans IA. Lancement : {command}
"""

{imports}
from playwright.sync_api import Page, expect
{helpers}
{fixtures}
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


def _nth(nth: int | None) -> str:
    return "" if nth is None else f".nth({nth})"


def scope_code(frame: list | None) -> str:
    """Code Playwright qui entre dans les cadres (iframes) de l'élément : page.frame_locator('#editeur')."""
    return "page" + "".join(f".frame_locator({hop['css']!r}){_nth(hop['nth'])}" for hop in frame or [])


def locator_code(element: Element) -> str:
    """Code Playwright qui retrouve l'élément.

    D'abord le localisateur recommandé par Playwright, vérifié par l'agent dans la page :
    page.get_by_role('button', name='Connexion', exact=True). Il entre seul dans les composants web.
    Sinon, un sélecteur lisible ; dans un composant web, on passe par son hôte :
    page.locator('generateur-code').locator('#generer').
    """
    code = scope_code(element.frame)
    if element.playwright:
        return f"{code}.{element.playwright}"
    code += "".join(f".locator({hop['css']!r}){_nth(hop['nth'])}" for hop in element.shadow or [])
    return code + f".locator({element.selector!r}){_nth(element.nth)}"


def _text_code(text_code: str, frame: list | None, in_shadow: bool, timeout: str = "") -> str:
    """Le texte est affiché : dans la page, dans un cadre, ou dans un composant web (Shadow DOM)."""
    if in_shadow:  # innerText ne contient pas le texte des composants : on cherche l'élément qui l'affiche
        return f"expect(page.get_by_text(re.compile(re.escape({text_code}), re.IGNORECASE)).first).to_be_visible({timeout.lstrip(', ')})"
    return f"expect({scope_code(frame)}.locator('body')).to_contain_text({text_code}, ignore_case=True, use_inner_text=True{timeout})"


def _dialog_code(dialog: dict) -> list[str]:
    """La réponse de l'agent à une fenêtre JavaScript, préparée avant l'action qui l'ouvre."""
    if not dialog["accepted"]:
        answer, said = "dialog.dismiss()", "Annuler"
    elif dialog.get("text") is not None:
        answer, said = f"dialog.accept({dialog['text']!r})", f"OK, avec la saisie « {dialog['text']} »"
    else:
        answer, said = "dialog.accept()", "OK"
    message = " ".join(str(dialog["message"]).split())
    return [f"# Fenêtre « {message} » : réponse {said}", f"page.once('dialog', lambda dialog: {answer})"]


# Ajouté au script s'il tape un mot de passe : il le lit dans le fichier .env, jamais dans le test
_SECRET_HELPERS = '''
load_dotenv(find_dotenv(usecwd=True))  # les mots de passe de test sont dans le fichier .env du projet


def secret(nom: str) -> str:
    """Un mot de passe de test, lu dans le fichier .env (l'agent l'y a rangé)."""
    valeur = os.environ.get(nom)
    if not valeur:
        raise RuntimeError(f"Ajoute {nom}=... dans le fichier .env du projet : ce test en a besoin.")
    return valeur
'''

# Sessions : dans le dossier sessions/, depuis le dossier où pytest est lancé (la racine du projet)
_SESSIONS_HELPER = '''
# Sessions enregistrées (cookies et stockage local), à la racine du projet : lance pytest depuis la racine
SESSIONS = Path("sessions")
'''


@dataclass
class _Generation:
    """Ce que le test généré doit savoir de l'exécution : mots de passe, valeur unique, outils à ajouter."""

    names: dict = field(default_factory=dict)  # repère du mot de passe -> variable du fichier .env
    unique: str | None = None  # valeur de {unique} pendant l'exécution de l'agent
    uses_unique: bool = False  # le test crée sa propre valeur unique

    def value(self, text: str) -> str:
        """Le texte à taper ou à vérifier, en Python.

        Un mot de passe devient secret('MOT_DE_PASSE_...'), lu dans le fichier .env ; la valeur unique de
        l'agent devient {unique} dans une f-string : le test crée la sienne à chaque lancement.
        """
        pieces = split(text, self.names)
        if not self.unique or not any(not is_secret and self.unique in value for is_secret, value in pieces):
            return " + ".join(f"secret({value!r})" if is_secret else repr(value) for is_secret, value in pieces)
        self.uses_unique = True
        out = []
        for is_secret, value in pieces:
            if is_secret:
                out.append("{secret(" + repr(value) + ")}")
                continue
            for is_unique, chunk in morceaux(value, self.unique):
                out.append("{unique}" if is_unique else _fstring(chunk))
        return 'f"' + "".join(out) + '"'

    def plain(self, text: str) -> str:
        """Un texte de commentaire ou de message : {unique} à la place de la valeur de l'agent."""
        return masquer(text, self.unique)


def _fstring(text: str) -> str:
    """Un morceau de texte dans une f-string entre guillemets : échappements de JSON, accolades doublées."""
    return json.dumps(text, ensure_ascii=False)[1:-1].replace("{", "{{").replace("}", "}}")


def _new_tab_code(line: str, result) -> list[str]:
    """Une action qui ouvre un nouvel onglet : le test le récupère et continue dedans, comme l'agent."""
    if not result.opened_tab:
        return [line]
    return ["with page.expect_popup() as nouvel_onglet:", f"    {line}", "page = nouvel_onglet.value",
            "page.wait_for_load_state()"]


def script_filename(run: RunResult) -> str:
    return f"test_{run.case.slug}.py"


def _context_fixture(run: RunResult) -> str:
    """La fixture browser_context_args de pytest-playwright : appareil émulé, session reprise."""
    appareil, session = getattr(run, "appareil", None), run.case.session
    if not appareil and not session:
        return ""
    lines = ["", "@pytest.fixture"]
    lines.append("def browser_context_args(browser_context_args, playwright):" if appareil
                 else "def browser_context_args(browser_context_args):")
    about = []
    if appareil:
        about.append(f"le même appareil que l'agent ({appareil} émulé)")
    if session:
        about.append(f"la session « {session} », enregistrée par le cas qui contient « Enregistrer la session : {session} »")
    lines.append(f'    """Le contexte du navigateur : {" et ".join(about)}."""')
    if session:
        missing = f"Session « {session} » absente du dossier sessions : lance d'abord le test qui l'enregistre."
        lines += [f"    session = SESSIONS / {session + '.json'!r}", "    if not session.is_file():",
                  f"        pytest.skip({missing!r})"]
    parts = ["**browser_context_args"]
    if appareil:
        parts.append(f"**playwright.devices[{appareil!r}]")
    if session:
        parts.append('"storage_state": str(session)')
    lines.append(f"    return {{{', '.join(parts)}}}")
    return "\n".join(lines) + "\n\n"


def generate_script(run: RunResult) -> str:
    """Construit le code du test à partir des étapes réussies."""
    lines: list[str] = []
    banners: list[str] = []  # bandeaux de cookies déjà pris en charge
    files = False  # fichiers joints : ils sont copiés à côté du test
    gen = _Generation(env_names(run), getattr(run, "unique", None))
    if run.closed_shadow:  # avant toute page, comme pendant l'exploration
        lines += ["# Composants web fermés (Shadow DOM « closed ») : créés ouverts, sinon Playwright ne peut pas y entrer",
                  "page.context.add_init_script(OUVRIR_COMPOSANTS)"]
    if getattr(run, "ads_blocked", 0):  # comme pendant l'exploration : pas de publicité plein écran sur la page
        lines += ["# Régies publicitaires bloquées, comme pendant l'exécution de l'agent : une publicité plein écran couvrirait la page",
                  "page.context.route(PUBLICITES, lambda route: route.abort())"]
    for step in run.steps:
        action, result = step.action, step.result
        if action is None or not result.ok:
            continue  # on ne rejoue que ce qui a fonctionné
        args = action.args
        if result.dialog is not None:  # Playwright fermerait la fenêtre avec Annuler : on donne la réponse de l'agent
            lines += _dialog_code(result.dialog)
        if action.name == "goto":
            lines.append(f"page.goto({gen.value(args['url'])})")
        elif action.name == "cookies":
            # Playwright accepte le bandeau dès qu'il couvre la page, avant l'action ou la vérification qu'il gênerait
            if args["playwright"] not in banners:
                banners.append(args["playwright"])
                name = "bandeau_cookies" if len(banners) == 1 else f"bandeau_cookies_{len(banners)}"
                where = f" ({args['outil']})" if args["outil"] else " du site"
                lines += [f"# Bandeau de cookies{where} : accepté s'il s'affiche, comme pendant l'exécution de l'agent",
                          f"{name} = {args['playwright']}.first",
                          f"page.add_locator_handler({name}, lambda: {name}.click())"]
        elif action.name == "click":
            if args.get("double"):
                click = "dblclick()"
            elif args.get("button") == "right":
                click = "click(button='right')"
            else:
                click = "click()"
            lines += _new_tab_code(f"{locator_code(result.element)}.{click}", result)
        elif action.name == "press":
            where = locator_code(result.element) if "ref" in args else "page.keyboard"
            lines += _new_tab_code(f"{where}.press({args['key']!r})", result)
        elif action.name == "hover":
            lines.append(f"{locator_code(result.element)}.hover()")
        elif action.name == "scroll":
            if "ref" in args:
                lines.append(f"{locator_code(result.element)}.scroll_into_view_if_needed()")
            elif args["to"] == "bottom":
                lines.append("page.evaluate('window.scrollTo(0, document.body.scrollHeight)')")
            else:
                lines.append("page.evaluate('window.scrollTo(0, 0)')")
        elif action.name == "upload":
            files = True
            lines.append(f"{locator_code(result.element)}.set_input_files(DOSSIER / {args['file']!r})")
        elif action.name == "drag":
            lines.append(f"{locator_code(result.element)}.drag_to({locator_code(result.target)})")
        elif action.name == "switch_tab":
            lines += [f"page = page.context.pages[{args['index'] - 1}]  # onglet n°{args['index']}", "page.bring_to_front()"]
        elif action.name == "fill":
            lines.append(f"{locator_code(result.element)}.fill({gen.value(args['text'])})")
        elif action.name == "select":
            lines.append(f"{locator_code(result.element)}.select_option({gen.value(args['value'])})")
        elif action.name == "wait":
            if "text" not in args:
                lines.append(f"page.wait_for_timeout({args['seconds'] * 1000})")
            else:
                lines.append(_text_code(gen.value(args["text"]), result.frame, result.in_shadow,
                                        f", timeout={args['seconds'] * 1000}"))
        elif action.name == "check_text":
            if args.get("absent"):
                lines.append(f"expect(page.locator('body')).not_to_contain_text({gen.value(args['text'])}, ignore_case=True, use_inner_text=True)")
                for frame in getattr(result, "frames_checked", None) or []:  # ni dans la page, ni dans ses cadres
                    lines.append(f"expect({scope_code(frame)}.locator('body')).not_to_contain_text({gen.value(args['text'])}, "
                                 "ignore_case=True, use_inner_text=True)")
            else:
                lines.append(_text_code(gen.value(args["text"]), result.frame, result.in_shadow))
        elif action.name == "check_url":
            assertion = "not_to_have_url" if args.get("absent") else "to_have_url"
            lines.append(f"expect(page).{assertion}(re.compile(re.escape({gen.value(args['contains'])})))")
        elif action.name == "check_order":
            # Chaque texte après le précédent ; la casse et les espaces ne comptent pas
            pattern = ".*".join(r"\s+".join(re.escape(word) for word in text.split()) for text in args["texts"])
            lines.append(f"expect(page.locator('body')).to_contain_text(re.compile({pattern!r}, re.IGNORECASE | re.DOTALL), use_inner_text=True)")
        elif action.name == "check_count":
            message = f"« {gen.plain(args['text'])} » doit apparaître {args['count']} fois"
            lines.append(f"assert attendre(lambda: compte(page, {gen.value(args['text'])}) == {args['count']}), {message!r}")
        elif action.name == "check_value":
            element = result.element
            if args.get("regex"):  # un format, pas une valeur exacte
                expected = f"re.compile({args['value']!r})"
            elif split(args["value"], gen.names) != [(False, args["value"])]:  # un mot de passe : lu dans le .env
                expected = gen.value(args["value"])
            else:  # vérification réussie : on reprend la valeur réelle (pour une liste, la valeur de l'option)
                expected = gen.value(result.value if result.check and result.value is not None else args["value"])
            if element.reads_text():  # zone de texte riche, liste « maison » : son texte affiché
                lines.append(f"expect({locator_code(element)}).to_have_text({expected}, use_inner_text=True)")
            elif element.tag == "select" and args.get("regex"):  # le motif porte sur le libellé de l'option choisie
                lines.append(f"expect({locator_code(element)}.locator('option:checked')).to_have_text({expected})")
            else:
                lines.append(f"expect({locator_code(element)}).to_have_value({expected})")
        elif action.name == "check_state":
            lines.append(f"expect({locator_code(result.element)}).{_STATE_ASSERTIONS[args['state']]}")
        elif action.name == "check_visual":  # avis du modèle sur une capture : rien à rejouer sans IA
            expected = " ".join(args["expected"].split())
            lines.append(f"# À automatiser : vérification visuelle faite par l'IA ({'conforme' if result.check else 'non conforme'}) : {expected}")

    saved = getattr(run, "session_saved", None) is not None and run.case.save_session
    if saved:  # la session de ce test, pour les tests qui commencent déjà connectés
        name = run.case.save_session
        lines += [f"# Session « {name} » : gardée pour les tests qui commencent déjà connectés (ligne « Session : {name} »)",
                  "SESSIONS.mkdir(exist_ok=True)", f"page.context.storage_state(path=SESSIONS / {name + '.json'!r})"]
    if gen.uses_unique:  # en tête du test : une valeur à chaque lancement, comme {unique} pour l'agent
        lines.insert(0, f"unique = datetime.now().strftime({FORMAT!r})  # {{unique}} du cas de test : une valeur nouvelle "
                        "à chaque lancement")

    counting = any(step.action and step.action.name == "check_count" and step.result.ok for step in run.steps)
    body = "\n".join(f"    {line}" for line in lines) or "    pass"
    fixtures = _context_fixture(run)
    imports = "import re\nimport time\n" if counting else "import re\n"
    if gen.uses_unique:
        imports += "from datetime import datetime\n"
    helpers = _COUNT_HELPERS if counting else ""
    if "secret(" in body:
        imports = "import os\n" + imports + "\nfrom dotenv import find_dotenv, load_dotenv\n"
        helpers += _SECRET_HELPERS
    if files or saved or run.case.session:
        imports += "from pathlib import Path\n"
    if fixtures:
        imports += "\nimport pytest\n"
    if files:
        helpers += "\n# Fichiers joints pendant le test, copiés à côté de ce fichier\nDOSSIER = Path(__file__).resolve().parent\n"
    if saved or run.case.session:
        helpers += _SESSIONS_HELPER
    if run.closed_shadow:
        helpers += f"\n# Crée ouverts les composants web fermés, avant le chargement de la page\nOUVRIR_COMPOSANTS = {OPEN_SHADOW_JS!r}\n"
    if getattr(run, "ads_blocked", 0):
        helpers += f"\n# Adresses des régies publicitaires\nPUBLICITES = re.compile({AD_URLS.pattern!r})\n"
    navigateur = getattr(run, "navigateur", "chromium") or "chromium"
    command = f"pytest {script_filename(run)}" + (f" --browser {navigateur}" if navigateur != "chromium" else "")
    return _TEMPLATE.format(
        title=run.case.title,
        command=command,
        imports=imports,
        helpers=helpers,
        fixtures=fixtures,
        function=f"test_{run.case.slug}",
        body=body,
    )


def write_script(run: RunResult) -> None:
    """Écrit le script dans le dossier du rapport."""
    path = run.out_dir / script_filename(run)
    path.write_text(generate_script(run), encoding="utf-8")
    run.script_path = path
