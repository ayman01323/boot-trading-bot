from __future__ import annotations

import argparse
import base64
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
DEFAULT_THRESHOLD_PERCENT = 80.0
OPENAI_API_BASE = "https://api.openai.com/v1"
GITHUB_API_BASE = "https://api.github.com"


def _now_utc(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _period(now: datetime) -> str:
    return now.strftime("%Y-%m")


def _number(value: Any, *, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 and math.isfinite(parsed) else default


def _usage_state(percent: float, threshold: float) -> str:
    if percent >= 100.0:
        return "EXHAUSTED"
    if percent >= threshold:
        return "ALERT"
    return "OK"


def _usage_status(
    provider: str,
    *,
    consumed: float,
    limit: float,
    unit: str,
    period: str,
    observed_at: str,
    threshold: float,
    source: str,
    detail: str,
) -> dict[str, Any]:
    if limit <= 0:
        return _unknown(provider, period, observed_at, "Configured allowance must be greater than zero.")
    percent = round((consumed / limit) * 100.0, 2)
    return {
        "provider": provider,
        "state": _usage_state(percent, threshold),
        "period": period,
        "consumed": round(consumed, 6),
        "limit": round(limit, 6),
        "unit": unit,
        "percent": percent,
        "threshold_percent": threshold,
        "source": source,
        "observed_at": observed_at,
        "detail": detail,
    }


def _unknown(provider: str, period: str, observed_at: str, detail: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "state": "UNKNOWN",
        "period": period,
        "consumed": None,
        "limit": None,
        "unit": "",
        "percent": None,
        "threshold_percent": DEFAULT_THRESHOLD_PERCENT,
        "source": "unavailable",
        "observed_at": observed_at,
        "detail": str(detail)[:500],
    }


def _http_json(
    url: str,
    *,
    token: str,
    headers: Mapping[str, str] | None = None,
    opener=urllib.request.urlopen,
) -> dict[str, Any]:
    request_headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "boot-trading-bot-provider-credit-monitor/1",
    }
    request_headers.update(dict(headers or {}))
    request = urllib.request.Request(url, headers=request_headers, method="GET")
    try:
        with opener(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
            message = str((json.loads(body).get("error") or {}).get("message") or body)
        except Exception:
            message = str(exc)
        raise RuntimeError(f"HTTP {exc.code}: {message[:300]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Provider returned a non-object JSON response")
    return payload


def collect_openai(
    environ: Mapping[str, str] | None = None,
    *,
    opener=urllib.request.urlopen,
    now: datetime | None = None,
) -> dict[str, Any]:
    env = environ or os.environ
    current = _now_utc(now)
    observed = current.isoformat()
    period = _period(current)
    token = str(env.get("OPENAI_ADMIN_KEY") or "").strip()
    if not token:
        return _unknown("openai", period, observed, "OPENAI_ADMIN_KEY is not configured.")

    try:
        configured_limit = _number(env.get("OPENAI_MONTHLY_BUDGET_USD"))
        if configured_limit is None:
            limit_payload = _http_json(
                f"{OPENAI_API_BASE}/organization/spend_limit",
                token=token,
                opener=opener,
            )
            cents = _number(limit_payload.get("threshold_amount"))
            configured_limit = None if cents is None else cents / 100.0
        if configured_limit is None or configured_limit <= 0:
            raise RuntimeError(
                "No positive OpenAI hard spend limit was returned; set OPENAI_MONTHLY_BUDGET_USD."
            )

        start = int(current.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
        end = int(current.timestamp()) + 1
        page: str | None = None
        spent = 0.0
        for _ in range(20):
            query: dict[str, Any] = {
                "start_time": start,
                "end_time": end,
                "bucket_width": "1d",
                "limit": 31,
            }
            if page:
                query["page"] = page
            payload = _http_json(
                f"{OPENAI_API_BASE}/organization/costs?{urllib.parse.urlencode(query)}",
                token=token,
                opener=opener,
            )
            for bucket in payload.get("data") or []:
                for result in (bucket or {}).get("results") or []:
                    amount = (result or {}).get("amount") or {}
                    currency = str(amount.get("currency") or "usd").lower()
                    if currency != "usd":
                        raise RuntimeError(f"Unsupported OpenAI cost currency: {currency}")
                    spent += _number(amount.get("value"), default=0.0) or 0.0
            if not payload.get("has_more"):
                break
            page = str(payload.get("next_page") or "").strip() or None
            if not page:
                raise RuntimeError("OpenAI costs response declared more pages without next_page")
        else:
            raise RuntimeError("OpenAI costs pagination exceeded the safety limit")

        return _usage_status(
            "openai",
            consumed=spent,
            limit=configured_limit,
            unit="USD",
            period=period,
            observed_at=observed,
            threshold=DEFAULT_THRESHOLD_PERCENT,
            source="openai-organization-costs",
            detail="Monthly organisation cost versus the OpenAI hard spend limit.",
        )
    except Exception as exc:
        return _unknown("openai", period, observed, f"OpenAI billing query failed: {type(exc).__name__}: {exc}")


def collect_copilot(
    environ: Mapping[str, str] | None = None,
    *,
    opener=urllib.request.urlopen,
    now: datetime | None = None,
) -> dict[str, Any]:
    env = environ or os.environ
    current = _now_utc(now)
    observed = current.isoformat()
    period = _period(current)
    token = str(env.get("COPILOT_BILLING_TOKEN") or "").strip()
    scope = str(env.get("COPILOT_BILLING_SCOPE") or "user").strip().lower()
    owner = str(env.get("COPILOT_BILLING_OWNER") or env.get("GITHUB_REPOSITORY_OWNER") or "").strip()
    mode = str(env.get("COPILOT_BILLING_MODE") or "ai_credit").strip().lower()
    limit_name = "COPILOT_MONTHLY_PREMIUM_REQUESTS" if mode == "premium_request" else "COPILOT_MONTHLY_AI_CREDITS"
    allowance = _number(env.get(limit_name))

    missing = []
    if not token:
        missing.append("COPILOT_BILLING_TOKEN")
    if not owner:
        missing.append("COPILOT_BILLING_OWNER")
    if allowance is None or allowance <= 0:
        missing.append(limit_name)
    if scope not in {"user", "organization"}:
        missing.append("COPILOT_BILLING_SCOPE=user|organization")
    if mode not in {"ai_credit", "premium_request"}:
        missing.append("COPILOT_BILLING_MODE=ai_credit|premium_request")
    if missing:
        return _unknown("copilot", period, observed, "Missing or invalid: " + ", ".join(missing) + ".")

    account_path = "users" if scope == "user" else "organizations"
    metric_path = "ai_credit" if mode == "ai_credit" else "premium_request"
    query = urllib.parse.urlencode({"year": current.year, "month": current.month})
    url = (
        f"{GITHUB_API_BASE}/{account_path}/{urllib.parse.quote(owner, safe='')}"
        f"/settings/billing/{metric_path}/usage?{query}"
    )
    try:
        payload = _http_json(
            url,
            token=token,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2026-03-10",
            },
            opener=opener,
        )
        consumed = sum(
            _number((row or {}).get("grossQuantity"), default=0.0) or 0.0
            for row in payload.get("usageItems") or []
        )
        unit = "AI credits" if mode == "ai_credit" else "premium requests"
        return _usage_status(
            "copilot",
            consumed=consumed,
            limit=float(allowance),
            unit=unit,
            period=period,
            observed_at=observed,
            threshold=DEFAULT_THRESHOLD_PERCENT,
            source=f"github-{scope}-{metric_path}-usage",
            detail=f"Monthly gross {unit} usage versus the configured account allowance.",
        )
    except Exception as exc:
        return _unknown("copilot", period, observed, f"GitHub billing query failed: {type(exc).__name__}: {exc}")


def _load_json(path: str | Path | None, default: Any) -> Any:
    if not path:
        return default
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return default


def _gemini_payloads(messages: Any) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for envelope in messages if isinstance(messages, list) else []:
        if not isinstance(envelope, dict):
            continue
        ack_id = str(envelope.get("ackId") or "").strip()
        message = envelope.get("message") or {}
        encoded = str(message.get("data") or "").strip()
        if not encoded:
            continue
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
            payload = json.loads(decoded)
        except Exception:
            continue
        if isinstance(payload, dict):
            attributes = message.get("attributes") if isinstance(message.get("attributes"), dict) else {}
            payloads.append(
                {
                    "payload": payload,
                    "ack_id": ack_id,
                    "message_id": str(message.get("messageId") or ""),
                    "publish_time": str(message.get("publishTime") or ""),
                    "budget_id": str(
                        attributes.get("budgetId")
                        or attributes.get("budget_id")
                        or payload.get("budgetId")
                        or payload.get("budget_id")
                        or ""
                    ).strip(),
                }
            )
    return payloads


def collect_gemini(
    messages: Any,
    previous: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], list[str]]:
    env = environ or os.environ
    current = _now_utc(now)
    observed = current.isoformat()
    period = _period(current)
    expected_budget_id = str(env.get("GEMINI_BUDGET_ID") or "").strip()
    if not expected_budget_id:
        return _unknown("gemini", period, observed, "GEMINI_BUDGET_ID is not configured."), []

    payloads = _gemini_payloads(messages)
    ack_ids: list[str] = []
    candidates: list[tuple[str, dict[str, Any]]] = []
    for envelope in payloads:
        if envelope.get("budget_id") != expected_budget_id:
            continue
        payload = envelope["payload"]
        consumed = _number(payload.get("costAmount"))
        limit = _number(payload.get("budgetAmount"))
        if consumed is None or limit is None or limit <= 0:
            continue
        if envelope.get("ack_id"):
            ack_ids.append(str(envelope["ack_id"]))
        payload_period = str(payload.get("costIntervalStart") or "")[:7]
        if payload_period and payload_period != period:
            continue
        candidates.append((str(envelope.get("publish_time") or payload.get("costIntervalStart") or ""), envelope))
    if candidates:
        source_publish_time, selected = sorted(candidates, key=lambda row: row[0])[-1]
        old = dict(previous or {})
        old_publish_time = str(old.get("source_publish_time") or "")
        if (
            old.get("provider") == "gemini"
            and old.get("period") == period
            and old.get("budget_id") == expected_budget_id
            and old.get("state") in {"OK", "ALERT", "EXHAUSTED"}
            and old_publish_time
            and source_publish_time
            and source_publish_time <= old_publish_time
        ):
            return old, ack_ids

        payload = selected["payload"]
        consumed = float(_number(payload.get("costAmount"), default=0.0) or 0.0)
        limit = float(_number(payload.get("budgetAmount"), default=0.0) or 0.0)
        currency = str(payload.get("currencyCode") or "USD").upper()
        status = _usage_status(
            "gemini",
            consumed=consumed,
            limit=limit,
            unit=currency,
            period=period,
            observed_at=observed,
            threshold=DEFAULT_THRESHOLD_PERCENT,
            source="google-cloud-budget-pubsub",
            detail="Gemini-scoped Google Cloud monthly budget estimate; billing data can be delayed.",
        )
        status["budget_id"] = expected_budget_id
        status["source_publish_time"] = source_publish_time
        status["source_message_id"] = str(selected.get("message_id") or "")
        return status, ack_ids

    old = dict(previous or {})
    if (
        old.get("provider") == "gemini"
        and old.get("period") == period
        and old.get("budget_id") == expected_budget_id
        and old.get("state") in {
        "OK",
        "ALERT",
        "EXHAUSTED",
        }
    ):
        return old, ack_ids

    configured = str(env.get("GEMINI_BUDGET_MONITOR_CONFIGURED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    detail = (
        "Awaiting the first current-month Google Cloud budget notification."
        if configured
        else "Google Cloud Gemini budget Pub/Sub monitoring is not configured."
    )
    return _unknown("gemini", period, observed, detail), ack_ids


def collect_status(
    environ: Mapping[str, str] | None = None,
    *,
    previous: Mapping[str, Any] | None = None,
    gemini_messages: Any = None,
    opener=urllib.request.urlopen,
    now: datetime | None = None,
) -> tuple[dict[str, Any], list[str]]:
    env = environ or os.environ
    current = _now_utc(now)
    previous_gemini = ((previous or {}).get("providers") or {}).get("gemini") or {}
    gemini, ack_ids = collect_gemini(gemini_messages or [], previous_gemini, env, now=current)
    providers = {
        "openai": collect_openai(env, opener=opener, now=current),
        "gemini": gemini,
        "copilot": collect_copilot(env, opener=opener, now=current),
    }
    complete = all(row.get("state") != "UNKNOWN" for row in providers.values())
    return (
        {
            "schema_version": SCHEMA_VERSION,
            "available": True,
            "checked_at": current.isoformat(),
            "period": _period(current),
            "threshold_percent": DEFAULT_THRESHOLD_PERCENT,
            "configuration_complete": complete,
            "providers": providers,
        },
        ack_ids,
    )


def markdown_summary(status: Mapping[str, Any]) -> str:
    lines = [
        "## AI provider credit monitor",
        "",
        "| Provider | State | Used | Limit | Percent | Source |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for name in ("openai", "gemini", "copilot"):
        row = ((status.get("providers") or {}).get(name) or {})
        consumed = "—" if row.get("consumed") is None else f"{row.get('consumed'):g} {row.get('unit') or ''}".strip()
        limit = "—" if row.get("limit") is None else f"{row.get('limit'):g} {row.get('unit') or ''}".strip()
        percent = "—" if row.get("percent") is None else f"{row.get('percent'):.2f}%"
        lines.append(
            f"| {name.title()} | {row.get('state', 'UNKNOWN')} | {consumed} | {limit} | {percent} | {row.get('source', 'unavailable')} |"
        )
    lines += ["", f"Configuration complete: **{'YES' if status.get('configuration_complete') else 'NO'}**"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect sanitised AI-provider credit usage status.")
    parser.add_argument("--previous")
    parser.add_argument("--gemini-messages")
    parser.add_argument("--output", required=True)
    parser.add_argument("--ack-ids-output")
    parser.add_argument("--markdown-output")
    args = parser.parse_args(argv)

    previous = _load_json(args.previous, {})
    messages = _load_json(args.gemini_messages, [])
    status, ack_ids = collect_status(previous=previous, gemini_messages=messages)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.ack_ids_output:
        Path(args.ack_ids_output).write_text("\n".join(ack_ids) + ("\n" if ack_ids else ""), encoding="utf-8")
    if args.markdown_output:
        Path(args.markdown_output).write_text(markdown_summary(status), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
