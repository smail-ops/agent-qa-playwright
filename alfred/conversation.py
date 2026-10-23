"""Conversation libre avec Alfred.

Les commandes simples (« lance la démo », « arrête ») sont comprises dans la
page, sans IA. Toute autre phrase arrive ici : elle part au modèle avec le
contexte utile (cas de test, mission en cours, derniers résultats), et la
réponse revient en JSON :

    « refais le test de connexion sur le site de recette »
    → {"reponse": "Très bien, je relance CT-01 sur le site de recette.",
       "action": {"type": "lancer", "cas": "CT-01", "url": "https://recette.exemple.fr/"}}

Comme pour l'agent, le modèle ne fait que proposer : l'action est contrôlée
(liste fermée, cas existant, adresse valide) avant de partir vers la page, et
la phrase lue à voix haute ne contient jamais un mot de passe.

Une question technique sur un outil (« comment gérer une iframe avec Robot
Framework ? ») part avec des extraits de la documentation officielle, et la
réponse revient avec les liens des pages citées.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from agent import __version__, documentation
from agent.actions import _extract_json
from agent.propositions import ask, case_from_dict, next_codes
from agent.providers import Provider

from .maitre import CREATEUR

ACTIONS = ("aucune", "lancer", "selectionner", "arreter", "ouvrir", "rediger", "proposer")
FILES = ("rapport", "anomalie", "pytest", "robot", "gherkin", "robot_bdd")
MAX_REPLY = 450  # caractères lus à voix haute au plus
MAX_TEXT = 500  # longueur d'une phrase du testeur
MAX_HISTORY = 6  # échanges précédents envoyés au modèle
URL = re.compile(r"https?://[^\s/?#]+\S*", re.IGNORECASE)
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre",
        "décembre")

PROMPT = """Tu es Alfred, l'assistant vocal d'un testeur QA. Tu pilotes un agent qui exécute des cas de test sur un navigateur.
Le testeur te parle à voix haute ; ta réponse sera lue par une synthèse vocale.

{identite}

Réponds uniquement avec un objet JSON : {"reponse": "...", "action": {"type": "..."}}

Actions possibles (une seule par réponse) :
- {"type": "aucune"} : répondre seulement (question, explication, conversation).
- {"type": "lancer", "cas": "<code ou identifiant de la liste>", "url": "https://..." (facultatif), "vision": true (facultatif)} : lancer un cas de test de la liste. Mets "url" seulement si le testeur demande un autre site, et "vision" s'il demande de regarder l'écran.
- {"type": "selectionner", "cas": "<code ou identifiant>"} : afficher un cas sans le lancer.
- {"type": "arreter"} : arrêter la mission en cours.
- {"type": "ouvrir", "fichier": "rapport" | "anomalie" | "pytest" | "robot" | "gherkin", "cas": "CT-14" (facultatif)} : ouvrir un fichier d'un résultat récent, par défaut le plus récent qui en a un.
- {"type": "rediger", "titre": "...", "url": "https://...", "preconditions": "...", "etapes": ["..."], "resultats": ["..."]} : rédiger le cas de test que le testeur te dicte. Étapes à l'infinitif, une action chacune ; résultats attendus observables. Sans adresse donnée, utilise le site par défaut. Le cas sera affiché, puis lancé quand le testeur dira « vas-y ».
- {"type": "proposer", "url": "https://..."} : analyser un site pour proposer des user stories et leurs critères en Gherkin.

Règles :
1. « reponse » : une à trois phrases courtes, en français, avec le ton courtois d'un majordome (vouvoiement), sans tiret cadratin ni liste. Elle sera dite à voix haute : pas de symbole, pas de code.
2. Pour parler de toi, de l'agent, de son fonctionnement ou de ses limites, appuie-toi sur « Qui tu es » ; pour la situation du moment (cas de test, mission en cours, derniers résultats), sur le contexte fourni. Si une information manque, dis-le simplement, sans inventer.
3. N'invente jamais un code de cas : reprends un code ou un identifiant de la liste. Si plusieurs cas conviennent, demande lequel.
4. Ne prononce jamais un mot de passe.
5. Une demande d'action claire donne l'action ; une question donne « aucune ».
"""


# Ce qu'Alfred sait de lui-même et de l'agent : sans cela, il ne saurait que répondre à « qui es-tu ? »
IDENTITE = """Qui tu es :
- Alfred, le majordome de l'agent de test « agent-qa-playwright », version {version}. C'est un projet libre (licence MIT), publié sur GitHub par son auteur, {createur}, testeur QA en automatisation.
- Tu vis dans une page web locale, sur l'ordinateur du testeur : le cadran de la mission, la vue du navigateur et le journal des étapes. Tu parles avec la voix de l'ordinateur et tu comprends la parole grâce à la reconnaissance vocale du navigateur.
- En ce moment, c'est le modèle {modele} qui te fait parler.

Ce que fait l'agent que tu pilotes :
- Il exécute un cas de test écrit en français, en Markdown ou en Gherkin, dans un vrai navigateur Chromium piloté par Playwright. À chaque tour, il observe la page, le modèle d'IA (Gemini ou Claude) choisit une seule action en désignant un élément par son numéro, puis le programme l'exécute.
- L'IA propose, le programme vérifie : le verdict (réussi, échoué ou bloqué) vient des vérifications réelles, jamais de l'avis du modèle.
- Il voit aussi dans les cadres (iframes) et les composants web (Shadow DOM), manie les listes déroulantes faites de div, répond aux fenêtres JavaScript, attend un chargement lent et vérifie un format avec un motif. Les e-mails, téléphones, cartes et IBAN affichés par la page sont masqués avant l'envoi au modèle.
- Après chaque exécution, il génère trois tests qui se rejouent sans IA : pytest avec Playwright, Robot Framework avec SeleniumLibrary, et un scénario Gherkin en français. Un parcours réussi est mémorisé et rejoué sans IA ; si le site change, l'IA répare le parcours.
- Quand un test échoue, il rédige la fiche d'anomalie, avec un diagnostic de l'IA présenté comme un avis à confirmer. Sur une erreur technique de Playwright, le diagnostic s'appuie sur des extraits de la documentation officielle, avec leurs liens.
- À une question technique sur Playwright, Selenium ou Robot Framework (« comment gérer une iframe ? »), tu réponds à partir de leur documentation officielle, et le lien de la page s'affiche sous ta réponse.
- Il analyse un site pour proposer des user stories et leurs critères en Gherkin, puis les cas de test d'une user story.
- Une campagne de vingt cas sur cinq sites d'entraînement mesure la justesse de ses verdicts.
- Ses limites : il ne tape pas de touche du clavier, ne survole pas, ne glisse-dépose pas, ne suit pas un nouvel onglet, n'envoie pas de fichier, et s'arrête après vingt actions ou trois erreurs de suite."""


def system_prompt(provider: Provider) -> str:
    """Les consignes d'Alfred, avec ce qu'il sait de lui-même : version, auteur, modèle qui le fait parler."""
    modele = f"{getattr(provider, 'name', '')} ({getattr(provider, 'model', '')})".strip()
    identite = IDENTITE.format(version=__version__, createur=CREATEUR, modele=modele)
    return PROMPT.replace("{identite}", identite)  # pas de format() : les exemples JSON des consignes ont des accolades


def _case_label(case: dict) -> str:
    return case.get("code") or case["id"]


def case_keys(cases: list[dict]) -> dict[str, dict]:
    """Ce que le modèle peut citer pour désigner un cas : son code s'il est unique, sinon son identifiant."""
    codes: dict[str, list[dict]] = {}
    for case in cases:
        if case.get("code"):
            codes.setdefault(case["code"].upper(), []).append(case)
    keys = {case["id"]: case for case in cases}
    keys.update({code: found[0] for code, found in codes.items() if len(found) == 1})
    return keys


def _key_for(case: dict, keys: dict[str, dict]) -> str:
    code = (case.get("code") or "").upper()
    return code if code and keys.get(code) is case else case["id"]


def _when(iso: str) -> str:
    try:
        moment = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    return f"{moment:%d/%m %H:%M}"


def _result_line(result: dict) -> str:
    parts = [_when(result.get("debut", "")), result["titre"], result["statut"]]
    if result.get("conclusion"):
        parts.append(f"conclusion : {result['conclusion']}")
    failed = result.get("verifications_echouees") or []
    if failed:
        parts.append("vérifications en échec : " + " ; ".join(failed[:3]))
    diagnosis = result.get("diagnostic")
    if isinstance(diagnosis, dict) and diagnosis.get("titre"):
        parts.append(f"avis de l'IA ({diagnosis.get('nature', '')}, gravité {diagnosis.get('gravite', '')}) : "
                     f"{diagnosis['titre']}. {diagnosis.get('explication', '')}".strip())
    if result.get("appels_modele") == 0 and result.get("etapes_rejouees_depuis_la_memoire"):
        parts.append("rejoué depuis la mémoire, sans IA")
    return "- " + " | ".join(part for part in parts if part)


def build_context(cases: list[dict], results: list[dict], selection: dict | None = None, mission: str | None = None,
                  maitre: str | None = None, files: list[str] = (), now: datetime | None = None) -> str:
    """Ce que le modèle sait de la situation : date, cas de test, mission en cours, derniers résultats."""
    now = now or datetime.now()
    keys = case_keys(cases)
    lines = [f"Date : {JOURS[now.weekday()]} {now.day} {MOIS[now.month - 1]} {now.year}, {now:%H:%M}"]
    if maitre:
        lines.append(f"Maître (le testeur) : {maitre}")
    lines.append(f"Site par défaut : {default_url(cases, selection)}")
    lines.append(f"Cas affiché à l'écran : {_case_label(selection)} {selection['title']}" if selection
                 else "Cas affiché à l'écran : aucun")
    lines.append(f"Mission en cours : {mission}" if mission else "Mission en cours : aucune")
    lines += ["", "Cas de test disponibles (code ou identifiant | titre | site | remarque) :"]
    for case in cases:
        notes = ["scénario Gherkin"] if case.get("gherkin") else []
        if case.get("memory"):
            notes.append("parcours en mémoire, rejoué sans IA")
        if case.get("demo"):
            notes.append("démo sans IA disponible")
        title = case["title"][len(case["code"]):].strip() if case.get("code") and not case.get("gherkin") else case["title"]
        lines.append(f"- {_key_for(case, keys)} | {title} | {case.get('host', '')}" + (f" | {', '.join(notes)}" if notes else ""))
    lines += ["", "Derniers résultats (du plus récent au plus ancien) :"]
    lines += [_result_line(result) for result in results] or ["- aucun pour l'instant"]
    if files:
        lines += ["", "Fichiers de la dernière mission : " + ", ".join(files)]
    return "\n".join(lines)


def default_url(cases: list[dict], selection: dict | None = None) -> str:
    if selection and selection.get("url"):
        return selection["url"]
    return next((case["url"] for case in cases if case.get("url", "").startswith("https://")), "https://www.saucedemo.com/")


def build_message(text: str, history: list[dict], context: str) -> str:
    lines = [context, ""]
    exchanges = [h for h in history if isinstance(h, dict)][-MAX_HISTORY:]
    if exchanges:
        lines.append("Échanges précédents :")
        for item in exchanges:
            who = "Alfred" if item.get("role") == "alfred" else "Testeur"
            lines.append(f"{who} : {' '.join(str(item.get('texte', '')).split())[:MAX_TEXT]}")
        lines.append("")
    lines.append(f"Phrase du testeur : « {text} »")
    return "\n".join(lines)


# --- Mots de passe : jamais prononcés -----------------------------------------------------

_SECRET_PATTERNS = (
    re.compile(r"mot de passe\s*(?:«|\")\s*([^»\"]+?)\s*(?:»|\")", re.IGNORECASE),
    re.compile(r"(?:«|\")\s*([^»\"]+?)\s*(?:»|\")\s*dans le champ\s*(?:«\s*)?(?:password|mot de passe)", re.IGNORECASE),
)


def secrets_of(texts: list[str]) -> set[str]:
    """Les mots de passe cités dans les cas de test (« secret_sauce » dans le champ Password...)."""
    found = set()
    for text in texts:
        for pattern in _SECRET_PATTERNS:
            found.update(value.strip() for value in pattern.findall(text) if len(value.strip()) >= 4)
    return found


def redact(text: str, secrets: set[str]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        text = re.sub(re.escape(secret), "le mot de passe", text, flags=re.IGNORECASE)
    return text


# --- Réponse du modèle : contrôlée avant de partir vers la page --------------------------


def _say(text: object) -> str:
    """Une phrase à dire : sans retour à la ligne, sans symbole de mise en forme, de longueur raisonnable."""
    text = re.sub(r"[*`#>|]", "", " ".join(str(text or "").split())).replace("\u2014", ",")  # tiret cadratin
    return text if len(text) <= MAX_REPLY else text[: MAX_REPLY - 1].rsplit(" ", 1)[0] + "…"


def check_action(action: object, cases: list[dict], cases_dir: Path, default: str) -> tuple[dict, str]:
    """L'action validée, et la phrase à dire à la place de celle du modèle si elle ne tient pas (sinon "")."""
    if not isinstance(action, dict):
        return {"type": "aucune"}, ""
    kind = str(action.get("type") or "aucune").strip().lower()
    if kind not in ACTIONS or kind == "aucune":
        return {"type": "aucune"}, ""
    if kind == "arreter":
        return {"type": "arreter"}, ""
    if kind == "ouvrir":
        name = str(action.get("fichier") or "").strip().lower()
        if name not in FILES:
            return {"type": "aucune"}, ""
        checked = {"type": "ouvrir", "fichier": name}
        if action.get("cas"):
            checked["cas"] = _padded(str(action["cas"]).strip())
        return checked, ""
    if kind in ("lancer", "selectionner"):
        wanted = str(action.get("cas") or "").strip()
        keys = case_keys(cases)
        case = keys.get(wanted) or keys.get(wanted.upper()) or keys.get(_padded(wanted))
        if case is None:
            same = [c for c in cases if (c.get("code") or "").upper() == _padded(wanted).upper()]
            if len(same) > 1:  # les scénarios d'un même fichier .feature partagent leur code
                return {"type": "aucune"}, f"Plusieurs scénarios portent le code {same[0]['code']} : lequel voulez-vous ?"
            return {"type": "aucune"}, f"Je ne trouve pas le cas {wanted or 'demandé'} parmi vos cas de test."
        checked = {"type": kind, "cas": case["id"], "code": case.get("code") or ""}
        if kind == "lancer":
            url = _url(action.get("url"))
            if action.get("url") and not url:
                return {"type": "aucune"}, "Cette adresse ne me semble pas valide : elle doit commencer par http ou https."
            if url:
                checked["url"] = url
            if action.get("vision") is True:
                checked["vision"] = True
        return checked, ""
    if kind == "proposer":
        url = _url(action.get("url"), default)
        if not url:
            return {"type": "aucune"}, "Cette adresse ne me semble pas valide : elle doit commencer par http ou https."
        return {"type": "proposer", "url": url}, ""
    # rediger : un cas au format CT, avec le prochain numéro libre, montré avant d'être lancé
    try:
        case = case_from_dict(action)
    except ValueError:
        return {"type": "aucune"}, ("Je n'ai pas réussi à rédiger ce cas. Précisez-moi les étapes et le résultat attendu, "
                                    "s'il vous plaît.")
    url = _url(action.get("url"), default) or default
    code = next_codes(cases_dir, "CT", 1)[0]
    return {"type": "rediger", "titre": f"{code} {case.title}", "markdown": case.markdown(code, url)}, ""


def _url(value: object, default: str = "") -> str:
    """« saucedemo.com » devient https://saucedemo.com ; une adresse invalide donne "" (et la valeur par défaut si vide)."""
    url = str(value or "").strip()
    if not url:
        return default
    if re.fullmatch(r"(?:[\w-]+\.)+[a-z]{2,}(?::\d+)?(?:/\S*)?", url, flags=re.IGNORECASE):
        url = "https://" + url
    return url if URL.fullmatch(url) else ""


def _padded(code: str) -> str:
    """« CT-3 » ou « ct 3 » désignent CT-03."""
    match = re.fullmatch(r"\s*([A-Za-z]{2,})[\s-]*(\d+)\s*", code)
    return f"{match.group(1).upper()}-{int(match.group(2)):02d}" if match else code


FILE_NAMES = {"rapport": "le rapport", "anomalie": "la fiche d'anomalie", "pytest": "le test pytest", "robot": "le test Robot",
              "gherkin": "le scénario Gherkin", "robot_bdd": "le scénario Gherkin pour Robot"}


def locate_file(action: dict, results: list[dict]) -> tuple[dict, str]:
    """Le fichier demandé, pris dans le résultat le plus récent qui l'a (et du cas cité, s'il y en a un)."""
    code = action.get("cas", "")
    for result in results:
        if code and not result["titre"].upper().startswith(code.upper()):
            continue
        name = (result.get("fichiers") or {}).get(action["fichier"])
        if name and result.get("dossier"):
            return {"type": "ouvrir", "fichier": action["fichier"], "url": f"/rapports/{result['dossier']}/{name}",
                    "titre": result["titre"]}, ""
    target = f" pour {code}" if code else ""
    return {"type": "aucune"}, f"Je n'ai pas encore {FILE_NAMES[action['fichier']]}{target} à vous montrer."


def parse_reply(raw: str, cases: list[dict], cases_dir: Path, default: str, secrets: set[str] = frozenset(),
                results: list[dict] = ()) -> dict:
    """La réponse du modèle, contrôlée : une phrase à dire (sans mot de passe) et une action permise."""
    try:
        data = json.loads(_extract_json(raw))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"réponse illisible ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError("un objet JSON est attendu")
    action, correction = check_action(data.get("action"), cases, Path(cases_dir), default)
    if action["type"] == "ouvrir":
        action, correction = locate_file(action, list(results))
    reply = _say(redact(str(correction or data.get("reponse") or ""), secrets))  # le mot de passe d'abord, tel quel
    if not reply:
        reply = "C'est entendu." if action["type"] != "aucune" else "Je n'ai pas de réponse à vous donner pour l'instant."
    return {"reponse": reply, "action": action}


def converse(provider: Provider, text: str, history: list[dict], context: str, cases: list[dict], cases_dir: Path,
             default: str, secrets: set[str] = frozenset(), results: list[dict] = ()) -> dict:
    """Une phrase du testeur, une réponse d'Alfred (une requête au modèle).

    Une question technique sur Playwright, Selenium ou Robot Framework reçoit une réponse tirée
    de la documentation officielle, avec les liens des pages citées (voir agent/documentation.py).
    """
    pages = documentation.question_pages(text)
    reply = documentation.answer(provider, text, pages, lambda model, system, user: ask(model, system, user, max_tokens=2000)) \
        if pages else None
    if reply is not None:  # None : les pages lues ne parlent pas du sujet, Alfred répond comme d'habitude
        reply["reponse"] = _say(redact(reply["reponse"], secrets))
        return reply
    raw = ask(provider, system_prompt(provider), build_message(text, history, context), max_tokens=4000)
    return parse_reply(raw, cases, cases_dir, default, secrets, results)
