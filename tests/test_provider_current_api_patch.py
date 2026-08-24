from learnerbot import ai_council_http_patch as base
from learnerbot import provider_current_api_patch as compat


def test_gemini_current_omits_removed_sampling_fields(monkeypatch):
    captured = {}

    def fake_http(url, *, headers, payload=None, method=None, timeout=90):
        if url.endswith("/models"):
            return 200, {
                "models": [{
                    "name": "models/gemini-3.5-flash-lite",
                    "supportedGenerationMethods": ["generateContent"],
                }]
            }, "", {}
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = payload
        return 200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}, "", {}

    monkeypatch.setattr(base, "_http_json", fake_http)
    rc, out, err = compat._call_gemini_current(
        "hello",
        {"GEMINI_API_KEY": "test", "GEMINI_COUNCIL_MODEL": "gemini-stale-model"},
    )
    assert (rc, out, err) == (0, "ok", "")
    assert captured["url"].endswith("/models/gemini-3.5-flash-lite:generateContent")
    assert captured["headers"]["x-goog-api-key"] == "test"
    generation = captured["payload"]["generationConfig"]
    assert generation == {"maxOutputTokens": 2400}
    assert "temperature" not in generation
    assert "top_p" not in generation
    assert "top_k" not in generation


def test_gemini_discovery_prefers_configured_generate_content_model(monkeypatch):
    monkeypatch.setattr(
        base,
        "_http_json",
        lambda *a, **k: (
            200,
            {
                "models": [
                    {"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/gemini-custom", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/embed-only", "supportedGenerationMethods": ["embedContent"]},
                ]
            },
            "",
            {},
        ),
    )
    model, error = compat._discover_gemini_model_current(
        "test",
        {"GEMINI_COUNCIL_MODEL": "models/gemini-custom"},
    )
    assert model == "gemini-custom"
    assert error == ""


def test_deepseek_retired_alias_falls_forward_when_discovery_unavailable(monkeypatch):
    monkeypatch.setattr(
        base,
        "_http_json",
        lambda *a, **k: (503, {"error": {"message": "temporary"}}, "", {}),
    )
    model, error = compat._discover_deepseek_model_current(
        "test",
        {"DEEPSEEK_COUNCIL_MODEL": "deepseek-chat"},
    )
    assert model == "deepseek-v4-flash"
    assert error == ""


def test_deepseek_prefers_current_flash_model(monkeypatch):
    monkeypatch.setattr(
        base,
        "_http_json",
        lambda *a, **k: (
            200,
            {"data": [{"id": "deepseek-v4-pro"}, {"id": "deepseek-v4-flash"}]},
            "",
            {},
        ),
    )
    model, error = compat._discover_deepseek_model_current("test", {})
    assert model == "deepseek-v4-flash"
    assert error == ""
