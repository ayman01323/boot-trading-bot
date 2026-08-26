from __future__ import annotations

import json
import subprocess
import sys


def _run_loader(kind: str, path) -> dict:
    code = r'''
import json, sys
from sibot1_engines._shared import deep_nomination_relaxation_patch as patch
s = patch._gemini_load(sys.argv[2]) if sys.argv[1] == "gemini" else patch._grok_load(sys.argv[2])
print(json.dumps({
    "min_liquidity_usd": str(getattr(s, "min_liquidity_usd", "")),
    "min_volume_usd": str(getattr(s, "min_volume_usd", "")),
    "min_volume_liquidity_ratio": str(getattr(s, "min_volume_liquidity_ratio", "")),
    "max_volume_liquidity_ratio": str(getattr(s, "max_volume_liquidity_ratio", "")),
    "min_confidence": str(getattr(s, "min_confidence", "")),
    "min_volume_velocity": str(getattr(s, "min_volume_velocity", "")),
    "reject_dev_selling": bool(getattr(s, "reject_dev_selling", False)),
    "max_source_age_ms": int(getattr(s, "max_source_age_ms", 0)),
}))
'''
    proc = subprocess.run(
        [sys.executable, "-c", code, kind, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_gemini_nomination_caps_strict_source_settings(tmp_path):
    p = tmp_path / "gemini.csv"
    p.write_text(
        "engine_id,engine_version,strategy_id,chain,trade_amount,min_liquidity_usd,min_volume_usd,min_volume_liquidity_ratio,max_volume_liquidity_ratio,min_liquidity_velocity_pct,min_lp_locked_pct_prefilter,signal_cooldown_ms,take_profit_pct,max_source_age_ms\n"
        "gemini,1.1.0,Gemini-PulseFlow,solana,1.5,5000,200,0.02,10,-35,25,300000,0.05,750\n",
        encoding="utf-8",
    )
    s = _run_loader("gemini", p)
    assert s["min_liquidity_usd"] == "3000"
    assert s["min_volume_usd"] == "100"
    assert s["min_volume_liquidity_ratio"] == "0.01"
    assert s["max_volume_liquidity_ratio"] == "10"
    assert s["max_source_age_ms"] == 750


def test_gemini_does_not_raise_already_more_flexible_values(tmp_path):
    p = tmp_path / "gemini.csv"
    p.write_text(
        "engine_id,engine_version,strategy_id,chain,trade_amount,min_liquidity_usd,min_volume_usd,min_volume_liquidity_ratio,max_volume_liquidity_ratio,min_liquidity_velocity_pct,min_lp_locked_pct_prefilter,signal_cooldown_ms,take_profit_pct,max_source_age_ms\n"
        "gemini,1.1.0,Gemini-PulseFlow,solana,1.5,2000,80,0.005,10,-35,25,300000,0.05,750\n",
        encoding="utf-8",
    )
    s = _run_loader("gemini", p)
    assert s["min_liquidity_usd"] == "2000"
    assert s["min_volume_usd"] == "80"
    assert s["min_volume_liquidity_ratio"] == "0.005"


def test_grok_relaxes_nomination_but_keeps_dev_unknown_fail_closed(tmp_path):
    p = tmp_path / "grok.csv"
    p.write_text(
        "chain,strategy_id,trade_amount,min_confidence,min_volume_velocity,take_profit_pct,stop_loss_pct,reject_dev_selling,max_source_age_ms\n"
        "solana,CompactFlow-v1,1,0.55,0.02,0.035,-0.018,true,750\n",
        encoding="utf-8",
    )
    s = _run_loader("grok", p)
    assert s["min_confidence"] == "0.52"
    assert s["min_volume_velocity"] == "0.005"
    assert s["reject_dev_selling"] is True
    assert s["max_source_age_ms"] == 750
