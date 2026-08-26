from types import SimpleNamespace

from learnerbot import sibot1_solana_candidate_state_fix_patch as fix


def _app(tmp_path):
    csv_dir = tmp_path / "csv"
    data_dir = tmp_path / "data"
    csv_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(csv_dir=csv_dir, data_dir=data_dir)


def _exit_candidate():
    return {
        "candidate_id": "exit-1",
        "kind": "EXIT",
        "chain": "solana",
        "shadow_lot_id": "lot-1",
        "engine_id": "grok",
        "asset": "Mint111111111111111111111111111111111",
        "poolcheck_verdict": "UNSPECIFIED",
    }


def _entry_candidate():
    return {
        "candidate_id": "entry-1",
        "kind": "ENTRY",
        "chain": "solana",
        "shadow_lot_id": "lot-2",
        "engine_id": "grok",
        "asset_out": "Mint222222222222222222222222222222222",
        "poolcheck_verdict": "SHADOW_ONLY",
    }


def test_exit_without_live_position_is_suppressed_before_claim(tmp_path, monkeypatch):
    app = _app(tmp_path)
    called = {"prev": 0, "reconcile": 0}
    monkeypatch.setattr(fix._bridge, "_candidate_age", lambda candidate: 0)
    monkeypatch.setattr(fix._bridge, "readiness", lambda app, tid: {"exit_execution_active": True})
    monkeypatch.setattr(fix, "_confirmed_live_position", lambda app, tid, candidate: None)
    monkeypatch.setattr(
        fix,
        "_reconcile_missing_exit_position",
        lambda app, tid, candidate: called.__setitem__("reconcile", called["reconcile"] + 1),
    )
    monkeypatch.setattr(
        fix,
        "_PREV_PROCESS_CANDIDATE",
        lambda app, tid, candidate: called.__setitem__("prev", called["prev"] + 1),
    )

    fix._process_candidate_state_aware(app, "123", _exit_candidate())

    assert called["reconcile"] == 1
    assert called["prev"] == 0


def test_real_live_position_exit_reaches_existing_safe_pipeline(tmp_path, monkeypatch):
    app = _app(tmp_path)
    called = {"prev": 0}
    monkeypatch.setattr(fix._bridge, "_candidate_age", lambda candidate: 0)
    monkeypatch.setattr(fix._bridge, "readiness", lambda app, tid: {"exit_execution_active": True})
    monkeypatch.setattr(
        fix,
        "_confirmed_live_position",
        lambda app, tid, candidate: {"status": "OPEN", "token_raw": "100", "mint": candidate["asset"]},
    )
    monkeypatch.setattr(
        fix,
        "_PREV_PROCESS_CANDIDATE",
        lambda app, tid, candidate: called.__setitem__("prev", called["prev"] + 1),
    )

    fix._process_candidate_state_aware(app, "123", _exit_candidate())

    assert called["prev"] == 1


def test_untracked_wallet_owned_exit_records_reconciliation_only(tmp_path, monkeypatch):
    app = _app(tmp_path)
    candidate = _exit_candidate()
    monkeypatch.setattr(fix, "_wallet_token_balance_raw", lambda app, tid, mint: 777)

    fix._reconcile_missing_exit_position(app, "123", candidate)

    row = fix.reconciliation_row(app, "123", candidate["asset"])
    assert row is not None
    assert row["status"] == "RECONCILIATION_OWNED"
    assert row["token_raw"] == "777"
    assert row["source"] == "runtime_exit_signal"


def test_solana_entry_claim_is_not_announced_live_before_revalidation(monkeypatch):
    calls = []
    monkeypatch.setattr(
        fix,
        "_PREV_CANDIDATE_SELECTED",
        lambda module, chain, app, tid, candidate: calls.append(dict(candidate)),
    )

    fix._candidate_selected_state_aware(fix._bridge, "solana", object(), "123", _entry_candidate())

    assert calls == []


def test_solana_entry_announced_live_only_after_revalidation(monkeypatch):
    calls = []
    candidate = _entry_candidate()
    candidate["_live_revalidated"] = True
    monkeypatch.setattr(
        fix,
        "_PREV_CANDIDATE_SELECTED",
        lambda module, chain, app, tid, candidate: calls.append(dict(candidate)),
    )

    fix._candidate_selected_state_aware(fix._bridge, "solana", object(), "123", candidate)

    assert len(calls) == 1
    assert calls[0]["poolcheck_verdict"] == "LIVE_REVALIDATED"


def test_exit_alert_uses_position_confirmed_not_unspecified(monkeypatch):
    calls = []
    candidate = _exit_candidate()
    monkeypatch.setattr(
        fix,
        "_confirmed_live_position",
        lambda app, tid, candidate: {"status": "OPEN", "token_raw": "100"},
    )
    monkeypatch.setattr(
        fix,
        "_PREV_CANDIDATE_SELECTED",
        lambda module, chain, app, tid, candidate: calls.append(dict(candidate)),
    )

    fix._candidate_selected_state_aware(fix._bridge, "solana", object(), "123", candidate)

    assert len(calls) == 1
    assert calls[0]["poolcheck_verdict"] == "POSITION_CONFIRMED"
    assert calls[0]["poolcheck_verdict"] != "UNSPECIFIED"


def test_successful_fresh_live_revalidation_promotes_entry_alert(monkeypatch):
    calls = []
    candidate = _entry_candidate()
    fix._alerts._TLS.solana_entry = ("123", candidate)
    monkeypatch.setattr(fix, "_PREV_LIVE_REVALIDATION", lambda app, mint, amount: (True, "PASS", {}))
    monkeypatch.setattr(
        fix,
        "_candidate_selected_state_aware",
        lambda module, chain, app, tid, candidate: calls.append(dict(candidate)),
    )
    try:
        result = fix._live_revalidation_with_selection(object(), candidate["asset_out"], 0.0005)
    finally:
        try:
            delattr(fix._alerts._TLS, "solana_entry")
        except AttributeError:
            pass

    assert result[0] is True
    assert len(calls) == 1
    assert calls[0]["_live_revalidated"] is True


def test_failed_fresh_live_revalidation_never_promotes_entry_alert(monkeypatch):
    calls = []
    candidate = _entry_candidate()
    fix._alerts._TLS.solana_entry = ("123", candidate)
    monkeypatch.setattr(
        fix,
        "_PREV_LIVE_REVALIDATION",
        lambda app, mint, amount: (False, "LP_CONCENTRATION_RISK", {}),
    )
    monkeypatch.setattr(
        fix,
        "_candidate_selected_state_aware",
        lambda module, chain, app, tid, candidate: calls.append(dict(candidate)),
    )
    try:
        result = fix._live_revalidation_with_selection(object(), candidate["asset_out"], 0.0005)
    finally:
        try:
            delattr(fix._alerts._TLS, "solana_entry")
        except AttributeError:
            pass

    assert result[0] is False
    assert calls == []


def test_one_time_wallet_inventory_records_only_untracked_holdings(tmp_path, monkeypatch):
    app = _app(tmp_path)
    rpc_calls = []
    tracked_mint = "Tracked11111111111111111111111111111111"
    untracked_mint = "Untracked11111111111111111111111111111"

    monkeypatch.setattr(fix, "_wallet_address", lambda app, tid: "Wallet111111111111111111111111111111111")
    monkeypatch.setattr(fix, "_bridge_open_mints", lambda app, tid: {tracked_mint})

    def fake_rpc(app, method, params):
        rpc_calls.append((method, params[1]))
        if params[1].get("programId") == fix._TOKEN_PROGRAMS[0]:
            return {
                "value": [
                    {"account": {"data": {"parsed": {"info": {"mint": tracked_mint, "tokenAmount": {"amount": "50"}}}}}},
                    {"account": {"data": {"parsed": {"info": {"mint": untracked_mint, "tokenAmount": {"amount": "75"}}}}}},
                ]
            }
        return {"value": []}

    monkeypatch.setattr(fix._bridge._sol, "_rpc", fake_rpc)

    assert fix.reconcile_wallet_owned_tokens(app, "123") is True
    assert fix.reconciliation_row(app, "123", tracked_mint) is None
    row = fix.reconciliation_row(app, "123", untracked_mint)
    assert row is not None
    assert row["status"] == "RECONCILIATION_OWNED"
    assert row["source"] == "pre_fix_migration"
    assert len(rpc_calls) == 2

    # Migration marker makes this a one-time scan for the same user/version.
    assert fix.reconcile_wallet_owned_tokens(app, "123") is True
    assert len(rpc_calls) == 2
