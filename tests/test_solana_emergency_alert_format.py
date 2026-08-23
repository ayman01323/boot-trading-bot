from learnerbot import telegram_solana_force_exit_patch as patch


def _base_notice(guard: str) -> str:
    return (
        "🧯 <b>Solana emergency exit deferred — liquidity unsafe</b>\n"
        "Reason: <code>SOLANA_STOP_LOSS</code>\n"
        "Position: <code>p1</code>\n"
        "Hard impact+slippage ceiling: <b>5.00%</b>\n"
        "Tried: <b>100%, 75%, 50% and 25%</b> of the remaining position.\n"
        "No transaction was broadcast. Jupiter still priced every safe slice above the emergency ceiling.\n"
        f"Last guard: <code>{guard}</code>\n"
        "Automatic retry: <b>900s</b> (liquidity attempt 11).\n"
        "A 100% price-impact quote is not bypassed because that could realise essentially all remaining swap value as loss."
    )


def test_emergency_impact_notice_is_truthful_and_uses_live_slice_ladder():
    raw = _base_notice(
        "Economic execution guard: quoted price impact 10000.00 bps + slippage 50 bps = 10050.00 bps exceeds 500 bps"
    )

    out = patch._format_emergency_liquidity_notice(raw)

    assert "SOLANA EMERGENCY EXIT DEFERRED" in out
    assert "Status:</b> Liquidity unsafe" in out
    assert "<b>Position</b>" in out
    assert "<b>Safety checks</b>" in out
    assert "100% → 75% → 50% → 25% → 10% → 5% → 2% → 1%" in out
    assert "Transaction broadcast: <b>NO</b>" in out
    assert "<b>Liquidity result</b>" in out
    assert "Jupiter returned an executable route" in out
    assert "10000.00 bps" in out
    assert "Automatic retry: <b>15 min (900s)</b>" in out
    assert "Liquidity attempt: <b>11</b>" in out
    assert "near-100% price-impact quote is never bypassed automatically" in out


def test_jupiter_no_quote_notice_is_not_misreported_as_high_price_impact():
    raw = _base_notice(
        'Jupiter quote HTTP 400: {"requestId":"req-1","error":"Failed to get quotes"}'
    )

    out = patch._format_emergency_liquidity_notice(raw)

    assert "Status:</b> Quote unavailable" in out
    assert "Jupiter returned no executable quote" in out
    assert "unavailable route" in out
    assert "100% → 75% → 50% → 25% → 10% → 5% → 2% → 1%" in out
    assert "priced every tested slice above the emergency ceiling" not in out
    assert "Failed to get quotes" in out


def test_dust_output_has_distinct_economic_status():
    raw = _base_notice(
        "Economic execution guard: net proceeds after fees 900 lamports below emergency minimum 10000 lamports"
    )

    out = patch._format_emergency_liquidity_notice(raw)

    assert "Status:</b> Economically unsafe" in out
    assert "net proceeds after fees fell below the emergency minimum" in out


def test_non_emergency_notice_is_unchanged():
    text = "ordinary notification"
    assert patch._format_emergency_liquidity_notice(text) == text
