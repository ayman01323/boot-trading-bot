from __future__ import annotations

from learnerbot import ai_council_live_web_patch as live


def _final_prompt(question: str) -> str:
    return (
        "You are PasPuss AI. Give the user one direct answer.\n\n"
        f"USER QUESTION:\n{question}\n\n"
        "PRIVATE DRAFTING MATERIAL:\ninternal drafts"
    )


def test_current_weather_triggers_web_search() -> None:
    assert live._needs_web_search(_final_prompt("What is the current temperature in London?")) is True
    assert live._question_requires_live("What is the current temperature in London?") is True


def test_current_time_with_standalone_now_triggers_live_search() -> None:
    question = "what is the time itinisa now"
    assert live._question_requires_live(question) is True
    assert live._needs_web_search(_final_prompt(question)) is True


def test_clock_question_without_now_still_triggers_live_search() -> None:
    assert live._question_requires_live("What time is it in Tunisia?") is True
    assert live._question_requires_live("What is the local time in Tokyo?") is True
    assert live._question_requires_live("What date is it in Sydney?") is True


def test_time_concept_question_stays_static() -> None:
    assert live._question_requires_live("Explain time complexity in Big-O notation.") is False


def test_static_question_does_not_trigger_web_search() -> None:
    assert live._needs_web_search(_final_prompt("What is the best food for an adult cat?")) is False
    assert live._question_requires_live("What is the best food for an adult cat?") is False


def test_independent_agent_prompt_does_not_pay_for_web_search() -> None:
    prompt = "You are one independent member of SiBot's AI Council. What is the weather in London now?"
    assert live._needs_web_search(prompt) is False


def test_live_openai_call_requires_current_hosted_web_search(monkeypatch) -> None:
    captured = {}

    def fake_http(url, *, headers, payload=None, method=None, timeout=90):
        captured["url"] = url
        captured["payload"] = payload
        return 200, {"output_text": "London is 21°C right now."}, "", {}

    monkeypatch.setattr(live._http, "_http_json", fake_http)
    rc, out, err = live._call_openai(
        _final_prompt("What is the current temperature in London?"),
        {"OPENAI_API_KEY": "test-key", "OPENAI_COUNCIL_MODEL": "gpt-test"},
    )

    assert rc == 0
    assert out == "London is 21°C right now."
    assert err == ""
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["payload"]["tools"] == [{"type": "web_search"}]
    assert captured["payload"]["tool_choice"] == "required"
    assert "MUST use live web search" in captured["payload"]["input"]


def test_tool_compatibility_error_retries_current_web_model(monkeypatch) -> None:
    models = []

    def fake_http(url, *, headers, payload=None, method=None, timeout=90):
        models.append(payload["model"])
        if len(models) == 1:
            return 400, {"error": {"message": "tool not supported"}}, "", {}
        return 200, {"output_text": "Fresh answer"}, "", {}

    monkeypatch.setattr(live._http, "_http_json", fake_http)
    rc, out, err = live._call_openai(
        _final_prompt("What is the latest news in London?"),
        {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_COUNCIL_MODEL": "custom-text-model",
            "OPENAI_WEB_MODEL": "gpt-5.6",
        },
    )
    assert (rc, out, err) == (0, "Fresh answer", "")
    assert models == ["custom-text-model", "gpt-5.6"]


def test_openai_429_falls_back_to_grounded_gemini(monkeypatch) -> None:
    calls = []

    def fake_http(url, *, headers, payload=None, method=None, timeout=90):
        calls.append((url, payload))
        if "api.openai.com" in url:
            return 429, {"error": {"message": "rate limit"}}, "", {}
        return 200, {
            "candidates": [{
                "content": {"parts": [{"text": "It is 18°C in London right now."}]},
                "groundingMetadata": {
                    "webSearchQueries": ["London current temperature"],
                    "groundingChunks": [{"web": {"uri": "https://example.com", "title": "Weather"}}],
                },
            }]
        }, "", {}

    monkeypatch.setattr(live._http, "_http_json", fake_http)
    rc, out, err = live._call_openai(
        _final_prompt("What is the current temperature in London?"),
        {
            "OPENAI_API_KEY": "openai-test",
            "GEMINI_API_KEY": "gemini-test",
            "GEMINI_COUNCIL_MODEL": "gemini-3.7-flash",
        },
    )
    assert (rc, out, err) == (0, "It is 18°C in London right now.", "")
    assert len(calls) == 2
    assert calls[1][1]["tools"] == [{"google_search": {}}]


def test_gemini_answer_without_grounding_metadata_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        live._http,
        "_http_json",
        lambda url, **kwargs: (
            (429, {"error": {"message": "rate limit"}}, "", {})
            if "api.openai.com" in url
            else (200, {"candidates": [{"content": {"parts": [{"text": "Probably 18°C."}]}}]}, "", {})
        ),
    )
    rc, out, err = live._call_openai(
        _final_prompt("What is the current temperature in London?"),
        {"OPENAI_API_KEY": "o", "GEMINI_API_KEY": "g"},
    )
    assert (rc, out, err) == (0, live.LIVE_UNAVAILABLE_TEXT, "")


def test_static_openai_call_delegates_without_web_search(monkeypatch) -> None:
    called = []

    monkeypatch.setattr(
        live,
        "_BASE_OPENAI",
        lambda prompt, env: called.append((prompt, env)) or (0, "static answer", ""),
    )
    result = live._call_openai(
        _final_prompt("What is the best food for a cat?"),
        {"OPENAI_API_KEY": "test-key"},
    )
    assert result == (0, "static answer", "")
    assert len(called) == 1


def test_failed_live_search_never_falls_back_to_offline_model(monkeypatch) -> None:
    monkeypatch.setattr(
        live._http,
        "_http_json",
        lambda *args, **kwargs: (503, {"error": {"message": "search unavailable"}}, "", {}),
    )
    base_calls = []
    monkeypatch.setattr(
        live,
        "_BASE_OPENAI",
        lambda prompt, env: base_calls.append(prompt) or (0, "I do not have access to live data.", ""),
    )
    rc, out, err = live._call_openai(
        _final_prompt("What is the latest news in London?"),
        {"OPENAI_API_KEY": "test-key", "GEMINI_API_KEY": "gemini-test"},
    )
    assert (rc, out, err) == (0, live.LIVE_UNAVAILABLE_TEXT, "")
    assert base_calls == []


def test_offline_refusal_is_detected_even_if_model_returns_it_with_success() -> None:
    text = (
        "I cannot provide the current real-time temperature for London because I do not "
        "have access to live weather feeds. Please check the Met Office."
    )
    assert live._looks_like_offline_refusal(text) is True
