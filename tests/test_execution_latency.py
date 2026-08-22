from __future__ import annotations

import time
from types import SimpleNamespace

from learnerbot import execution_latency as latency


def _app(tmp_path):
    return SimpleNamespace(data_dir=tmp_path)


def test_execution_latency_records_ms_and_reports_stage_percentiles(tmp_path):
    app = _app(tmp_path)
    for i in range(5):
        latency.record_sample(
            app,
            attempt_key=f"attempt-{i}",
            telegram_id="test-user",
            action="BUY",
            status="EXECUTED",
            receive_delay_ms=100 + i,
            strategy_ms=10 + i,
            latency={
                "pre_balance_ms": 20 + i,
                "order_ms": 80 + i,
                "transaction_construction_ms": 2 + i,
                "simulation_ms": 40 + i,
                "execute_ms": 90 + i,
                "post_balance_ms": 20 + i,
                "execution_total_ms": 252 + 6 * i,
            },
        )

    report = latency.summary(app, now=int(time.time()))

    assert report["samples_24h"] == 5
    assert report["samples_7d"] == 5
    assert report["current_24h"]["strategy_ms"]["p50_ms"] == 12.0
    assert report["current_24h"]["transaction_construction_ms"]["p50_ms"] == 4.0
    assert report["current_24h"]["order_ms"]["p95_ms"] == 83.8
    assert report["current_24h"]["total_event_to_result_ms"]["count"] == 5
    assert report["infrastructure_conclusion"] in {"KEEP", "BENCHMARK"}
    assert report["fast_server_comparison"]["candidate_regions"] == ["Frankfurt", "Amsterdam", "London"]


def test_execution_latency_requires_benchmark_before_five_samples(tmp_path):
    app = _app(tmp_path)
    latency.record_sample(
        app,
        attempt_key="one",
        telegram_id="test-user",
        action="BUY",
        status="EXECUTED",
        receive_delay_ms=0,
        strategy_ms=1,
        latency={"transaction_construction_ms": 1, "execution_total_ms": 5},
    )

    report = latency.summary(app, now=int(time.time()))

    assert report["samples_24h"] == 1
    assert report["infrastructure_conclusion"] == "BENCHMARK"
    assert "at least five" in report["recommendation"].lower()


def test_execution_latency_empty_history_never_invents_measurements(tmp_path):
    report = latency.summary(_app(tmp_path), now=int(time.time()))

    assert report["available"] is False
    assert report["samples_24h"] == 0
    assert report["current_24h"]["strategy_ms"]["p50_ms"] is None
    assert report["current_24h"]["transaction_construction_ms"]["p95_ms"] is None
    assert report["infrastructure_conclusion"] == "BENCHMARK"
