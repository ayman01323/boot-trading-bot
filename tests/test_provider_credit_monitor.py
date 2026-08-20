from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from learnerbot.provider_credit_alerts import alert_rows, mark_delivered, pending_master_ids, status_freshness
from learnerbot.provider_credit_monitor import collect_copilot, collect_gemini, collect_openai


NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_openai_uses_costs_and_hard_spend_limit():
    def opener(request, timeout=0):
        if request.full_url.endswith("/organization/spend_limit"):
            return _Response({"threshold_amount": 10000})
        assert "/organization/costs?" in request.full_url
        return _Response(
            {
                "data": [
                    {"results": [{"amount": {"value": 81.25, "currency": "usd"}}]},
                ],
                "has_more": False,
                "next_page": None,
            }
        )

    status = collect_openai({"OPENAI_ADMIN_KEY": "admin-test"}, opener=opener, now=NOW)
    assert status["state"] == "ALERT"
    assert status["percent"] == 81.25
    assert status["limit"] == 100.0


def test_openai_is_unknown_without_admin_key():
    status = collect_openai({}, now=NOW)
    assert status["state"] == "UNKNOWN"
    assert "OPENAI_ADMIN_KEY" in status["detail"]


def test_copilot_sums_gross_credits_not_net_credits():
    def opener(request, timeout=0):
        assert "/users/ayman01323/settings/billing/ai_credit/usage?" in request.full_url
        assert request.headers["X-github-api-version"] == "2026-03-10"
        return _Response(
            {
                "usageItems": [
                    {"grossQuantity": 700, "discountQuantity": 700, "netQuantity": 0},
                    {"grossQuantity": 550, "discountQuantity": 400, "netQuantity": 150},
                ]
            }
        )

    status = collect_copilot(
        {
            "COPILOT_BILLING_TOKEN": "billing-test",
            "COPILOT_BILLING_OWNER": "ayman01323",
            "COPILOT_MONTHLY_AI_CREDITS": "1500",
        },
        opener=opener,
        now=NOW,
    )
    assert status["consumed"] == 1250
    assert status["percent"] == 83.33
    assert status["state"] == "ALERT"


def test_gemini_decodes_budget_notification_and_returns_ack_id():
    payload = {
        "budgetDisplayName": "Gemini monthly",
        "costAmount": 90,
        "budgetAmount": 100,
        "currencyCode": "USD",
        "costIntervalStart": "2026-08-01T00:00:00Z",
        "alertThresholdExceeded": 0.8,
    }
    messages = [
        {
            "ackId": "ack-1",
            "message": {
                "data": base64.b64encode(json.dumps(payload).encode()).decode(),
                "publishTime": "2026-08-20T11:59:00Z",
                "messageId": "message-1",
                "attributes": {"budgetId": "gemini-budget"},
            },
        }
    ]
    status, ack_ids = collect_gemini(
        messages,
        {},
        {"GEMINI_BUDGET_MONITOR_CONFIGURED": "true", "GEMINI_BUDGET_ID": "gemini-budget"},
        now=NOW,
    )
    assert status["state"] == "ALERT"
    assert status["percent"] == 90.0
    assert status["source_publish_time"] == "2026-08-20T11:59:00Z"
    assert ack_ids == ["ack-1"]


def test_gemini_preserves_current_month_status_when_no_new_notification():
    previous = {
        "provider": "gemini",
        "state": "OK",
        "period": "2026-08",
        "percent": 35.0,
        "budget_id": "gemini-budget",
    }
    status, ack_ids = collect_gemini(
        [],
        previous,
        {"GEMINI_BUDGET_MONITOR_CONFIGURED": "true", "GEMINI_BUDGET_ID": "gemini-budget"},
        now=NOW,
    )
    assert status == previous
    assert ack_ids == []


def test_gemini_ignores_unrelated_or_malformed_messages_without_acknowledging():
    valid_other = {
        "costAmount": 99,
        "budgetAmount": 100,
        "costIntervalStart": "2026-08-01T00:00:00Z",
    }
    messages = [
        {
            "ackId": "ack-other",
            "message": {
                "data": base64.b64encode(json.dumps(valid_other).encode()).decode(),
                "attributes": {"budgetId": "other-budget"},
            },
        },
        {
            "ackId": "ack-malformed",
            "message": {
                "data": "not-base64",
                "attributes": {"budgetId": "gemini-budget"},
            },
        },
    ]
    status, ack_ids = collect_gemini(
        messages,
        {},
        {"GEMINI_BUDGET_MONITOR_CONFIGURED": "true", "GEMINI_BUDGET_ID": "gemini-budget"},
        now=NOW,
    )
    assert status["state"] == "UNKNOWN"
    assert ack_ids == []


def test_gemini_out_of_order_notification_cannot_regress_usage():
    previous = {
        "provider": "gemini",
        "state": "ALERT",
        "period": "2026-08",
        "percent": 90.0,
        "consumed": 90.0,
        "limit": 100.0,
        "unit": "USD",
        "budget_id": "gemini-budget",
        "source_publish_time": "2026-08-20T11:59:00Z",
    }
    older = {
        "costAmount": 50,
        "budgetAmount": 100,
        "costIntervalStart": "2026-08-01T00:00:00Z",
    }
    messages = [
        {
            "ackId": "ack-old",
            "message": {
                "data": base64.b64encode(json.dumps(older).encode()).decode(),
                "publishTime": "2026-08-20T10:00:00Z",
                "attributes": {"budgetId": "gemini-budget"},
            },
        }
    ]
    status, ack_ids = collect_gemini(
        messages,
        previous,
        {"GEMINI_BUDGET_MONITOR_CONFIGURED": "true", "GEMINI_BUDGET_ID": "gemini-budget"},
        now=NOW,
    )
    assert status == previous
    assert ack_ids == ["ack-old"]


def test_alert_delivery_is_once_per_master_provider_period_and_level():
    status = {
        "available": True,
        "checked_at": NOW.isoformat(),
        "period": "2026-08",
        "threshold_percent": 80,
        "providers": {
            "openai": {
                "provider": "openai",
                "period": "2026-08",
                "state": "ALERT",
                "percent": 82,
                "consumed": 82,
                "limit": 100,
                "unit": "USD",
            },
            "gemini": {"state": "OK"},
            "copilot": {"state": "UNKNOWN"},
        },
    }
    rows = alert_rows(status, now=NOW)
    assert [row["key"] for row in rows] == ["openai:2026-08:80"]
    state = {"deliveries": {}}
    assert pending_master_ids(state, rows[0]["key"], ["100", "200"]) == ["100", "200"]
    mark_delivered(state, rows[0]["key"], ["100"])
    assert pending_master_ids(state, rows[0]["key"], ["100", "200"]) == ["200"]


def test_stale_or_previous_month_status_never_emits_alerts():
    status = {
        "available": True,
        "checked_at": "2026-08-20T06:00:00+00:00",
        "period": "2026-08",
        "threshold_percent": 80,
        "providers": {
            "openai": {"provider": "openai", "period": "2026-08", "state": "ALERT", "percent": 90},
        },
    }
    assert status_freshness(status, now=NOW)[0] is False
    assert alert_rows(status, now=NOW) == []

    status["checked_at"] = NOW.isoformat()
    status["period"] = "2026-07"
    assert alert_rows(status, now=NOW) == []
