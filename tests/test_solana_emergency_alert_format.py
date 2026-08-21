from learnerbot import telegram_solana_force_exit_patch as patch


def test_emergency_liquidity_notice_is_organised_and_keeps_safety_details():
    raw = (
        "🧯 <b>Solana emergency exit deferred — liquidity unsafe</b>\n"
        "Reason: <code>SOLANA_STOP_LOSS</code>\n"
        "Position: <code>p1</code>\n"
        "Hard impact+slippage ceiling: <b>5.00%</b>\n"
        "Tried: <b>100%, 75%, 50% and 25%</b> of the remaining position.\n"
        "No transaction was broadcast. Jupiter still priced every safe slice above the emergency ceiling.\n"
        "Last guard: <code>Economic execution guard: quoted price impact 10000.00 bps + slippage 50 bps = 10050.00 bps exceeds 500 bps</code>\n"
        "Automatic retry: <b>900s</b> (liquidity attempt 11).\n"
        "A 100% price-impact quote is not bypassed because that could realise essentially all remaining swap value as loss."
    )

    out = patch._format_emergency_liquidity_notice(raw)

    assert "SOLANA EMERGENCY EXIT DEFERRED" in out
    assert "Status:</b> Liquidity unsafe" in out
    assert "<b>Position</b>" in out
    assert "<b>Safety checks</b>" in out
    assert "100% → 75% → 50% → 25%" in out
    assert "Transaction broadcast: <b>NO</b>" in out
    assert "<b>Liquidity result</b>" in out
    assert "10000.00 bps" in out
    assert "Automatic retry: <b>15 min (900s)</b>" in out
    assert "Liquidity attempt: <b>11</b>" in out
    assert "near-100% price-impact quote is never bypassed automatically" in out


def test_non_emergency_notice_is_unchanged():
    text = "ordinary notification"
    assert patch._format_emergency_liquidity_notice(text) == text
