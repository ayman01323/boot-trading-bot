from types import SimpleNamespace

from learnerbot import sibot
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


def test_provider_error_truth_separates_legacy_backlog_from_current_alchemy_errors(tmp_path):
    app = SimpleNamespace(data_dir=tmp_path / "data", csv_dir=tmp_path / "CSVbot")
    chain = SimpleNamespace(chain_id=8453, slug="base", type="EVM")
    _insert_status(
        app,
        8453,
        "0x" + "1" * 40,
        "RuntimeError: ETHERSCAN_API_KEY is not configured; SiBot cannot verify 60-day wallet histories",
    )
    _insert_status(
        app,
        8453,
        "0x" + "2" * 40,
        "AlchemyHistoryError: RuntimeError: Alchemy eth_getTransactionReceipt: HTTP 429; retries exhausted",
    )
    out = patch._provider_error_truth(app, chain)
    assert out["legacy"] == 1
    assert out["current"] == 1
    assert "Alchemy" in out["dominant"]
    assert "ETHERSCAN_API_KEY" not in out["dominant"]


def test_snapshot_reports_legacy_rows_as_backlog_not_active_provider_failure(monkeypatch, tmp_path):
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
        "RuntimeError: ETHERSCAN_API_KEY is not configured; SiBot cannot verify 60-day wallet histories",
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
