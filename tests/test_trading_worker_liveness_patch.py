from __future__ import annotations

import json
from contextlib import closing
from types import SimpleNamespace

from learnerbot import sibot
from learnerbot import trading_worker_liveness_patch as patch


def _app(tmp_path):
    csv_dir = tmp_path / "CSVbot"
    data_dir = tmp_path / "data"
    csv_dir.mkdir()
    data_dir.mkdir()
    return SimpleNamespace(csv_dir=csv_dir, data_dir=data_dir)


def test_evm_background_scan_reaches_orphan_beyond_ranked_first_250(tmp_path, monkeypatch):
    app = _app(tmp_path)
    chain = SimpleNamespace(chain_id=56, slug="bsc", type="EVM")
    ranked = {f"0x{i:040x}" for i in range(300)}
    error = "RuntimeError: Etherscan txlist: NOTOK Invalid API Key"

    with closing(sibot.connect(app)) as conn:
        for i, wallet in enumerate(sorted(ranked)):
            conn.execute(
                """INSERT INTO wallet_history_status(
                       chain_id,chain_slug,wallet,fetched_at,history_complete,error
                   ) VALUES(?,?,?,?,0,?)""",
                (56, "bsc", wallet, 100 + i, error),
            )
        orphan = "0x" + "f" * 40
        conn.execute(
            """INSERT INTO wallet_history_status(
                   chain_id,chain_slug,wallet,fetched_at,history_complete,error
               ) VALUES(?,?,?,?,0,?)""",
            (56, "bsc", orphan, 1000, error),
        )
        conn.commit()

    monkeypatch.setattr(patch._evm, "_ranked_wallets", lambda _app, _chain: ranked)
    result = patch._evm_background_candidate_no_starvation(app, chain, now_epoch=10_000)

    assert result == (orphan, "LEGACY_ETHERSCAN")


def test_evm_dead_drainer_flag_is_restarted(monkeypatch):
    calls = []
    monkeypatch.setattr(patch._evm, "_is_runtime_run_command", lambda: True)
    monkeypatch.setattr(patch, "_alive_thread_names", lambda: set())
    monkeypatch.setattr(patch._evm, "_DRAINER_STARTED", True)
    monkeypatch.setattr(patch, "_ORIGINAL_EVM_ENSURE", lambda app: calls.append(app) or True)
    app = SimpleNamespace()

    assert patch._ensure_evm_drainer_live(app) is True
    assert calls == [app]
    assert patch._evm._DRAINER_STARTED is False


def test_evm_live_drainer_is_not_duplicated(monkeypatch):
    calls = []
    monkeypatch.setattr(patch._evm, "_is_runtime_run_command", lambda: True)
    monkeypatch.setattr(patch, "_alive_thread_names", lambda: {"sibot-legacy-backlog-drainer"})
    monkeypatch.setattr(patch, "_ORIGINAL_EVM_ENSURE", lambda app: calls.append(app) or True)
    monkeypatch.setattr(patch._evm, "_DRAINER_STARTED", False)

    assert patch._ensure_evm_drainer_live(SimpleNamespace()) is False
    assert calls == []
    assert patch._evm._DRAINER_STARTED is True


def test_missing_solana_threads_are_restarted_without_changing_gates(monkeypatch):
    started = []

    class FakeConn:
        def close(self):
            return None

    class FakeThread:
        def __init__(self, *, target, args, daemon, name):
            self.target = target
            self.args = args
            self.daemon = daemon
            self.name = name

        def start(self):
            started.append(self.name)

    monkeypatch.setattr(patch, "_alive_thread_names", lambda: set())
    monkeypatch.setattr(patch._sol, "ensure_settings", lambda app: None)
    monkeypatch.setattr(patch._sol, "connect", lambda app: FakeConn())
    monkeypatch.setattr(patch.threading, "Thread", FakeThread)
    monkeypatch.setattr(patch._sol, "_WORKER_STARTED", True)

    launched = patch._ensure_solana_threads(SimpleNamespace())

    assert launched == ["sibot-solana-discovery", "sibot-solana-history", "sibot-solana-leaders"]
    assert started == launched


def test_stale_solana_selector_gets_ranking_only_refresh(tmp_path, monkeypatch):
    selector = tmp_path / "solana_leader_selector.json"
    selector.write_text(json.dumps({"generated_epoch": 100}), encoding="utf-8")
    monkeypatch.setattr(patch, "_SOL_SELECTOR", selector)
    calls = []
    monkeypatch.setattr(patch._sol, "refresh_rankings", lambda app: calls.append(app) or [])

    app = SimpleNamespace()
    assert patch._refresh_solana_selector_if_stale(app, now=1000) is True
    assert calls == [app]


def test_recent_auto_summary_contains_counts_not_identifiers(tmp_path):
    app = _app(tmp_path)
    auto = app.csv_dir / "auto"
    auto.mkdir()
    (auto / "auto_trade_simulations.csv").write_text(
        "timestamp_epoch,telegram_id,wallet_address,chain_slug,route_id,simulation_ok,reason\n"
        "1000,SECRET_USER,0xSECRET,polygon,SECRET_ROUTE,false,round trip profit below minimum\n",
        encoding="utf-8",
    )

    out = patch._recent_auto_summary(app, now=1100, seconds=3600)
    encoded = json.dumps(out)
    assert out["simulations"] == 1
    assert out["passed"] == 0
    assert "round trip profit below minimum" in encoded
    assert "SECRET_USER" not in encoded
    assert "0xSECRET" not in encoded
    assert "SECRET_ROUTE" not in encoded
