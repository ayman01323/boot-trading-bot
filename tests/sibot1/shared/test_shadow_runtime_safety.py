from __future__ import annotations

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from sibot1_engines._shared.contracts import TradeIntent
from sibot1_engines._shared.market_data import MarketEvidenceBook
from sibot1_engines._shared.paper_execution import MarketPriceBook, ShadowPaperExecutor
from sibot1_engines._shared.poolcheck_bridge import MandatoryShadowPoolCheck
from sibot1_engines._shared.runtime import SiBot1ShadowRuntime


def test_paper_executor_rejects_live_mode():
    with pytest.raises(RuntimeError):
        ShadowPaperExecutor(MarketPriceBook(), mode="LIVE")


def test_runtime_rejects_live_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("SIBOT1_EXECUTION_MODE", "LIVE")
    with pytest.raises(RuntimeError):
        SiBot1ShadowRuntime(tmp_path)


def test_poolcheck_never_grants_live_pass_from_evm_source_flags(tmp_path):
    book = MarketEvidenceBook()
    book.put(
        "evt",
        {
            "src_exact_quote_ok": True,
            "src_simulation_ok": True,
            "src_liquidity_ok": True,
            "src_sellability_ok": True,
            "src_whole_route_approved": True,
        },
    )
    port = MandatoryShadowPoolCheck(tmp_path, book)
    intent = TradeIntent(
        intent_id="i",
        engine_id="gpt",
        engine_version="1",
        strategy_id="x",
        chain="base",
        side="ARBITRAGE",
        asset_in="A",
        asset_out="B",
        requested_input_amount=Decimal("1"),
        created_at_ms=1,
        market_event_id="evt",
    )
    decision = port.assess_entry(intent)
    assert decision.verdict == "SHADOW_ONLY"
    assert decision.passed is False
    assert decision.evidence["live_eligible"] is False


def test_executor_surface_has_no_signer_or_broadcast():
    names = {name.lower() for name in dir(ShadowPaperExecutor)}
    forbidden = {"sign", "broadcast", "send_raw_transaction", "private_key"}
    assert forbidden.isdisjoint(names)


def test_standalone_runtime_script_imports_from_any_cwd(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    script = repo / "scripts" / "sibot1_shadow_runtime.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
