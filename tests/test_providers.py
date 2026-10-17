"""Construction des requêtes Gemini et Claude, sans appel réseau."""

import email.message
import io
import json
import urllib.error

import pytest

from agent import providers
from agent.providers import ClaudeProvider, GeminiProvider, ProviderError, ScriptedProvider


def test_gemini_requete(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    provider = GeminiProvider(api_key="cle-de-test")
    url, headers, payload = provider.build_request("consignes", "message")
    assert url.endswith(f"/models/{providers.DEFAULT_GEMINI_MODEL}:generateContent")
    assert headers == {"x-goog-api-key": "cle-de-test"}  # la clé passe dans l'en-tête, pas dans l'URL
    assert payload["systemInstruction"]["parts"][0]["text"] == "consignes"
    assert payload["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_reponse():
    data = {"candidates": [{"content": {"parts": [{"text": '{"action": '}, {"text": '"finish"}'}]}}]}
    assert GeminiProvider.parse_response(data) == '{"action": "finish"}'


def test_claude_requete(monkeypatch):
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    provider = ClaudeProvider(api_key="cle-de-test")
    url, headers, payload = provider.build_request("consignes", "message")
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "cle-de-test"
    assert payload["model"] == providers.DEFAULT_CLAUDE_MODEL
    assert payload["messages"] == [{"role": "user", "content": "message"}]


def test_claude_reponse():
    data = {"content": [{"type": "text", "text": '{"action": "finish"}'}]}
    assert ClaudeProvider.parse_response(data) == '{"action": "finish"}'


def test_reponse_inattendue():
    with pytest.raises(ProviderError):
        GeminiProvider.parse_response({"promptFeedback": {"blockReason": "SAFETY"}})


def test_cle_absente(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="GEMINI_API_KEY"):
        GeminiProvider()


def test_appel_complet_sans_reseau(monkeypatch):
    """next_action() enchaîne requête et lecture de la réponse."""
    sent = {}

    def fake_post(url, headers, payload):
        sent["url"] = url
        return {"content": [{"type": "text", "text": '{"action": "check_url", "args": {"contains": "inventory"}}'}]}

    monkeypatch.setattr(providers, "post_json", fake_post)
    raw = ClaudeProvider(api_key="cle").next_action("consignes", "message", observation=None)
    assert json.loads(raw)["action"] == "check_url"
    assert sent["url"].startswith("https://api.anthropic.com")


def test_faux_modele_epuise():
    provider = ScriptedProvider([])
    assert json.loads(provider.next_action("", "", observation=None))["args"]["status"] == "BLOQUE"



# --- Quotas (erreur 429) ------------------------------------------------------------

def gemini_429(quota_id, value="20", delay="40s"):
    return json.dumps({"error": {"code": 429, "message": "You exceeded your current quota", "status": "RESOURCE_EXHAUSTED",
        "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                     "violations": [{"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                                     "quotaId": quota_id, "quotaValue": value}]},
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}]}})


def test_quota_du_jour_gemini():
    exc = providers.parse_rate_limit(gemini_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
    assert exc.daily and not exc.no_free_quota and exc.retry_after == 40
    assert "quota du jour épuisé" in str(exc) and "Il se renouvelle à " in str(exc)


def test_quota_par_minute_gemini():
    exc = providers.parse_rate_limit(gemini_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", delay="17.5s"))
    assert not exc.daily and exc.retry_after == 17.5


def test_modele_sans_quota_gratuit():
    exc = providers.parse_rate_limit(gemini_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier", value="0"))
    assert exc.no_free_quota and "limite 0" in str(exc)


def test_quota_claude_et_reponse_illisible():
    entetes = email.message.Message()
    entetes["Retry-After"] = "30"
    corps = json.dumps({"type": "error", "error": {"type": "rate_limit_error", "message": "Rate limited"}})
    assert providers.parse_rate_limit(corps, entetes).retry_after == 30
    assert providers.parse_rate_limit("<html>Too Many Requests</html>").retry_after is None


def test_erreur_429_remontee_sans_attente_aveugle(monkeypatch):
    """Le quota n'est plus attendu en silence par post_json : l'agent décide, et le dit."""
    def urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", email.message.Message(),
                                     io.BytesIO(gemini_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier").encode()))
    monkeypatch.setattr(providers.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(providers.time, "sleep", lambda s: pytest.fail("aucune attente silencieuse"))
    with pytest.raises(providers.RateLimitError) as info:
        providers.post_json("https://exemple.test", {}, {})
    assert info.value.retry_after == 40


# --- Vision : la capture d'écran part avec la demande ------------------------------------

IMAGE = ("image/jpeg", b"\xff\xd8\xff fausse image")


def test_gemini_avec_image():
    url, headers, payload = GeminiProvider(api_key="cle").build_request("consignes", "message", [IMAGE], max_tokens=4000)
    parts = payload["contents"][0]["parts"]
    assert parts[0] == {"inlineData": {"mimeType": "image/jpeg", "data": "/9j/IGZhdXNzZSBpbWFnZQ=="}}
    assert parts[-1] == {"text": "message"}  # l'image avant le texte, comme le conseille Google
    assert payload["generationConfig"]["maxOutputTokens"] == 4000


def test_claude_avec_image():
    url, headers, payload = ClaudeProvider(api_key="cle").build_request("consignes", "message", [IMAGE], max_tokens=4000)
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"
    assert content[1] == {"type": "text", "text": "message"}
    assert payload["max_tokens"] == 4000


def test_next_action_joint_la_capture(monkeypatch):
    """Avec la vision, l'observation porte la capture : next_action l'envoie au modèle."""
    sent = {}
    monkeypatch.setattr(providers, "post_json", lambda url, headers, payload: sent.update(payload) or
                        {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})

    class Observation:
        screenshot = b"\xff\xd8\xff capture"

    GeminiProvider(api_key="cle").next_action("consignes", "message", Observation())
    assert sent["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "image/jpeg"
    GeminiProvider(api_key="cle").next_action("consignes", "message", None)
    assert sent["contents"][0]["parts"] == [{"text": "message"}]


def test_faux_modele_reponse_libre():
    provider = ScriptedProvider([{"raw": '{"stories": []}'}])
    assert provider.complete("consignes", "message") == '{"stories": []}'
    with pytest.raises(ProviderError, match="aucune réponse prévue"):
        provider.complete("consignes", "message")
