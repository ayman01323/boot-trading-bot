from learnerbot.strategy_ai_cost_gate import build_material_snapshot, evaluate_cost_gate, material_sha256


def _evidence():
    return {
        "schema_version": 1,
        "generated_epoch": 123456,
        "generated_utc": "volatile",
        "window_hours": 12,
        "audit_metrics": {
            "chain_counts": {"base": 49},
            "action_counts": {"BUY": 19},
            "status_counts": {"SUCCESS": 9},
            "failed_transactions_by_chain": {"base": 0},
            "native_delta_by_chain": {"base": "0.01234"},
            "fee_native_by_chain": {"base": "0.0012"},
            "bot_decision_digest": {"status_counts": {"REJECT": 19}},
            "collection_error_digest": [],
        },
        "solana_live": {
            "performance": {
                "closed_trades": 3,
                "wins": 2,
                "losses": 1,
                "gross_profit_sol": "0.0104",
                "gross_loss_sol": "0.0031",
                "net_sol": "0.0073",
                "profit_factor": "3.3548",
                "exit_reason_counts": {"TAKE_PROFIT": 2, "STOP_LOSS": 1},
            },
            "exit_circuit_status_counts": {},
            "open_live": [{"unrealised_pct": 99, "updated_at": 123456}],
        },
        "profit_control": {
            "state": {"active_profile": "balanced", "last_run_epoch": "123456"},
            "strategy_registry": [
                {
                    "profile": "balanced",
                    "closed_trades": 3,
                    "wins": 2,
                    "losses": 1,
                    "net_sol": "0.0073",
                    "profit_factor": "3.3548",
                    "successful": 1,
                    "updated_at": 123456,
                }
            ],
        },
    }


def test_snapshot_ignores_timestamps_and_open_mark_to_market_noise():
    a = _evidence()
    b = _evidence()
    b["generated_epoch"] = 999999
    b["generated_utc"] = "later"
    b["solana_live"]["open_live"][0]["unrealised_pct"] = -77
    b["solana_live"]["open_live"][0]["updated_at"] = 999999
    b["profit_control"]["state"]["last_run_epoch"] = "999999"
    b["profit_control"]["strategy_registry"][0]["updated_at"] = 999999
    assert build_material_snapshot(a) == build_material_snapshot(b)
    assert material_sha256(a) == material_sha256(b)


def test_small_transaction_count_changes_stay_inside_activity_bucket():
    a = _evidence()
    b = _evidence()
    b["audit_metrics"]["chain_counts"]["base"] = 48
    b["audit_metrics"]["action_counts"]["BUY"] = 18
    assert material_sha256(a) == material_sha256(b)


def test_realised_trade_or_failure_is_material():
    a = _evidence()
    b = _evidence()
    b["solana_live"]["performance"]["closed_trades"] = 4
    assert material_sha256(a) != material_sha256(b)

    c = _evidence()
    c["audit_metrics"]["failed_transactions_by_chain"]["base"] = 1
    assert material_sha256(a) != material_sha256(c)


def test_gate_runs_first_time_source_change_material_change_and_six_hour_refresh():
    evidence = _evidence()
    first = evaluate_cost_gate(source_commit="aaa", evidence=evidence, previous_state={}, now_epoch=10_000)
    assert first["run_ai"] is True
    assert first["reason"] == "NO_PREVIOUS_AI_ATTEMPT"

    previous = {
        "last_ai_attempt_epoch": 10_000,
        "last_ai_attempt_source_commit": "aaa",
        "last_ai_attempt_material_sha256": material_sha256(evidence),
    }
    unchanged = evaluate_cost_gate(source_commit="aaa", evidence=evidence, previous_state=previous, now_epoch=10_000 + 3599)
    assert unchanged["run_ai"] is False
    assert unchanged["reason"] == "UNCHANGED_WITHIN_REFRESH_WINDOW"

    changed_source = evaluate_cost_gate(source_commit="bbb", evidence=evidence, previous_state=previous, now_epoch=10_100)
    assert changed_source["run_ai"] is True
    assert changed_source["reason"] == "SOURCE_COMMIT_CHANGED"

    changed_evidence = _evidence()
    changed_evidence["solana_live"]["performance"]["losses"] = 2
    changed = evaluate_cost_gate(source_commit="aaa", evidence=changed_evidence, previous_state=previous, now_epoch=10_100)
    assert changed["run_ai"] is True
    assert changed["reason"] == "MATERIAL_EVIDENCE_CHANGED"

    refresh = evaluate_cost_gate(source_commit="aaa", evidence=evidence, previous_state=previous, now_epoch=10_000 + 21_600)
    assert refresh["run_ai"] is True
    assert refresh["reason"] == "FORCED_REFRESH_DUE"


def test_manual_request_always_runs():
    evidence = _evidence()
    previous = {
        "last_ai_attempt_epoch": 20_000,
        "last_ai_attempt_source_commit": "aaa",
        "last_ai_attempt_material_sha256": material_sha256(evidence),
    }
    result = evaluate_cost_gate(source_commit="aaa", evidence=evidence, previous_state=previous, manual=True, now_epoch=20_001)
    assert result["run_ai"] is True
    assert result["reason"] == "MANUAL_REQUEST"
