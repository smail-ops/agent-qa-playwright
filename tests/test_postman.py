"""Collection Postman des appels d'API de la page : capture pendant le test, masquage, rejeu avec Newman."""

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_alfred import alfred, read_events, request  # noqa: F401 (fixture partagée)
from test_industrialisation import demo_case, demo_text, in_thread, scenario

from agent.coffre import Coffre
from agent.execution import run_case
from agent.postman import ApiCall, ApiRecorder, generate_postman, secret_variables
from agent.testcase import parse_test_case

NEWMAN_DOUBLE = Path(__file__).resolve().parent / "newman_double.mjs"


@pytest.fixture
def catalogue(demo_url, tmp_path):
    """CT-97 : la page charge le catalogue, la recherche et le détail par son API."""
    return in_thread(lambda: run_case(demo_case("CT-97", demo_url), scenario("CT-97"), tmp_path / "ct97",
                                      sessions_dir=tmp_path / "sessions", env_path=tmp_path / ".env"))


def test_appels_d_api_captures(catalogue, demo_url):
    assert catalogue.status == "PASS", catalogue.summary
    calls = [(call.method, call.url.replace(demo_url, ""), call.status, call.step) for call in catalogue.api_calls]
    assert calls == [("GET", "/api/produits.json", 200, 1), ("GET", "/api/produits.json?recherche=lampe", 200, 3),
                     ("GET", "/api/produits/2.json", 200, 5)]
    assert catalogue.api_calls[2].fields == ["id", "nom", "prix", "stock", "description"]
    assert catalogue.api_calls[0].headers == {"accept": "application/json"}
    report = catalogue.report_path.read_text(encoding="utf-8")
    assert "## Collection Postman" in report and "La page a fait 3 appels d'API pendant le test." in report
    assert f"npx newman run {catalogue.postman_path.name} -e {catalogue.postman_env_path.name}" in report
    resultat = json.loads((catalogue.out_dir / "resultat.json").read_text(encoding="utf-8"))
    assert resultat["fichiers"]["postman"] == catalogue.postman_path.name


def test_collection_et_environnement(catalogue, demo_url):
    collection = json.loads(catalogue.postman_path.read_text(encoding="utf-8"))
    assert collection["info"]["schema"] == "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
    assert collection["info"]["name"] == "CT-97 Démo locale : catalogue chargé par l'API"
    assert [item["name"] for item in collection["item"]] == [
        "GET /api/produits.json", "GET /api/produits.json?recherche=lampe", "GET /api/produits/2.json"]
    search = collection["item"][1]["request"]
    assert search["url"] == {"raw": "{{baseUrl}}/api/produits.json?recherche=lampe", "host": ["{{baseUrl}}"],
                             "path": ["api", "produits.json"], "query": [{"key": "recherche", "value": "lampe"}]}
    assert search["description"] == "Envoyée par la page à l'étape 3 du test."
    tests = collection["item"][2]["event"][0]["script"]["exec"]
    assert tests[:3] == ['pm.test("Statut 200", function () {', "    pm.response.to.have.status(200);", "});"]
    assert '    ["id", "nom", "prix", "stock", "description"].forEach(function (champ) {' in tests
    assert "event" not in collection  # pas de {unique} dans ces appels : pas de script de collection
    environment = json.loads(catalogue.postman_env_path.read_text(encoding="utf-8"))
    assert environment["values"] == [{"key": "baseUrl", "value": demo_url, "type": "default", "enabled": True}]
    assert environment["_postman_variable_scope"] == "environment"


def test_collection_rejouee_comme_avec_newman(catalogue):
    if shutil.which("node") is None:
        pytest.skip("Node.js n'est pas installé ici")
    command = ["node", str(NEWMAN_DOUBLE), str(catalogue.postman_path), str(catalogue.postman_env_path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "9 tests, 0 en échec" in result.stdout and "✓ Champs de la réponse : id, nom, prix, stock" in result.stdout


def test_sans_appel_d_api_pas_de_collection(demo_url, tmp_path):
    result = in_thread(lambda: run_case(demo_case("CT-00", demo_url), scenario("CT-00"), tmp_path / "ct00",
                                        sessions_dir=tmp_path / "sessions", env_path=tmp_path / ".env"))
    assert result.status == "PASS" and result.api_calls == [] and result.postman_path is None
    assert "Collection Postman" not in result.report_path.read_text(encoding="utf-8")


def _run(calls, case_text, unique=None):
    case = parse_test_case(case_text)
    return SimpleNamespace(case=case, coffre=Coffre.from_case(case.text), unique=unique, api_calls=calls,
                           started_at=datetime(2026, 10, 24, 9, 0), postman_env_path=None)


def test_rien_de_secret_dans_la_collection():
    texte = ("# CT-60 Connexion par l'API\nURL : https://boutique.exemple.fr/connexion\n\n## Étapes\n"
             "1. Saisir « alfred.{unique}@exemple.fr » dans le champ E-mail\n"
             "2. Saisir « P@ss mot!2026 » dans le champ Mot de passe\n")
    unique = "1024090000"
    calls = [
        ApiCall("POST", "https://api.exemple.fr/v1/connexion", {"content-type": "application/json"},
                json.dumps({"email": f"alfred.{unique}@exemple.fr", "motDePasse": "P@ss mot!2026"}), 200,
                "application/json", ["jeton", "client"], 3),
        ApiCall("POST", "https://boutique.exemple.fr/connexion", {"content-type": "application/x-www-form-urlencoded"},
                "email=alfred.1024090000%40exemple.fr&password=P%40ss+mot%212026", 302, "text/html", [], 3),
        ApiCall("GET", f"https://api.exemple.fr/v1/clients?email=alfred.{unique}%40exemple.fr",
                {"accept": "application/json", "authorization": "Bearer eyJhbGciOi.secret.jeton"}, None, 200,
                "application/json", ["id"], 4),
    ]
    collection, environment = generate_postman(_run(calls, texte, unique))
    dump = json.dumps(collection, ensure_ascii=False) + json.dumps(environment, ensure_ascii=False)
    for secret in ("P@ss mot!2026", "P%40ss+mot%212026", "eyJhbGciOi", unique):
        assert secret not in dump, secret
    login, form, clients = (item["request"] for item in collection["item"])
    assert json.loads(login["body"]["raw"]) == {"email": "alfred.{{unique}}@exemple.fr", "motDePasse": "{{motDePasse}}"}
    assert login["body"]["options"] == {"raw": {"language": "json"}}
    assert login["url"]["raw"] == "{{baseUrl2}}/v1/connexion"  # l'API est sur un autre site que la page
    assert form["body"]["urlencoded"][1] == {"key": "password", "value": "{{motDePasse}}", "type": "text"}
    assert form["url"]["raw"] == "{{baseUrl}}/connexion"
    assert {"key": "Authorization", "value": "Bearer {{jeton}}"} in clients["header"]
    assert collection["item"][2]["name"] == "GET /v1/clients?email=alfred.{{unique}}%40exemple.fr"
    assert collection["item"][1]["event"][0]["script"]["exec"][:2] == [
        'pm.test("Statut 302", function () {', "    pm.response.to.have.status(302);"]
    script = collection["event"][0]["script"]["exec"]
    assert script[1] == 'if (!pm.variables.get("unique")) {'  # une seule valeur pour tout le lancement
    assert environment["values"] == [
        {"key": "baseUrl", "value": "https://boutique.exemple.fr", "type": "default", "enabled": True},
        {"key": "baseUrl2", "value": "https://api.exemple.fr", "type": "default", "enabled": True},
        {"key": "jeton", "value": "", "type": "secret", "enabled": True},
        {"key": "motDePasse", "value": "", "type": "secret", "enabled": True},
    ]


def test_appels_retenus(tmp_path):
    """Seuls fetch et XMLHttpRequest comptent, sans les outils de mesure d'audience ou de publicité."""
    recorder = ApiRecorder(SimpleNamespace(on=lambda name, handler: None))

    def response(url, kind="fetch", method="GET", body=None, content_type="application/json", data=b'{"ok": true}'):
        request = SimpleNamespace(resource_type=kind, method=method, url=url, post_data=body,
                                  headers={"Accept": "application/json", "Cookie": "session=1", "User-Agent": "x"})
        return SimpleNamespace(request=request, url=url, status=200, headers={"content-type": content_type},
                               body=lambda: data)

    recorder.step = 2
    for answer in (response("https://site.fr/api/panier"), response("https://site.fr/logo.png", kind="image"),
                   response("https://www.google-analytics.com/g/collect", method="POST"),
                   response("https://site.fr/api/commande", kind="xhr", method="POST", body='{"id": 3}')):
        recorder._on_response(answer)
    assert [(call.method, call.url, call.step) for call in recorder.calls] == [
        ("GET", "https://site.fr/api/panier", 2), ("POST", "https://site.fr/api/commande", 2)]
    assert recorder.calls[0].headers == {"accept": "application/json"}  # ni cookie, ni navigateur
    assert recorder.calls[0].fields == ["ok"] and recorder.calls[1].body == '{"id": 3}'


def test_variables_secretes_signalees(tmp_path):
    env = tmp_path / "env.json"
    env.write_text(json.dumps({"values": [{"key": "baseUrl", "type": "default"}, {"key": "motDePasse", "type": "secret"}]}),
                   encoding="utf-8")
    assert secret_variables(SimpleNamespace(postman_env_path=env)) == ["motDePasse"]
    assert secret_variables(SimpleNamespace(postman_env_path=None)) == []


def test_alfred_sert_la_collection(alfred, demo_url):
    (alfred.cases_dir / "demo" / "CT-97_catalogue.md").write_text(demo_text("CT-97", demo_url), encoding="utf-8")
    shutil.copy(Path(__file__).resolve().parent.parent / "cas_de_test" / "demo" / "scenario_CT-97.json",
                alfred.cases_dir / "demo")
    data = request(alfred, "POST", "/api/missions", {"case": "demo/CT-97_catalogue.md", "provider": "scripted"})[1]
    done = read_events(alfred, data["id"])[-1]
    assert done["status"] == "PASS" and done["files"]["postman"].endswith(".postman_collection.json")
    status, collection = request(alfred, "GET", done["files"]["postman"])
    assert status == 200 and collection["info"]["name"].startswith("CT-97")
