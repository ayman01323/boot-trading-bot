from contextlib import closing
from types import SimpleNamespace

from learnerbot import sibot
from learnerbot import sibot_legacy_backlog_drainer_patch as patch


def _app(tmp_path):
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    return SimpleNamespace(data_dir=tmp_path / "data", csv_dir=csv_dir)


def _chain(slug="bsc", chain_id=56):
    return SimpleNamespace(slug=slug, chain_id=chain_id, type="EVM")


def _insert(app, chain, wallet, fetched_at, error):
    with closing(sibot.connect(app)) as conn:
        conn.execute(
            """INSERT INTO wallet_history_status(
                   chain_id,chain_slug,wallet,fetched_at,history_complete,error
               ) VALUES(?,?,?,?,0,?)""",
            (chain.chain_id, chain.slug, wallet, fetched_at, error),
        )
        conn.commit()


def test_drainer_only_targets_orphaned_legacy_outside_ranked_window(tmp_path, monkeypatch):
    app = _app(tmp_path)
    chain = _chain()
    _insert(app, chain, "0xranked", 100, "RuntimeError: Etherscan txlist: NOTOK Invalid API Key")
    _insert(app, chain, "0xorphan", 200, "RuntimeError: Etherscan txlist: NOTOK Free API access is not supported")

    monkeypatch.setattr(patch, "_ranked_wallets", lambda app, chain: {"0xranked"})
    monkeypatch.setattr(patch._alchemy, "alchemy_rpc_url", lambda app, chain_id: "https://alchemy.invalid/redacted")
    calls = []

    def refresh(app, chain, wallet):
        calls.append(wallet)
        with closing(sibot.connect(app)) as conn:
            conn.execute(
                "UPDATE wallet_history_status SET error='',history_complete=1 WHERE chain_id=? AND wallet=?",
                (chain.chain_id, wallet),
            )
            conn.commit()
        return {"wallet": wallet, "complete": True, "trades": 1}

    monkeypatch.setattr(patch._sibot, "refresh_wallet_history", refresh)

    result = patch._drain_once(app, now_epoch=10_000, chains=[chain])

    assert result["status"] == "SUCCESS"
    assert result["wallet"] == "0xorphan"
    assert result["kind"] == "LEGACY_ETHERSCAN"
    assert calls == ["0xorphan"]
    status = patch.status_for_chain(app, chain)
    assert status["attempts"] == 1
    assert status["successes"] == 1
    assert status["last_result"] == "SUCCESS"


def test_ranked_queue_wallet_is_not_duplicated_by_background_drainer(tmp_path, monkeypatch):
    app = _app(tmp_path)
    chain = _chain()
    _insert(app, chain, "0xranked", 100, "RuntimeError: Etherscan txlist: NOTOK Invalid API Key")

    monkeypatch.setattr(patch, "_ranked_wallets", lambda app, chain: {"0xranked"})
    monkeypatch.setattr(patch._alchemy, "alchemy_rpc_url", lambda app, chain_id: "https://alchemy.invalid/redacted")
    calls = []
    monkeypatch.setattr(
        patch._sibot,
        "refresh_wallet_history",
        lambda app, chain, wallet: calls.append(wallet) or {"wallet": wallet},
    )

    assert patch._drain_once(app, now_epoch=10_000, chains=[chain]) == {"status": "IDLE"}
    assert calls == []


def test_provider_429_sets_account_wide_exponential_backoff(tmp_path, monkeypatch):
    app = _app(tmp_path)
    chain = _chain()
    _insert(app, chain, "0xorphan", 100, "RuntimeError: Etherscan txlist: NOTOK Invalid API Key")

    monkeypatch.setattr(patch, "_ranked_wallets", lambda app, chain: set())
    monkeypatch.setattr(patch._alchemy, "alchemy_rpc_url", lambda app, chain_id: "https://alchemy.invalid/redacted")
    monkeypatch.setattr(
        patch._sibot,
        "refresh_wallet_history",
        lambda app, chain, wallet: {
            "wallet": wallet,
            "complete": False,
            "error": "RuntimeError: Alchemy tx context: HTTP 429; retries exhausted",
        },
    )

    base_backoff = patch._rate_limit_backoff_seconds(app)
    max_backoff = patch._max_backoff_seconds(app)

    first = patch._drain_once(app, now_epoch=1_000, chains=[chain])
    assert first["status"] == "RATE_LIMIT"
    assert first["backoff_seconds"] == base_backoff
    assert first["next_epoch"] == 1_000 + base_backoff
    assert patch._read_state_int(app, patch._GLOBAL_PRESSURE_KEY, 0) == 1

    blocked_at = 1_000 + max(1, base_backoff // 2)
    blocked = patch._drain_once(app, now_epoch=blocked_at, chains=[chain])
    assert blocked == {"status": "BACKOFF", "next_epoch": 1_000 + base_backoff}

    second_at = 1_001 + base_backoff
    second = patch._drain_once(app, now_epoch=second_at, chains=[chain])
    second_backoff = min(max_backoff, base_backoff * 2)
    assert second["status"] == "RATE_LIMIT"
    assert second["backoff_seconds"] == second_backoff
    assert second["next_epoch"] == second_at + second_backoff
    assert patch._read_state_int(app, patch._GLOBAL_PRESSURE_KEY, 0) == 2


def test_success_resets_account_wide_provider_pressure(tmp_path, monkeypatch):
    app = _app(tmp_path)
    chain = _chain("base", 8453)
    _insert(app, chain, "0xorphan", 100, "RuntimeError: Etherscan txlist: NOTOK Invalid API Key")
    patch._write_state(app, {patch._GLOBAL_PRESSURE_KEY: 4})

    monkeypatch.setattr(patch, "_ranked_wallets", lambda app, chain: set())
    monkeypatch.setattr(patch._alchemy, "alchemy_rpc_url", lambda app, chain_id: "https://alchemy.invalid/redacted")
    monkeypatch.setattr(
        patch._sibot,
        "refresh_wallet_history",
        lambda app, chain, wallet: {"wallet": wallet, "complete": True, "trades": 2},
    )

    result = patch._drain_once(app, now_epoch=10_000, chains=[chain])
    assert result["status"] == "SUCCESS"
    assert patch._read_state_int(app, patch._GLOBAL_PRESSURE_KEY, -1) == 0


def test_transient_alchemy_orphan_is_recovered_after_retry_age(tmp_path, monkeypatch):
    app = _app(tmp_path)
    chain = _chain("base", 8453)
    _insert(app, chain, "0xretry", 100, "AlchemyHistoryError: HTTP 429; retries exhausted")

    monkeypatch.setattr(patch, "_ranked_wallets", lambda app, chain: set())
    monkeypatch.setattr(patch._alchemy, "alchemy_rpc_url", lambda app, chain_id: "https://alchemy.invalid/redacted")
    monkeypatch.setattr(
        patch._sibot,
        "refresh_wallet_history",
        lambda app, chain, wallet: {"wallet": wallet, "complete": True, "trades": 2},
    )

    result = patch._drain_once(app, now_epoch=1_000, chains=[chain])
    assert result["status"] == "SUCCESS"
    assert result["wallet"] == "0xretry"
    assert result["kind"] == "TRANSIENT_ALCHEMY"


def test_progressive_trace_wallet_is_resumed_until_success(tmp_path, monkeypatch):
    app = _app(tmp_path)
    chain = _chain("bsc", 56)
    wallet = "0xprogress"
    _insert(app, chain, wallet, 100, "RuntimeError: Etherscan txlist: NOTOK Invalid API Key")

    monkeypatch.setattr(patch, "_ranked_wallets", lambda app, chain: set())
    monkeypatch.setattr(patch._alchemy, "alchemy_rpc_url", lambda app, chain_id: "https://alchemy.invalid/redacted")
    calls = []

    def refresh(app, chain, selected):
        calls.append(selected)
        if len(calls) == 1:
            error = "AlchemyHistoryProgress: trace progress pending 4/8; worker yielded for cross-chain fairness"
            with closing(sibot.connect(app)) as conn:
                conn.execute(
                    "UPDATE wallet_history_status SET fetched_at=?,error=? WHERE chain_id=? AND wallet=?",
                    (1_000, error, chain.chain_id, selected),
                )
                conn.commit()
            return {"wallet": selected, "complete": False, "error": error}
        with closing(sibot.connect(app)) as conn:
            conn.execute(
                "UPDATE wallet_history_status SET fetched_at=?,history_complete=1,error='' WHERE chain_id=? AND wallet=?",
                (1_180, chain.chain_id, selected),
            )
            conn.commit()
        return {"wallet": selected, "complete": True, "trades": 3}

    monkeypatch.setattr(patch._sibot, "refresh_wallet_history", refresh)

    first = patch._drain_once(app, now_epoch=1_000, chains=[chain])
    assert first["status"] == "PROGRESS"
    assert first["next_epoch"] == 1_180
    assert patch.status_for_chain(app, chain)["progress_backlog"] == 1

    # Global pacing is over, but this chain is deliberately held until the same
    # 180-second progressive retry age used by the trace worker.
    assert patch._drain_once(app, now_epoch=1_100, chains=[chain]) == {"status": "IDLE"}

    second = patch._drain_once(app, now_epoch=1_180, chains=[chain])
    assert second["status"] == "SUCCESS"
    assert second["wallet"] == wallet
    assert calls == [wallet, wallet]
    assert patch.status_for_chain(app, chain)["progress_backlog"] == 0


def test_nontransient_failure_backs_off_only_that_chain(tmp_path, monkeypatch):
    app = _app(tmp_path)
    bsc = _chain("bsc", 56)
    base = _chain("base", 8453)
    _insert(app, bsc, "0xbsc", 100, "RuntimeError: Etherscan txlist: NOTOK Invalid API Key")
    _insert(app, base, "0xbase", 100, "RuntimeError: Etherscan txlist: NOTOK Invalid API Key")

    monkeypatch.setattr(patch, "_ranked_wallets", lambda app, chain: set())
    monkeypatch.setattr(patch._alchemy, "alchemy_rpc_url", lambda app, chain_id: "https://alchemy.invalid/redacted")

    def refresh(app, chain, wallet):
        if chain.chain_id == bsc.chain_id:
            return {"wallet": wallet, "error": "AlchemyHistoryError: HTTP 400"}
        return {"wallet": wallet, "complete": True, "trades": 1}

    monkeypatch.setattr(patch._sibot, "refresh_wallet_history", refresh)

    first = patch._drain_once(app, now_epoch=1_000, chains=[bsc, base])
    assert first["status"] == "FAILED"
    assert first["chain_id"] == 56
    # Global pacing ends after 45s, while BSC remains in its five-minute local
    # cooldown, so Base can use the next bounded recovery slot.
    second = patch._drain_once(app, now_epoch=1_046, chains=[bsc, base])
    assert second["status"] == "SUCCESS"
    assert second["chain_id"] == 8453


def _fake_thread_recorder(started):
    class FakeThread:
        def __init__(self, *, target, args, daemon, name):
            self.target = target
            self.args = args
            self.daemon = daemon
            self.name = name

        def start(self):
            started.append((self.name, self.daemon))

    return FakeThread


def test_dynamic_start_workers_path_launches_one_drainer(monkeypatch):
    calls = []
    started = []

    monkeypatch.setattr(patch, "_PREV_START_WORKERS", lambda app: calls.append(app) or "BASE")
    monkeypatch.setattr(patch, "_is_runtime_run_command", lambda: True)
    monkeypatch.setattr(patch, "_DRAINER_STARTED", False)
    monkeypatch.setattr(patch, "_interval_seconds", lambda app: 45)
    monkeypatch.setattr(patch.threading, "Thread", _fake_thread_recorder(started))
    app = SimpleNamespace()

    assert patch.start_workers_with_legacy_backlog_drainer(app) == "BASE"
    assert patch.start_workers_with_legacy_backlog_drainer(app) == "BASE"
    assert calls == [app, app]
    assert started == [("sibot-legacy-backlog-drainer", True)]


def test_real_telegram_startup_path_launches_drainer_even_with_early_worker_capture(monkeypatch):
    calls = []
    started = []

    monkeypatch.setattr(patch, "_PREV_START_MENU_THREAD", lambda app: calls.append(app) or "MENU")
    monkeypatch.setattr(patch, "_is_runtime_run_command", lambda: True)
    monkeypatch.setattr(patch, "_DRAINER_STARTED", False)
    monkeypatch.setattr(patch, "_interval_seconds", lambda app: 45)
    monkeypatch.setattr(patch.threading, "Thread", _fake_thread_recorder(started))
    app = SimpleNamespace()

    assert patch.start_menu_thread_with_legacy_backlog_drainer(app) == "MENU"
    assert patch.start_menu_thread_with_legacy_backlog_drainer(app) == "MENU"
    assert calls == [app, app]
    assert started == [("sibot-legacy-backlog-drainer", True)]


def test_non_runtime_commands_do_not_launch_background_thread(monkeypatch):
    started = []
    monkeypatch.setattr(patch, "_is_runtime_run_command", lambda: False)
    monkeypatch.setattr(patch, "_DRAINER_STARTED", False)
    monkeypatch.setattr(patch.threading, "Thread", _fake_thread_recorder(started))

    assert patch._ensure_drainer_started(SimpleNamespace()) is False
    assert started == []


def test_install_changes_scheduling_only():
    import inspect

    source = inspect.getsource(patch.install)
    assert "_next_history_wallet" not in source
    assert "refresh_wallet_history" not in source
    assert "start_workers" in source
    assert "start_menu_thread" in source
