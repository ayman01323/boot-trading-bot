from types import SimpleNamespace

import pytest

from learnerbot import sibot
from learnerbot import sibot_alchemy_trace_progress_patch as trace_progress
from learnerbot import trade_blocker_alchemy_history_patch as patch


def _insert_status(app, chain_id, wallet, error):
    with sibot.connect(app) as conn:
        conn.execute(
            """INSERT INTO wallet_history_status(
                   chain_id,chain_slug,wallet,fetched_at,history_complete,error
               ) VALUES(?,?,?,?,0,?)
               ON CONFLICT(chain_id,wallet) DO UPDATE SET error=excluded.error""",
            (chain_id, "base", wallet, 1, error),
        )
        conn.commit()


def test_provider_error_truth_separates_all_etherscan_backlog_from_current_alchemy_errors(tmp_path):
    app = SimpleNamespace(data_dir=tmp_path / "data", csv_dir=tmp_path / "CSVbot")
    chain = SimpleNamespace(chain_id=8453, slug="base", type="EVM")
    legacy_errors = [
        "RuntimeError: ETHERSCAN_API_KEY is not configured; SiBot cannot verify 60-day wallet histories",
        "RuntimeError: Etherscan txlist: NOTOK Invalid API Key (#err2)",
        "RuntimeError: Etherscan txlist: NOTOK Free API access is not supported for this chain. Please upgrade your api plan",
    ]
    for idx, error in enumerate(legacy_errors, 1):
        _insert_status(app, 8453, "0x" + str(idx) * 40, error)
    _insert_status(
        app,
        8453,
        "0x" + "9" * 40,
        "AlchemyHistoryError: RuntimeError: Alchemy eth_getTransactionReceipt: HTTP 429; retries exhausted",
    )
    out = patch._provider_error_truth(app, chain)
    assert out["legacy"] == 3
    assert out["current"] == 1
    assert "Alchemy" in out["dominant"]
    assert "Etherscan" not in out["dominant"]


def test_snapshot_reports_invalid_key_etherscan_row_as_backlog_not_active_provider_failure(monkeypatch, tmp_path):
    app = SimpleNamespace(
        data_dir=tmp_path / "data",
        csv_dir=tmp_path / "CSVbot",
        etherscan_api_key="",
    )
    chain = SimpleNamespace(chain_id=8453, slug="base", type="EVM")
    _insert_status(
        app,
        8453,
        "0x" + "3" * 40,
        "RuntimeError: Etherscan txlist: NOTOK Invalid API Key (#err2)",
    )
    monkeypatch.setattr(
        patch,
        "_PREV_SNAPSHOT",
        lambda app, tid: {"evm": {"base": {"errors": 1, "dominant": "old"}}},
    )
    monkeypatch.setattr(patch, "_providers", lambda app: {"base": "ALCHEMY"})
    monkeypatch.setattr(patch._health, "load_chains", lambda app, enabled_only=True: [chain])
    out = patch._snapshot(app, "1")
    row = out["evm"]["base"]
    assert row["errors"] == 0
    assert row["current_provider_errors"] == 0
    assert row["legacy_errors"] == 1
    assert "legacy Etherscan history backlog" in row["dominant"]
    assert "queued for Alchemy refresh" in row["dominant"]


def test_runtime_invariant_pins_final_refresh_to_alchemy_trace_progress(monkeypatch):
    assert sibot.refresh_wallet_history is trace_progress.refresh_wallet_history
    patch._assert_alchemy_runtime()
    monkeypatch.setattr(sibot, "refresh_wallet_history", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="final refresh is not the Alchemy"):
        patch._assert_alchemy_runtime()
