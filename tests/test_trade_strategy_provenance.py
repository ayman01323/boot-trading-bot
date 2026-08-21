from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

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


def test_new_evm_and_solana_positions_are_stamped_and_immutable(tmp_path):
    app = App(tmp_path)

    with sibot.connect(app) as conn:
        _insert_evm(conn, "evm-new")
        row = conn.execute(
            "SELECT strategy_version,git_sha FROM positions WHERE position_id='evm-new'"
        ).fetchone()
        assert row["strategy_version"] == provenance.STRATEGY_VERSION
        assert row["git_sha"] == provenance.GIT_SHA
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE positions SET git_sha=? WHERE position_id='evm-new'", ("0" * 40,))

    with solana_sibot.connect(app) as conn:
        _insert_sol(conn, "sol-new")
        row = conn.execute(
            "SELECT strategy_version,git_sha FROM positions WHERE position_id='sol-new'"
        ).fetchone()
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
            "SELECT strategy_version,git_sha FROM positions WHERE position_id='old-position'"
        ).fetchone()
        assert row["strategy_version"] == provenance.LEGACY_VALUE
        assert row["git_sha"] == provenance.LEGACY_VALUE


def test_24h_attribution_never_merges_different_versions_or_live_shadow(tmp_path):
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

    groups = provenance.strategy_attribution_24h(app, "123", now=now)
    keyed = {(g["mode"], g["strategy_version"], g["git_sha"]): g for g in groups}

    assert set(keyed) == {
        ("LIVE", "vA", sha_a),
        ("LIVE", "vB", sha_b),
        ("SHADOW", "vA", sha_a),
    }
    assert keyed[("LIVE", "vA", sha_a)]["wins"] == 2
    assert keyed[("LIVE", "vA", sha_a)]["losses"] == 0
    assert keyed[("LIVE", "vA", sha_a)]["pnl_by_chain"]["base"] == provenance.Decimal("0.20")
    assert keyed[("LIVE", "vA", sha_a)]["pnl_by_chain"]["solana"] == provenance.Decimal("0.05")
    assert keyed[("LIVE", "vB", sha_b)]["wins"] == 0
    assert keyed[("LIVE", "vB", sha_b)]["losses"] == 1
    assert keyed[("SHADOW", "vA", sha_a)]["wins"] == 1

    report = provenance.strategy_attribution_report_24h(app, "123", now=now)
    assert "vA" in report and sha_a[:12] in report
    assert "vB" in report and sha_b[:12] in report
    assert "LIVE" in report and "SHADOW" in report
