# Full-CI probe for the non-deploying agent branch.
from types import SimpleNamespace
import time

from learnerbot import solana_leader_discovery_coverage_patch as patch
from learnerbot import solana_sibot as sol


def _app(tmp_path):
    app = SimpleNamespace(data_dir=tmp_path / "data", csv_dir=tmp_path / "csv")
    app.data_dir.mkdir()
    app.csv_dir.mkdir()
    return app


def test_jupiter_route_log_is_recognised_as_swap_candidate():
    result = {"meta": {"logMessages": ["Program log: Instruction: Shared Accounts Route"]}}
    assert patch.looks_like_swap(result) is True


def test_rotating_recent_discovery_samples_eight_blocks(tmp_path, monkeypatch):
    app = _app(tmp_path)
    settings = {
        "enabled": "true",
        "discovery_sampled_blocks_per_cycle": "8",
        "discovery_recent_window_slots": "48",
        "history_candidate_limit": "300",
    }
    monkeypatch.setattr(sol, "settings", lambda app: settings)
    calls = []

    def fake_rpc(app, method, params):
        if method == "getSlot":
            return 1000
        if method == "getBlock":
            calls.append(int(params[0]))
            return {"blockTime": int(time.time()), "transactions": []}
        raise AssertionError(method)

    monkeypatch.setattr(sol, "_rpc", fake_rpc)
    found = patch.discover_recent_blocks(app)
    assert found == 0
    assert len(calls) == 8
    assert calls[-1] == 1000
    assert min(calls) >= 953


def test_history_backfill_reaches_candidates_beyond_old_top_100(tmp_path, monkeypatch):
    app = _app(tmp_path)
    monkeypatch.setattr(
        sol,
        "settings",
        lambda app: {"history_candidate_limit": "300", "history_refresh_hours": "12"},
    )
    now = int(time.time())
    with sol.connect(app) as conn:
        for i in range(150):
            wallet = f"wallet{i:03d}"
            conn.execute(
                "INSERT INTO candidates(wallet,first_seen,last_seen,swap_events,last_signature,updated_at) VALUES(?,?,?,?,?,?)",
                (wallet, now, now, 150 - i, f"sig{i}", now),
            )
        for i in range(100):
            conn.execute(
                "INSERT INTO history_status(wallet,fetched_at,signatures,swaps,closed_trades,truncated,error) VALUES(?,?,?,?,?,?,?)",
                (f"wallet{i:03d}", now, 0, 0, 0, 0, ""),
            )
        conn.commit()

    assert patch.next_history_wallet(app) == "wallet100"
