from types import SimpleNamespace

from learnerbot import solana_position_liquidity_health_patch as patch


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _app():
    return SimpleNamespace(csv_dir="csv", data_dir="data")


def _position(position_id="p1", token_amount_raw=1_000_000):
    return {
        "position_id": position_id,
        "telegram_id": "123",
        "mint": "mint-abc",
        "status": "OPEN",
        "mode": "LIVE",
        "token_amount_raw": token_amount_raw,
    }


def _cfg():
    return {
        "live_liquidity_health_check_seconds": "900",
        "live_liquidity_warning_combined_bps": "150",
        "live_liquidity_warning_repeat_hours": "4",
        "live_order_slippage_bps": "50",
    }


def _wire(monkeypatch, positions=None, quote=None, quote_raises=None,
          last_check=0, last_alert=0):
    state = {}

    def state_get(conn, key, default=None):
        return state.get(key, default)

    def state_set(conn, key, value):
        state[key] = value

    monkeypatch.setattr(patch._sol, "settings", lambda app: dict(_cfg()))
    monkeypatch.setattr(patch, "_open_live_positions", lambda app: positions or [])
    monkeypatch.setattr(patch._sol, "connect", lambda app: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(patch._sol, "_state", state_get)
    monkeypatch.setattr(patch._sol, "_set_state", state_set)
    monkeypatch.setattr(patch._sol, "_DB_LOCK", _NullLock())

    if last_check:
        state[f"liquidity_health_last_check:{positions[0]['position_id']}"] = str(last_check)
    if last_alert:
        state[f"liquidity_health_last_alert:{positions[0]['position_id']}"] = str(last_alert)

    notified = []
    monkeypatch.setattr(patch._live, "_notify", lambda app, tid, text: notified.append((tid, text)))

    if quote_raises is not None:
        def raiser(*a, **k):
            raise quote_raises
        monkeypatch.setattr(patch, "_quote_only", raiser)
    else:
        monkeypatch.setattr(patch, "_quote_only", lambda *a, **k: quote or {})

    return notified, state


def test_combined_impact_slippage_bps_from_price_impact_field():
    combined = patch._combined_impact_slippage_bps({"priceImpact": 4.5}, 50)
    assert combined == patch.Decimal("500")  # 450 bps + 50 bps slippage


def test_combined_impact_slippage_bps_from_price_impact_pct_field():
    combined = patch._combined_impact_slippage_bps({"priceImpactPct": 0.02}, 50)
    assert combined == patch.Decimal("250")  # 200 bps + 50 bps slippage


def test_alerts_when_quote_crosses_warning_threshold(monkeypatch):
    pos = _position()
    notified, state = _wire(monkeypatch, positions=[pos], quote={"priceImpact": 4.0})  # 400 bps > 150 bps warning
    patch.check_open_position_liquidity(_app())
    assert len(notified) == 1
    tid, text = notified[0]
    assert tid == "123"
    assert "p1" in text
    assert "liquidity" in text.lower()
    assert state[f"liquidity_health_last_check:{pos['position_id']}"]
    assert state[f"liquidity_health_last_alert:{pos['position_id']}"]


def test_no_alert_when_quote_is_healthy(monkeypatch):
    pos = _position()
    notified, state = _wire(monkeypatch, positions=[pos], quote={"priceImpact": 0.5})  # 50 bps + 50 bps = 100 bps < 150
    patch.check_open_position_liquidity(_app())
    assert notified == []
    assert state[f"liquidity_health_last_check:{pos['position_id']}"]


def test_skips_position_not_due_for_check(monkeypatch):
    import time
    pos = _position()
    calls = []
    notified, state = _wire(monkeypatch, positions=[pos], last_check=int(time.time()))

    def tracked_quote(*a, **k):
        calls.append(1)
        return {"priceImpact": 4.0}
    monkeypatch.setattr(patch, "_quote_only", tracked_quote)

    patch.check_open_position_liquidity(_app())
    assert calls == []
    assert notified == []


def test_does_not_repeat_alert_within_cooldown(monkeypatch):
    import time
    pos = _position()
    notified, state = _wire(
        monkeypatch, positions=[pos], quote={"priceImpact": 4.0}, last_alert=int(time.time()),
    )
    patch.check_open_position_liquidity(_app())
    assert notified == []
    # Cadence still advances even though we didn't re-alert.
    assert state[f"liquidity_health_last_check:{pos['position_id']}"]


def test_quote_failure_is_skipped_silently(monkeypatch):
    pos = _position()
    notified, state = _wire(monkeypatch, positions=[pos], quote_raises=RuntimeError("jupiter down"))
    patch.check_open_position_liquidity(_app())  # must not raise
    assert notified == []
    assert state[f"liquidity_health_last_check:{pos['position_id']}"]


def test_position_missing_mint_or_amount_is_skipped(monkeypatch):
    pos = _position(token_amount_raw=0)
    calls = []
    notified, state = _wire(monkeypatch, positions=[pos])

    def tracked_quote(*a, **k):
        calls.append(1)
        return {"priceImpact": 4.0}
    monkeypatch.setattr(patch, "_quote_only", tracked_quote)

    patch.check_open_position_liquidity(_app())
    assert calls == []
    assert notified == []


def test_wrapper_calls_previous_monitor_positions_and_never_raises(monkeypatch):
    calls = []
    monkeypatch.setattr(patch, "_PREV_MONITOR_POSITIONS", lambda app: calls.append("prev") or "prev-result")

    def boom(app):
        raise RuntimeError("health check exploded")
    monkeypatch.setattr(patch, "check_open_position_liquidity", boom)

    result = patch.monitor_positions_with_liquidity_health(_app())
    assert calls == ["prev"]
    assert result == "prev-result"
