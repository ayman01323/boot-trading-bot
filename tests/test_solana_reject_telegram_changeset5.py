from __future__ import annotations

from types import SimpleNamespace

from learnerbot import solana_leader_cursor_reliability_patch as mod


MINT = "Ay4WoENShiwYg4m4TSThVgiTknom4acE3XhJouQkagWx"
WALLET = "Fa9TFqUqxP111111111111111111111111FzXjXg7j"
SIG_A = "3NrQVYqRwz111111111111111111111111111111111111111111111111111111111111MZTFUbuc"
SIG_B = "64v1Wjx37n111111111111111111111111111111111111111111111111111111111111krw9B73f"
REASON = "LP_CONCENTRATION_RISK: RugCheck liquidity risk requires SHADOW/LIVE revalidation: Large Amount of LP Unlocked"


def _action(reason=REASON):
    return {"action": "REJECT", "reason": reason, "pool_risk_code": "LP_CONCENTRATION_RISK"}


def _event(signature=SIG_A, wallet=WALLET):
    return {"mint": MINT, "leader_wallet": wallet, "signature": signature}


def test_changeset5_stamp_and_suppression_window():
    assert mod._REJECT_REPORT_SUPPRESS_SECONDS == 900


def test_dedup_key_ignores_signal_signature():
    a = mod._telegram_reject_dedup_key("123", _event(SIG_A), _action())
    b = mod._telegram_reject_dedup_key("123", _event(SIG_B), _action())
    assert a == b


def test_dedup_key_changes_for_different_leader_or_reason():
    base = mod._telegram_reject_dedup_key("123", _event(), _action())
    other_leader = mod._telegram_reject_dedup_key("123", _event(wallet="DifferentLeader111"), _action())
    other_reason = mod._telegram_reject_dedup_key("123", _event(), _action("OTHER_RISK"))
    assert base != other_leader
    assert base != other_reason


def test_message_contains_full_clickable_identifiers_without_ellipsis():
    text = mod._reject_message(_event(), _action())
    assert MINT in text
    assert WALLET in text
    assert SIG_A in text
    assert f"https://solscan.io/token/{MINT}" in text
    assert f"https://www.dexview.com/solana/{MINT}" in text
    assert f"https://solscan.io/account/{WALLET}" in text
    assert f"https://solscan.io/tx/{SIG_A}" in text
    assert "…" not in text


def test_same_condition_different_signal_sends_once_within_15_minutes(monkeypatch):
    mod._REJECT_REPORT_DEDUP.clear()
    sent = []
    published = []
    clock = {"now": 1_000.0}

    monkeypatch.setattr(mod.time, "time", lambda: clock["now"])
    monkeypatch.setattr(mod, "_reject_targets", lambda app, event, action: ["123"])
    monkeypatch.setattr(mod, "_send_reject_report", lambda app, tid, event, action: sent.append(event["signature"]) or True)
    monkeypatch.setattr(mod, "publish_rejection", lambda **kwargs: published.append(kwargs))

    app = SimpleNamespace(csv_dir="unused", telegram_bot_token="token")
    mod._report_reject_actions(app, _event(SIG_A), [_action()])
    clock["now"] = 1_010.0
    mod._report_reject_actions(app, _event(SIG_B), [_action()])

    assert sent == [SIG_A]
    # Research publication stays event-level even while Telegram is deduplicated.
    assert len(published) == 2


def test_same_condition_can_alert_again_after_15_minutes(monkeypatch):
    mod._REJECT_REPORT_DEDUP.clear()
    sent = []
    clock = {"now": 2_000.0}

    monkeypatch.setattr(mod.time, "time", lambda: clock["now"])
    monkeypatch.setattr(mod, "_reject_targets", lambda app, event, action: ["123"])
    monkeypatch.setattr(mod, "_send_reject_report", lambda app, tid, event, action: sent.append(event["signature"]) or True)
    monkeypatch.setattr(mod, "publish_rejection", lambda **kwargs: None)

    app = SimpleNamespace(csv_dir="unused", telegram_bot_token="token")
    mod._report_reject_actions(app, _event(SIG_A), [_action()])
    clock["now"] = 2_901.0
    mod._report_reject_actions(app, _event(SIG_B), [_action()])

    assert sent == [SIG_A, SIG_B]
