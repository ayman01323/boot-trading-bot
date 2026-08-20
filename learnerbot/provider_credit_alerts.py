from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Mapping


PROVIDER_LABELS = {
    "openai": "OpenAI",
    "gemini": "Gemini",
    "copilot": "GitHub Copilot",
}
MAX_STATUS_AGE_SECONDS = 3 * 60 * 60


def _now_utc(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def status_freshness(
    status: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
    max_age_seconds: int = MAX_STATUS_AGE_SECONDS,
) -> tuple[bool, str]:
    payload = status or {}
    current = _now_utc(now)
    if not payload.get("available"):
        return False, "status is unavailable"
    if str(payload.get("period") or "") != current.strftime("%Y-%m"):
        return False, "status is from a different billing period"
    raw_checked = str(payload.get("checked_at") or "").strip()
    try:
        checked = datetime.fromisoformat(raw_checked.replace("Z", "+00:00"))
        checked = _now_utc(checked)
    except (TypeError, ValueError):
        return False, "checked_at is missing or invalid"
    age = (current - checked).total_seconds()
    if age < -300:
        return False, "checked_at is in the future"
    if age > max(60, int(max_age_seconds)):
        return False, "status is stale"
    return True, "OK"


def alert_rows(status: Mapping[str, Any] | None, *, now: datetime | None = None) -> list[dict[str, Any]]:
    payload = status or {}
    fresh, _detail = status_freshness(payload, now=now)
    if not fresh:
        return []
    threshold = float(payload.get("threshold_percent") or 80.0)
    current_period = _now_utc(now).strftime("%Y-%m")
    rows: list[dict[str, Any]] = []
    for provider in ("openai", "gemini", "copilot"):
        item = dict(((payload.get("providers") or {}).get(provider) or {}))
        state = str(item.get("state") or "UNKNOWN").upper()
        if state not in {"ALERT", "EXHAUSTED"}:
            continue
        if str(item.get("period") or payload.get("period") or "") != current_period:
            continue
        level = 100 if state == "EXHAUSTED" else int(threshold)
        period = str(item.get("period") or payload.get("period") or "unknown")
        rows.append(
            {
                "provider": provider,
                "key": f"{provider}:{period}:{level}",
                "level": level,
                "period": period,
                "state": state,
                "text": alert_text(provider, item, threshold),
            }
        )
    return rows


def alert_text(provider: str, item: Mapping[str, Any], threshold: float = 80.0) -> str:
    label = PROVIDER_LABELS.get(provider, provider.title())
    state = str(item.get("state") or "ALERT").upper()
    percent = item.get("percent")
    consumed = item.get("consumed")
    limit = item.get("limit")
    unit = str(item.get("unit") or "units")
    if state == "EXHAUSTED":
        heading = f"🚨 {label} CREDIT LIMIT REACHED"
    else:
        heading = f"⚠️ {label} CREDIT WARNING"
    percent_text = "unknown" if percent is None else f"{float(percent):.2f}%"
    usage_text = "unavailable" if consumed is None or limit is None else f"{float(consumed):g} / {float(limit):g} {unit}"
    return (
        f"{heading}\n"
        f"Period: {item.get('period') or 'unknown'}\n"
        f"Used: {usage_text} ({percent_text})\n"
        f"Alert threshold: {float(threshold):g}%\n"
        "Action: replenish credit or reduce provider usage before automated reports stop."
    )


def status_html(status: Mapping[str, Any] | None) -> str:
    payload = status or {}
    if not payload.get("available"):
        return "<b>💳 AI PROVIDER CREDITS</b>\n\nNo provider-credit status has been published yet."
    lines = [
        "<b>💳 AI PROVIDER CREDITS</b>",
        "",
        f"Period: <code>{html.escape(str(payload.get('period') or 'unknown'))}</code>",
        f"Checked: <code>{html.escape(str(payload.get('checked_at') or 'unknown')[:32])}</code>",
        f"Alert threshold: <b>{float(payload.get('threshold_percent') or 80):g}%</b>",
    ]
    fresh, freshness_detail = status_freshness(payload)
    if not fresh:
        lines += ["", f"🚫 <b>STALE/INVALID:</b> {html.escape(freshness_detail)}", "Automatic alerts are paused until a fresh status is published."]
    icons = {"OK": "✅", "ALERT": "⚠️", "EXHAUSTED": "🚨", "UNKNOWN": "❔"}
    for provider in ("openai", "gemini", "copilot"):
        item = dict(((payload.get("providers") or {}).get(provider) or {}))
        state = str(item.get("state") or "UNKNOWN").upper()
        percent = item.get("percent")
        pct = "unknown" if percent is None else f"{float(percent):.2f}%"
        detail = html.escape(str(item.get("detail") or "")[:260])
        lines += [
            "",
            f"{icons.get(state, '❔')} <b>{PROVIDER_LABELS[provider]}</b>: {html.escape(state)} — {html.escape(pct)}",
        ]
        if detail:
            lines.append(detail)
    if not payload.get("configuration_complete"):
        lines += ["", "⚠️ <i>One or more billing monitors still needs configuration.</i>"]
    return "\n".join(lines)


def pending_master_ids(delivery_state: Mapping[str, Any], key: str, masters: list[str]) -> list[str]:
    delivered = {
        str(value)
        for value in (((delivery_state or {}).get("deliveries") or {}).get(key) or [])
        if str(value).strip()
    }
    return [str(master) for master in masters if str(master) not in delivered]


def mark_delivered(delivery_state: dict[str, Any], key: str, chat_ids: list[str]) -> dict[str, Any]:
    deliveries = delivery_state.setdefault("deliveries", {})
    current = {str(value) for value in deliveries.get(key) or [] if str(value).strip()}
    current.update(str(value) for value in chat_ids if str(value).strip())
    deliveries[key] = sorted(current)
    return delivery_state
