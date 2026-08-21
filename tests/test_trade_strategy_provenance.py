from __future__ import annotations

import csv
import sqlite3
import time
from pathlib import Path

import pytest

from learnerbot import auto_trader
from learnerbot import sibot
from learnerbot import solana_sibot
from learnerbot import trade_strategy_provenance_patch as provenance


class App:
    def __init__(self, root: Path):
        self.data_dir = root / "data"
        self.csv_dir = root / "csv"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.csv_dir.mkdir(parents=True, exist_ok=True)


def _insert_evm(conn, position_id: str, **extra):
    values = {
        "position_id": position_id,
        "telegram_id": "123",
        "wallet_address": "0x0000000000000000000000000000000000000001",
        "chain_id": 8453,
        "chain_slug": "base",
        "primary_leader": "0x0000000000000000000000000000000000000002",
        "token": "0x0000000000000000000000000000000000000003",
        "mode": "LIVE",
        "status": "OPEN",
        "token_amount_raw": "100",
        "entry_input_native": "0.01",
        "entry_cost_native": "0.01",
        "entry_ts": int(time.time()),
        "updated_at": int(time.time()),
    }
    values.update(extra)
    cols = ",".join(values)
    marks = ",".join("?" for _ in values)
    conn.execute(f"INSERT INTO positions({cols}) VALUES({marks})", tuple(values.values()))
    conn.commit()


def _insert_sol(conn, position_id: str, **extra):
    values = {
        "position_id": position_id,
        "telegram_id": "123",
        "leader_wallet": "leader",
        "mint": "mint",
        "mode": "LIVE",
        "status": "OPEN",
        "token_amount_raw": "100",
        "entry_cost_sol": "0.01",
        "entry_ts": int(time.time()),
        "updated_at": int(time.time()),
    }
    values.update(extra)
    cols = ",".join(values)
    marks = ",".join("?" for _ in values)
    conn.execute(f"INSERT INTO positions({cols}) VALUES({marks})", tuple(values.values()))
    conn.commit()


def _auto_row(now: int, **extra) -> dict:
    row = {
        "timestamp_epoch": now,
        "telegram_id": "123",
        "wallet_id": "evm-1",
        "chain_id": "137",
        "chain_slug": "polygon",
        "route_id": "route-1",
        "route_path": "A>B>A",
        "input_base": "1",
        "expected_gross_base": "0.05",
        "expected_gas_base": "0.01",
        "expected_net_base": "0.04",
        "realised_net_base": "0.03",
        "profit_fee_base": "0.005",
        "fee_tx_hash": "fee",
        "tx_hash": "tx",
        "status": "SUCCESS",
        "note": "test",
    }
    row.update(extra)
    return row


def test_new_evm_and_solana_positions_are_stamped_and_immutable(tmp_path):
    app = App(tmp_path)

    with sibot.connect(app) as conn:
        _insert_evm(conn, "evm-new")
        row = conn.execute(
            "SELECT strategy_engine,strategy_version,git_sha FROM positions WHERE position_id='evm-new'"
        ).fetchone()
        assert row["strategy_engine"] == provenance.EVM_ENGINE
        assert row["strategy_version"] == provenance.STRATEGY_VERSION
        assert row["git_sha"] == provenance.GIT_SHA
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE positions SET git_sha=? WHERE position_id='evm-new'", ("0" * 40,))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE positions SET strategy_engine=? WHERE position_id='evm-new'", ("OTHER",))

    with solana_sibot.connect(app) as conn:
        _insert_sol(conn, "sol-new")
        row = conn.execute(
            "SELECT strategy_engine,strategy_version,git_sha FROM positions WHERE position_id='sol-new'"
        ).fetchone()
        assert row["strategy_engine"] == provenance.SOLANA_ENGINE
        assert row["strategy_version"] == provenance.STRATEGY_VERSION
        assert row["git_sha"] == provenance.GIT_SHA
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE positions SET strategy_version=? WHERE position_id='sol-new'", ("v999",))


def test_existing_positions_are_marked_legacy_not_backfilled_with_current_sha(tmp_path):
    app = App(tmp_path)

    # Use the captured pre-patch connector to simulate a position created before deployment.
    with provenance._ORIG_EVM_CONNECT(app) as conn:
        _insert_evm(conn, "old-position")

    with sibot.connect(app) as conn:
        row = conn.execute(
            "SELECT strategy_engine,strategy_version,git_sha FROM positions WHERE position_id='old-position'"
        ).fetchone()
        assert row["strategy_engine"] == provenance.EVM_ENGINE
        assert row["strategy_version"] == provenance.LEGACY_VALUE
        assert row["git_sha"] == provenance.LEGACY_VALUE


def test_auto_execution_and_simulation_logs_are_stamped_and_old_rows_stay_legacy(tmp_path):
    app = App(tmp_path)
    now = int(time.time())
    execution = app.csv_dir / "auto" / "auto_trade_execution.csv"
    execution.parent.mkdir(parents=True, exist_ok=True)

    # Simulate a pre-feature record with the historical header.
    old_headers = [
        "timestamp_epoch", "telegram_id", "wallet_id", "chain_id", "chain_slug",
        "route_id", "route_path", "input_base", "expected_gross_base", "expected_gas_base",
        "expected_net_base", "realised_net_base", "profit_fee_base", "fee_tx_hash", "tx_hash",
        "status", "note",
    ]
    with execution.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=old_headers)
        writer.writeheader()
        writer.writerow({h: _auto_row(now - 100).get(h, "") for h in old_headers})

    auto_trader._append(execution, _auto_row(now, tx_hash="tx-new"))
    rows = auto_trader._rows(execution)
    assert len(rows) == 2
    assert rows[0]["strategy_engine"] == provenance.AUTO_EVM_ENGINE
    assert rows[0]["strategy_version"] == provenance.LEGACY_VALUE
    assert rows[0]["git_sha"] == provenance.LEGACY_VALUE
    assert rows[1]["strategy_engine"] == provenance.AUTO_EVM_ENGINE
    assert rows[1]["strategy_version"] == provenance.STRATEGY_VERSION
    assert rows[1]["git_sha"] == provenance.GIT_SHA

    auto_trader._append_simulation(app.csv_dir, {
        "timestamp_epoch": now,
        "telegram_id": "123",
        "wallet_id": "evm-1",
        "chain_id": "137",
        "chain_slug": "polygon",
        "route_id": "route-1",
        "route_path": "A>B>A",
        "input_base": "1",
        "min_net_profit_base": "0.01",
        "gross_profit_base": "0.05",
        "gas_cost_base": "0.01",
        "simulation_ok": "true",
        "reason": "ok",
    })
    sim = auto_trader._rows(app.csv_dir / "auto" / "auto_trade_simulations.csv")[0]
    assert sim["strategy_engine"] == provenance.AUTO_EVM_ENGINE
    assert sim["strategy_version"] == provenance.STRATEGY_VERSION
    assert sim["git_sha"] == provenance.GIT_SHA


def test_24h_attribution_never_merges_engines_versions_or_live_shadow(tmp_path):
    app = App(tmp_path)
    now = int(time.time())
    sha_a = "a" * 40
    sha_b = "b" * 40

    with sibot.connect(app) as conn:
        _insert_evm(
            conn,
            "a-live-win",
            status="CLOSED",
            closed_at=now - 100,
            realised_user_net_native="0.20",
            realised_net_native="0.20",
            strategy_version="vA",
            git_sha=sha_a,
        )
        _insert_evm(
            conn,
            "b-live-loss",
            status="CLOSED",
            closed_at=now - 90,
            realised_user_net_native="-0.30",
            realised_net_native="-0.30",
            strategy_version="vB",
            git_sha=sha_b,
        )
        _insert_evm(
            conn,
            "a-shadow-win",
            mode="SHADOW",
            status="CLOSED",
            closed_at=now - 80,
            realised_user_net_native="0.10",
            realised_net_native="0.10",
            strategy_version="vA",
            git_sha=sha_a,
        )

    with solana_sibot.connect(app) as conn:
        _insert_sol(
            conn,
            "a-live-sol-win",
            status="CLOSED",
            closed_at=now - 70,
            realised_net_sol="0.05",
            strategy_version="vA",
            git_sha=sha_a,
        )

    # Add a receipt-confirmed AUTO arbitrage trade from the same deployment. It must
    # remain distinct from SiBot even though version/SHA are identical.
    auto_trader._append(
        app.csv_dir / "auto" / "auto_trade_execution.csv",
        _auto_row(now - 60, realised_net_base="0.03", profit_fee_base="0.005"),
    )

    groups = provenance.strategy_attribution_24h(app, "123", now=now)
    keyed = {
        (g["strategy_engine"], g["mode"], g["strategy_version"], g["git_sha"]): g
        for g in groups
    }

    assert set(keyed) == {
        (provenance.EVM_ENGINE, "LIVE", "vA", sha_a),
        (provenance.EVM_ENGINE, "LIVE", "vB", sha_b),
        (provenance.EVM_ENGINE, "SHADOW", "vA", sha_a),
        (provenance.SOLANA_ENGINE, "LIVE", "vA", sha_a),
        (provenance.AUTO_EVM_ENGINE, "LIVE", provenance.STRATEGY_VERSION, provenance.GIT_SHA),
    }
    assert keyed[(provenance.EVM_ENGINE, "LIVE", "vA", sha_a)]["wins"] == 1
    assert keyed[(provenance.EVM_ENGINE, "LIVE", "vB", sha_b)]["losses"] == 1
    assert keyed[(provenance.EVM_ENGINE, "SHADOW", "vA", sha_a)]["wins"] == 1
    assert keyed[(provenance.SOLANA_ENGINE, "LIVE", "vA", sha_a)]["wins"] == 1
    auto_group = keyed[(provenance.AUTO_EVM_ENGINE, "LIVE", provenance.STRATEGY_VERSION, provenance.GIT_SHA)]
    assert auto_group["wins"] == 1
    assert auto_group["pnl_by_chain"]["polygon"] == provenance.Decimal("0.025")

    report = provenance.strategy_attribution_report_24h(app, "123", now=now)
    assert provenance.EVM_ENGINE in report
    assert provenance.SOLANA_ENGINE in report
    assert provenance.AUTO_EVM_ENGINE in report
    assert "vA" in report and sha_a[:12] in report
    assert "vB" in report and sha_b[:12] in report
    assert "LIVE" in report and "SHADOW" in report
