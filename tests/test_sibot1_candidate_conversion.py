from decimal import Decimal
from pathlib import Path

from sibot1_engines._shared.contracts import MarketEvent
from sibot1_engines._shared.market_data import EvmOpportunityCsvSource, MarketEvidenceBook
from sibot1_engines.gemini.settings_schema import load_settings as load_gemini
from sibot1_engines.grok.settings_schema import load_settings as load_grok
from sibot1_engines.grok.strategy import CompactFlowStrategy


ROOT = Path(__file__).resolve().parents[1]


def test_evm_source_reads_full_power_fast_market_feeds(tmp_path):
    source = EvmOpportunityCsvSource(tmp_path, MarketEvidenceBook())
    assert tmp_path / "auto" / "base_full_power_opportunities.csv" in source.paths
    assert tmp_path / "auto" / "full_power_opportunities.csv" in source.paths
    # Dedicated Base feed must be checked before the combined full-power feed.
    assert source.paths.index(tmp_path / "auto" / "base_full_power_opportunities.csv") < source.paths.index(
        tmp_path / "auto" / "full_power_opportunities.csv"
    )


def test_relaxed_nomination_settings_are_bounded():
    gemini = load_gemini(ROOT / "CSVbot" / "sibot1" / "engines" / "gemini" / "settings.example.csv")
    grok = load_grok(ROOT / "CSVbot" / "sibot1" / "engines" / "grok" / "settings.example.csv")

    assert gemini.min_liquidity_usd == Decimal("5000")
    assert gemini.min_volume_usd == Decimal("200")
    assert gemini.min_volume_liquidity_ratio == Decimal("0.02")
    assert gemini.signal_cooldown_ms == 300000

    assert grok.min_confidence == Decimal("0.55")
    assert grok.min_volume_velocity == Decimal("0.02")
    # Developer selling evidence stays fail-closed even with broader nomination.
    assert grok.reject_dev_selling is True


def test_grok_reports_fail_closed_developer_evidence_rejection():
    strategy = CompactFlowStrategy(load_grok(ROOT / "CSVbot" / "sibot1" / "engines" / "grok" / "settings.example.csv"))
    event = MarketEvent(
        event_id="sol-test",
        chain="solana",
        observed_at_ms=1,
        source="test",
        event_type="market_pulse",
        asset_in="SOL",
        asset_out="MINT",
        price=Decimal("1"),
        source_age_ms=0,
        payload={"dev_selling_known": False, "volume_velocity": "1", "confidence": "0.9"},
    )
    assert strategy.entry_signal(event) is None
    assert strategy.rejection_counts()["developer_flow_unknown"] == 1
