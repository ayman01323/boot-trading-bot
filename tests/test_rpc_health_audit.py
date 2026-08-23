from __future__ import annotations

from scripts import rpc_health_audit as audit


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def test_provider_label_never_returns_key_bearing_url() -> None:
    secret_url = "https://eth-mainnet.g.alchemy.com/v2/SUPER-SECRET-KEY"
    assert audit._provider_label(secret_url) == "Alchemy"
    assert "SECRET" not in audit._provider_label(secret_url)


def test_unknown_provider_is_redacted_to_custom() -> None:
    assert audit._provider_label("https://token-like-subdomain.example.invalid/private/path") == "Other/Custom"


def test_evm_http_probe_requires_expected_chain(monkeypatch) -> None:
    monkeypatch.setattr(
        audit.requests,
        "post",
        lambda *args, **kwargs: _Response({"jsonrpc": "2.0", "id": 1, "result": "0x89"}),
    )
    ok = audit._http_probe("https://example.invalid/key", 137, "EVM", 2.0)
    assert ok["ok"] is True
    assert ok["reported_chain_id"] == 137
    wrong = audit._http_probe("https://example.invalid/key", 1, "EVM", 2.0)
    assert wrong["ok"] is False
    assert wrong["error"] == "WRONG_CHAIN"


def test_solana_http_probe_checks_health(monkeypatch) -> None:
    monkeypatch.setattr(
        audit.requests,
        "post",
        lambda *args, **kwargs: _Response({"jsonrpc": "2.0", "id": 1, "result": "ok"}),
    )
    row = audit._http_probe("https://example.invalid/api-key", -101, "SOLANA", 2.0)
    assert row["ok"] is True
    assert row["reported_chain_id"] == "solana-mainnet"


def test_error_kind_never_echoes_exception_text() -> None:
    exc = RuntimeError("https://provider.invalid/SECRET?api-key=SECRET returned 429 Too Many Requests")
    assert audit._error_kind(exc) == "RATE_LIMIT"
    assert "SECRET" not in audit._error_kind(exc)
