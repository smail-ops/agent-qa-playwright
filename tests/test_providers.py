"""Construction des requêtes Gemini et Claude, sans appel réseau."""

import json

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
