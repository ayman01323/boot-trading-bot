from decimal import Decimal
from types import SimpleNamespace

import pytest

from learnerbot import telegram_solana_send_patch as p


def test_solana_wallet_keyboard_adds_send_sol_and_usd(monkeypatch):
    monkeypatch.setattr(p, "_PREV_SOL_KEYBOARD", lambda app, tid: {"inline_keyboard": [[{"text": "base", "callback_data": "solwallet:add"}]]})
    kb = p.solwallet_keyboard(None, "1")
    texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
    callbacks = [b.get("callback_data") for row in kb["inline_keyboard"] for b in row]
    assert "💸 Send SOL" in texts
    assert "💵 Send USD" in texts
    assert "solsend:start:sol" in callbacks
    assert "solsend:start:usd" in callbacks


def test_usd_preview_converts_to_fixed_sol_and_does_not_broadcast(monkeypatch):
    sent = []
    fake_meta = {"wallet_id": "s123", "address": "sender"}
    monkeypatch.setattr(p, "_require_manual_transfer", lambda app, tid, chat_type=None: ({"can_transfer": "true"}, fake_meta))
    monkeypatch.setattr(p, "validate_solana_address", lambda address: address)
    monkeypatch.setattr(p, "_sol_usd_price", lambda: Decimal("100"))
    monkeypatch.setattr(p, "prepare_native_transfer", lambda app, tid, destination, lamports: {
        "sender": "Sender111111111111111111111111111111111111",
        "destination": destination,
        "lamports": lamports,
        "amount_sol": Decimal(lamports) / Decimal(1_000_000_000),
        "balance_sol": Decimal("0.08563"),
        "reserve_sol": Decimal("0.02"),
    })
    monkeypatch.setattr(p, "broadcast_native_transfer", lambda *args, **kwargs: pytest.fail("broadcast must not run during preview"))
    monkeypatch.setattr(p._ui, "_send", lambda app, tid, text, keyboard=None: sent.append((text, keyboard)))
    monkeypatch.setattr(p, "_audit", lambda *args, **kwargs: None)

    entry = p._make_preview(None, "1", "USD", "2", "Receiver111111111111111111111111111111111", "private")
    assert entry["lamports"] == 20_000_000
    assert entry["amount_sol"] == Decimal("0.02")
    assert sent
    assert "Requested: <b>$2.00</b>" in sent[-1][0]
    assert "Will send exactly: <b>0.020000000 SOL</b>" in sent[-1][0]
    assert "CONFIRM TRANSFER" in str(sent[-1][1])


def test_manual_transfer_blocked_while_solana_live(monkeypatch):
    monkeypatch.setattr(p._ui, "require_user", lambda csv_dir, tid, active=True: {"can_transfer": "true"})
    monkeypatch.setattr(p, "live_enabled", lambda app, tid: True)
    app = SimpleNamespace(csv_dir="/tmp", data_dir="/tmp")
    with pytest.raises(Exception, match="Disable Solana LIVE"):
        p._require_manual_transfer(app, "1", "private")


def test_confirm_callback_broadcasts_only_after_explicit_confirm(monkeypatch):
    sent = []
    answers = []
    called = []
    tid = "1"
    token = "abc123"
    p._PENDING_CONFIRM.clear()
    p._PENDING_CONFIRM[(tid, token)] = {
        "mode": "SOL",
        "requested": Decimal("0.01"),
        "destination": "Receiver111111111111111111111111111111111",
        "lamports": 10_000_000,
        "amount_sol": Decimal("0.01"),
        "expires": p.time.time() + 60,
    }
    monkeypatch.setattr(p._ui, "_auth", lambda app, x: True)
    monkeypatch.setattr(p, "_require_manual_transfer", lambda app, x, chat_type=None: ({"can_transfer": "true"}, {"wallet_id": "s1"}))
    monkeypatch.setattr(p, "broadcast_native_transfer", lambda app, x, dest, lamports: called.append((x, dest, lamports)) or {"signature": "sig123", "status": "CONFIRMED"})
    monkeypatch.setattr(p, "_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(p._sol, "settings", lambda app: {"explorer_url": "https://solscan.io"})
    monkeypatch.setattr(p._ui, "_send", lambda app, x, text, keyboard=None: sent.append((text, keyboard)))
    monkeypatch.setattr(p._tg, "answer_callback_query", lambda token, qid, text="": answers.append(text))
    app = SimpleNamespace(telegram_bot_token="t")
    cb = {
        "id": "q1",
        "data": f"solsend:confirm:{token}",
        "message": {"chat": {"id": int(tid), "type": "private"}},
    }
    assert p._handle_callback(app, cb) is True
    assert called == [(tid, "Receiver111111111111111111111111111111111", 10_000_000)]
    assert "Solana transfer submitted" in sent[-1][0]
    assert (tid, token) not in p._PENDING_CONFIRM


def test_cancel_never_broadcasts(monkeypatch):
    tid = "2"
    token = "cancel1"
    p._PENDING_CONFIRM.clear()
    p._PENDING_CONFIRM[(tid, token)] = {"expires": p.time.time() + 60}
    monkeypatch.setattr(p._ui, "_auth", lambda app, x: True)
    monkeypatch.setattr(p._ui, "_send", lambda *args, **kwargs: None)
    monkeypatch.setattr(p._tg, "answer_callback_query", lambda *args, **kwargs: None)
    monkeypatch.setattr(p, "broadcast_native_transfer", lambda *args, **kwargs: pytest.fail("cancel must never broadcast"))
    app = SimpleNamespace(telegram_bot_token="t")
    cb = {
        "id": "q2",
        "data": f"solsend:cancel:{token}",
        "message": {"chat": {"id": int(tid), "type": "private"}},
    }
    assert p._handle_callback(app, cb) is True
    assert (tid, token) not in p._PENDING_CONFIRM
