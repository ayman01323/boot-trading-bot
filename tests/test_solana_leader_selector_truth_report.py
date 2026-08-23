import json
import time

from learnerbot import solana_trade_gate_truth_patch as truth


def test_selector_truth_reads_sanitised_fresh_bridge(monkeypatch, tmp_path):
    path = tmp_path / "selector.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_epoch": int(time.time()),
                "pool": 314,
                "qualified": 0,
                "selected": 0,
                "first_failure_counts": {
                    "historical profit factor below minimum": 120,
                    "median return below LIVE edge floor": 194,
                },
                "thresholds_unchanged": True,
                "wallet_addresses_published": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(truth, "_SELECTOR_BRIDGE", path)
    out = truth._selector_truth()
    assert out["pool"] == 314
    assert out["qualified"] == 0
    assert out["selected"] == 0
    assert out["failures"]["median return below LIVE edge floor"] == 194
    assert out["thresholds_unchanged"] is True
    assert "wallet" not in json.dumps(out).lower()


def test_selector_truth_rejects_stale_or_malformed_bridge(monkeypatch, tmp_path):
    path = tmp_path / "selector.json"
    path.write_text(
        json.dumps({"generated_epoch": int(time.time()) - 9999, "pool": 1}),
        encoding="utf-8",
    )
    monkeypatch.setattr(truth, "_SELECTOR_BRIDGE", path)
    assert truth._selector_truth(max_age_seconds=60) == {}
    path.write_text("not-json", encoding="utf-8")
    assert truth._selector_truth() == {}
