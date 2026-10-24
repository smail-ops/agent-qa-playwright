"""Collection Postman des appels d'API faits par la page pendant le test.

Pendant l'exécution, l'agent note les requêtes que la page envoie à son API (fetch,
XMLHttpRequest) et leurs réponses. À la fin, il écrit une collection Postman (format
v2.1) : une requête par appel, avec ses tests (statut, réponse JSON, champs de la
réponse), et un environnement où l'adresse du site est la variable {{baseUrl}}. On
l'importe dans Postman (Fichier, Importer), ou on la rejoue en ligne de commande avec
Newman, l'outil de Postman pour l'intégration continue :

    npx newman run <cas>.postman_collection.json -e <cas>.postman_environment.json

Rien de secret n'y est écrit. Un mot de passe du cas de test devient {{motDePasse}},
variable secrète de l'environnement, laissée vide : à remplir dans Postman, ou à donner
à Newman (--env-var motDePasse=...). Un en-tête d'authentification devient {{jeton}}, de
la même façon. Les cookies ne sont pas repris. {unique} devient {{unique}}, recréé
avant chaque lancement par un script de la collection.

Les appels des outils de mesure d'audience, de publicité ou de consentement aux cookies
sont ignorés : ce ne sont pas ceux de l'application testée.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, quote, quote_plus, urlsplit

if TYPE_CHECKING:
    from .runner import RunResult

SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
MAX_CALLS = 50  # appels gardés au plus pour une exécution
MAX_BODY = 20_000  # caractères du corps d'une requête gardés au plus
MAX_FIELDS = 12  # champs de la réponse vérifiés au plus
KEPT_HEADERS = ("accept", "content-type", "authorization", "x-api-key", "x-requested-with")
SECRET_HEADERS = ("authorization", "x-api-key")
# Mesure d'audience, publicité, consentement aux cookies, polices... : pas l'API de l'application testée
_IGNORED_HOSTS = re.compile(
    r"google-analytics|googletagmanager|analytics\.google|doubleclick|googlesyndication|googleadservices|adservice|"
    r"facebook|fbcdn|hotjar|segment\.(?:io|com)|sentry|clarity\.ms|matomo|piwik|cookiebot|onetrust|cookielaw|didomi|"
    r"consensu|fundingchoices|quantcast|gstatic|fonts\.googleapis|recaptcha|criteo|taboola|outbrain|amazon-adsystem|"
    r"newrelic|nr-data|datadoghq|mixpanel|amplitude|intercom|zendesk|tiktok|linkedin|twitter|bing\.com", re.IGNORECASE)


@dataclass
class ApiCall:
    """Un appel d'API fait par la page : la requête, et ce que la réponse a montré."""

    method: str
    url: str
    headers: dict[str, str]
    body: str | None
    status: int
    content_type: str
    fields: list[str] = field(default_factory=list)  # champs de la réponse JSON (du premier élément d'une liste)
    step: int = 0  # étape du test pendant laquelle la page a fait l'appel


def _fields(text: str) -> list[str]:
    """Les champs d'une réponse JSON : ceux de l'objet, ou du premier objet d'une liste."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    return [str(key) for key in data][:MAX_FIELDS] if isinstance(data, dict) else []


class ApiRecorder:
    """Note les appels d'API (fetch, XMLHttpRequest) de toutes les pages du navigateur."""

    def __init__(self, context) -> None:
        self.calls: list[ApiCall] = []
        self.step = 0  # étape en cours, tenue à jour par l'exécution
        context.on("response", self._on_response)

    def _on_response(self, response) -> None:
        from playwright.sync_api import Error as PlaywrightError

        try:
            request = response.request
            if request.resource_type not in ("xhr", "fetch") or len(self.calls) >= MAX_CALLS:
                return
            if _IGNORED_HOSTS.search(urlsplit(response.url).hostname or ""):
                return
            content_type = response.headers.get("content-type", "")
            fields = []
            if "json" in content_type.lower():
                try:
                    fields = _fields(response.body().decode("utf-8", errors="replace"))
                except PlaywrightError:
                    fields = []  # réponse déjà libérée par le navigateur (page quittée)
            headers = {name.lower(): value for name, value in request.headers.items() if name.lower() in KEPT_HEADERS}
            body = request.post_data
            self.calls.append(ApiCall(request.method, request.url, headers, body[:MAX_BODY] if body else None,
                                      response.status, content_type, fields, self.step))
        except (PlaywrightError, UnicodeDecodeError):
            return


# --- La collection --------------------------------------------------------------------------


def _secret_variables(run: RunResult) -> dict[str, str]:
    """Valeur du mot de passe -> variable de l'environnement : [mot de passe] donne motDePasse, [mot de passe 2] motDePasse2."""
    secrets = run.coffre.secrets if run.coffre is not None else {}
    names = {}
    for placeholder, value in secrets.items():
        index = re.search(r"(\d+)\]$", placeholder)
        names[value] = "motDePasse" + (index.group(1) if index and index.group(1) != "1" else "")
    return names


class _Masque:
    """Remplace dans les requêtes ce qui ne doit pas être écrit : mots de passe, valeur unique de l'exécution."""

    def __init__(self, run: RunResult) -> None:
        self.secrets = _secret_variables(run)
        self.unique = getattr(run, "unique", None)
        self.used: set[str] = set()  # variables utilisées : motDePasse, jeton, unique

    def text(self, text: str) -> str:
        for value, name in sorted(self.secrets.items(), key=lambda item: -len(item[0])):
            # le mot de passe tel quel, encodé dans une URL ou un formulaire, ou échappé dans du JSON
            for form in dict.fromkeys((value, quote(value, safe=""), quote_plus(value), json.dumps(value)[1:-1])):
                if form and form in text:
                    text = text.replace(form, "{{" + name + "}}")
                    self.used.add(name)
        if self.unique and self.unique in text:
            text = text.replace(self.unique, "{{unique}}")
            self.used.add("unique")
        return text

    def header(self, name: str, value: str) -> str:
        if name in SECRET_HEADERS:  # un jeton d'accès ne s'écrit jamais : variable secrète, à remplir
            self.used.add("jeton")
            scheme = value.split(" ", 1)[0] if " " in value else ""
            return f"{scheme} {{{{jeton}}}}".strip()
        return self.text(value)


def _origins(run: RunResult, calls: list[ApiCall]) -> dict[str, str]:
    """Origine (https://site:port) -> variable : {{baseUrl}} pour le site du cas de test, puis baseUrl2..."""
    case_origin = "{0.scheme}://{0.netloc}".format(urlsplit(run.case.url))
    found = list(dict.fromkeys([case_origin] + ["{0.scheme}://{0.netloc}".format(urlsplit(call.url)) for call in calls]))
    used = {"{0.scheme}://{0.netloc}".format(urlsplit(call.url)) for call in calls}
    found = [origin for origin in found if origin in used]
    return {origin: "baseUrl" if index == 0 else f"baseUrl{index + 1}" for index, origin in enumerate(found)}


def _url(call: ApiCall, origins: dict[str, str], masque: _Masque) -> dict:
    parts = urlsplit(call.url)
    variable = origins["{0.scheme}://{0.netloc}".format(parts)]
    path = masque.text(parts.path)
    query = [{"key": masque.text(key), "value": masque.text(value)} for key, value in parse_qsl(parts.query, keep_blank_values=True)]
    raw = "{{" + variable + "}}" + path + (("?" + masque.text(parts.query)) if parts.query else "")
    url = {"raw": raw, "host": ["{{" + variable + "}}"], "path": [segment for segment in path.split("/") if segment]}
    if query:
        url["query"] = query
    return url


def _body(call: ApiCall, masque: _Masque) -> dict | None:
    if not call.body:
        return None
    kind = call.headers.get("content-type", "").lower()
    if "application/x-www-form-urlencoded" in kind:
        return {"mode": "urlencoded", "urlencoded": [{"key": masque.text(key), "value": masque.text(value), "type": "text"}
                                                     for key, value in parse_qsl(call.body, keep_blank_values=True)]}
    body = {"mode": "raw", "raw": masque.text(call.body)}
    if "json" in kind:
        body["options"] = {"raw": {"language": "json"}}
    return body


def _tests(call: ApiCall) -> list[str]:
    """Les tests Postman de la requête : ce que la réponse a montré pendant l'exécution de l'agent."""
    lines = [f'pm.test("Statut {call.status}", function () {{', f"    pm.response.to.have.status({call.status});", "});"]
    if "json" in call.content_type.lower():
        lines += ['pm.test("Réponse au format JSON", function () {', "    pm.response.to.be.json;", "});"]
        if call.fields:
            fields = ", ".join(call.fields)
            lines += [f'pm.test({json.dumps("Champs de la réponse : " + fields, ensure_ascii=False)}, function () {{',
                      "    const donnees = pm.response.json();",
                      "    const element = Array.isArray(donnees) ? donnees[0] : donnees;",
                      f"    {json.dumps(call.fields, ensure_ascii=False)}.forEach(function (champ) {{",
                      "        pm.expect(element).to.have.property(champ);",
                      "    });",
                      "});"]
    return lines


# {unique} : une valeur nouvelle à chaque lancement, au même format que pour l'agent (donnees.FORMAT : mois, jour,
# heure, minutes, secondes). Le script de la collection passe avant chaque requête : une variable locale garde la même
# valeur pour toutes les requêtes d'un lancement (Newman, exécuteur de collection de Postman).
_UNIQUE_SCRIPT = [
    "// {unique} du cas de test : une valeur nouvelle à chaque lancement, comme pour l'agent",
    'if (!pm.variables.get("unique")) {',
    "    const maintenant = new Date();",
    '    const deux = function (nombre) { return String(nombre).padStart(2, "0"); };',
    '    pm.variables.set("unique", deux(maintenant.getMonth() + 1) + deux(maintenant.getDate()) + '
    "deux(maintenant.getHours()) + deux(maintenant.getMinutes()) + deux(maintenant.getSeconds()));",
    "}",
]


def _calls(run: RunResult) -> list[ApiCall]:
    """Les appels de l'exécution, sans doublon (même méthode, même adresse, même corps)."""
    seen, calls = set(), []
    for call in getattr(run, "api_calls", None) or []:
        key = (call.method, call.url, call.body)
        if key not in seen:
            seen.add(key)
            calls.append(call)
    return calls


def collection_filename(run: RunResult) -> str:
    return f"{run.case.slug}.postman_collection.json"


def environment_filename(run: RunResult) -> str:
    return f"{run.case.slug}.postman_environment.json"


def generate_postman(run: RunResult) -> tuple[dict, dict] | None:
    """La collection et son environnement, ou None si la page n'a fait aucun appel d'API."""
    calls = _calls(run)
    if not calls:
        return None
    masque, origins = _Masque(run), _origins(run, calls)
    items = []
    for call in calls:
        parts = urlsplit(call.url)
        name = f"{call.method} {parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")
        request = {
            "method": call.method,
            "header": [{"key": key.title() if key != "x-api-key" else key, "value": masque.header(key, value)}
                       for key, value in call.headers.items()],
            "url": _url(call, origins, masque),
        }
        body = _body(call, masque)
        if body is not None:
            request["body"] = body
        description = f"Envoyée par la page à l'étape {call.step} du test." if call.step else "Envoyée par la page pendant le test."
        items.append({"name": masque.text(name), "request": {**request, "description": description},
                      "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": _tests(call)}}]})
    newman = f"npx newman run {collection_filename(run)} -e {environment_filename(run)}"
    collection = {
        "info": {
            "_postman_id": str(uuid.uuid4()),
            "name": run.case.title,
            "description": (f"Appels d'API enregistrés par agent-qa-playwright pendant le cas de test « {run.case.title} », "
                            f"le {run.started_at:%d/%m/%Y à %H:%M}. Chaque requête vérifie ce que la réponse a montré "
                            f"pendant l'exécution. En ligne de commande : {newman}"),
            "schema": SCHEMA,
        },
        "item": items,
    }
    if "unique" in masque.used:
        collection["event"] = [{"listen": "prerequest", "script": {"type": "text/javascript", "exec": _UNIQUE_SCRIPT}}]
    values = [{"key": variable, "value": origin, "type": "default", "enabled": True} for origin, variable in origins.items()]
    for name in sorted(masque.used - {"unique"}):  # mots de passe et jetons : variables secrètes, laissées vides
        values.append({"key": name, "value": "", "type": "secret", "enabled": True})
    environment = {"id": str(uuid.uuid4()), "name": f"agent-qa-playwright : {run.case.title}", "values": values,
                   "_postman_variable_scope": "environment"}
    return collection, environment


def write_postman(run: RunResult) -> None:
    """Écrit la collection et l'environnement dans le dossier du rapport, si la page a appelé une API."""
    generated = generate_postman(run)
    if generated is None:
        return
    collection, environment = generated
    run.postman_path = run.out_dir / collection_filename(run)
    run.postman_env_path = run.out_dir / environment_filename(run)
    run.postman_path.write_text(json.dumps(collection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run.postman_env_path.write_text(json.dumps(environment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def secret_variables(run: RunResult) -> list[str]:
    """Les variables secrètes de l'environnement (mots de passe, jetons) : à remplir avant de lancer la collection."""
    if getattr(run, "postman_env_path", None) is None:
        return []
    try:
        data = json.loads(run.postman_env_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [value["key"] for value in data.get("values", []) if value.get("type") == "secret"]
