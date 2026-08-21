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


def test_static_question_does_not_trigger_web_search() -> None:
    assert live._needs_web_search(_final_prompt("What is the best food for an adult cat?")) is False


def test_independent_agent_prompt_does_not_pay_for_web_search() -> None:
    prompt = "You are one independent member of SiBot's AI Council. What is the weather in London now?"
    assert live._needs_web_search(prompt) is False


def test_live_openai_call_requires_hosted_web_search(monkeypatch) -> None:
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
    assert captured["payload"]["tools"] == [
        {"type": "web_search_preview", "search_context_size": "medium"}
    ]
    assert captured["payload"]["tool_choice"] == "required"
    assert "Do not tell the user that you lack internet access" in captured["payload"]["input"]


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


def test_failed_live_search_falls_back_honestly(monkeypatch) -> None:
    monkeypatch.setattr(
        live._http,
        "_http_json",
        lambda *args, **kwargs: (503, {"error": {"message": "search unavailable"}}, "", {}),
    )
    captured = []
    monkeypatch.setattr(
        live,
        "_BASE_OPENAI",
        lambda prompt, env: captured.append(prompt) or (0, "Live information could not be retrieved at this moment.", ""),
    )
    rc, out, err = live._call_openai(
        _final_prompt("What is the latest news in London?"),
        {"OPENAI_API_KEY": "test-key"},
    )
    assert rc == 0
    assert "could not be retrieved" in out
    assert err == ""
    assert "do not claim PasPuss AI never has live-data capability" in captured[0]
