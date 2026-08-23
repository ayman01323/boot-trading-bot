from __future__ import annotations

import csv
import hashlib
import math
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path("/var/tmp/boot/ai_cost_router.sqlite3")
DEFAULT_DAILY_BUDGET_USD = 5.0
DEFAULT_MONTHLY_BUDGET_USD = 100.0
DEFAULT_WARNING_PERCENT = 80.0
DEFAULT_MAX_PROVIDER_RETRIES = 2

ALL_ADVISERS = ("claude", "gemini", "deepseek", "copilot")
LEVEL_ADVISERS = {
    0: (),
    1: ("deepseek",),
    2: ("deepseek", "gemini"),
    3: ("gemini", "claude"),
    4: ALL_ADVISERS,
}
LEVEL_GPT_MODELS = {
    0: "",
    1: "gpt-5.6-luna",
    2: "gpt-5.6-luna",
    3: "gpt-5.6-terra",
    4: "gpt-5.6-sol",
}

# Guardrail estimates, not invoices. Every rate can be overridden with
# AI_COST_PRICE_<PROVIDER>_{INPUT|OUTPUT|CACHED_INPUT}_PER_MTOK.
_MODEL_RATES: tuple[tuple[str, float, float, float], ...] = (
    ("gpt-5.6-sol", 5.0, 30.0, 0.50),
    ("gpt-5.6-terra", 2.0, 12.0, 0.20),
    ("gpt-5.6-luna", 0.20, 1.20, 0.02),
    ("gpt-5-nano", 0.20, 1.20, 0.02),
    ("claude-sonnet-5", 2.0, 10.0, 0.20),
    ("claude-haiku", 1.0, 5.0, 0.10),
    ("gemini-3.7-flash", 0.75, 3.75, 0.075),
    ("gemini-3.5-flash-lite", 0.75, 3.75, 0.075),
    ("deepseek-v4-pro", 0.435, 0.87, 0.087),
    ("deepseek-v4-flash", 0.14, 0.28, 0.028),
    ("deepseek-chat", 0.14, 0.28, 0.028),
    ("deepseek-reasoner", 0.435, 0.87, 0.087),
)
_PROVIDER_FALLBACK_RATES = {
    "gpt": (2.0, 12.0, 0.20),
    "claude": (2.0, 10.0, 0.20),
    "gemini": (0.75, 3.75, 0.075),
    "deepseek": (0.14, 0.28, 0.028),
    "copilot": (0.0, 0.0, 0.0),
}

_CRITICAL_TERMS = (
    " live ", "armed", "capital", "risk limit", "risk threshold", "stop loss",
    "stop-loss", "slippage", "trade execution", "trading execution", "wallet",
    "signing", "private key", "seed phrase", "mnemonic", "withdraw",
    "transfer funds", "deploy", "deployment", "production trade", "security credential",
)
_LEVEL3_TERMS = (
    "architecture", "database", "schema migration", "websocket", "api integration",
    "authentication", "authorization", "concurrency", "thread", "queue", "service",
    "systemd", "workflow", "github action", "server", "rpc", "execution engine",
    "strategy logic",
)
_LEVEL2_TERMS = (
    "bug", "fix", "code", "feature", "integration", "refactor", "performance",
    "latency", "monitor", "alert", "telegram", "parser", "logic", "repository",
)
_MECHANICAL_PREFIXES = (
    "read ", "show ", "list ", "check ", "status ", "run test", "test ",
    "compile ", "git status", "git diff",
)

_DB_LOCK = threading.RLock()


@dataclass(frozen=True)
class BudgetTicket:
    call_id: str
    allowed: bool
    reason: str
    provider: str
    model: str
    estimated_usd: float
    estimated_input_tokens: int
    max_output_tokens: int


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except Exception:
        return int(default)


def _db_path() -> Path:
    return Path(os.environ.get("AI_COST_DB_PATH") or DEFAULT_DB_PATH)


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_cost_calls (
            call_id TEXT PRIMARY KEY,
            created_epoch INTEGER NOT NULL,
            finished_epoch INTEGER NOT NULL DEFAULT 0,
            day_key TEXT NOT NULL,
            month_key TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            task_kind TEXT NOT NULL DEFAULT '',
            route_level INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL,
            success INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            cached_input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_usd REAL NOT NULL DEFAULT 0,
            actual_usd REAL NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_cost_alerts (
            alert_key TEXT PRIMARY KEY,
            sent_epoch INTEGER NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_cost_day ON ai_cost_calls(day_key, provider)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_cost_month ON ai_cost_calls(month_key, provider)")
    return conn


def _period_keys(epoch: int | None = None) -> tuple[str, str]:
    tm = time.gmtime(epoch or time.time())
    return time.strftime("%Y-%m-%d", tm), time.strftime("%Y-%m", tm)


def _normalise_provider(provider: str) -> str:
    return str(provider or "").lower().strip()


def _price_key(provider: str, side: str) -> str:
    return f"AI_COST_PRICE_{provider.upper()}_{side.upper()}_PER_MTOK"


def price_per_mtok(provider: str, model: str) -> tuple[float, float, float]:
    provider = _normalise_provider(provider)
    model_l = str(model or "").lower().strip()
    default = _PROVIDER_FALLBACK_RATES.get(provider, (1.0, 5.0, 0.10))
    for prefix, inp, out, cached in _MODEL_RATES:
        if model_l.startswith(prefix):
            default = (inp, out, cached)
            break
    return (
        max(0.0, _env_float(_price_key(provider, "input"), default[0])),
        max(0.0, _env_float(_price_key(provider, "output"), default[1])),
        max(0.0, _env_float(_price_key(provider, "cached_input"), default[2])),
    )


def estimate_input_tokens(text: str) -> int:
    return max(1, int(math.ceil(len(str(text or "").encode("utf-8")) / 3.5)))


def estimate_cost_usd(
    provider: str,
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> float:
    inp_rate, out_rate, cached_rate = price_per_mtok(provider, model)
    total_input = max(0, int(input_tokens))
    cached = min(total_input, max(0, int(cached_input_tokens)))
    uncached = max(0, total_input - cached)
    cost = (uncached * inp_rate + cached * cached_rate + max(0, int(output_tokens)) * out_rate) / 1_000_000.0
    return round(max(0.0, cost), 8)


def _effective_spend_expr() -> str:
    return "CASE WHEN state='RESERVED' THEN estimated_usd ELSE actual_usd END"


def _spend(conn: sqlite3.Connection, *, day: str | None = None, month: str | None = None, provider: str | None = None) -> float:
    where: list[str] = []
    params: list[Any] = []
    if day:
        where.append("day_key=?")
        params.append(day)
    if month:
        where.append("month_key=?")
        params.append(month)
    if provider:
        where.append("provider=?")
        params.append(provider)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    row = conn.execute(f"SELECT COALESCE(SUM({_effective_spend_expr()}),0) AS total FROM ai_cost_calls{clause}", params).fetchone()
    return float(row["total"] or 0.0)


def _call_count(conn: sqlite3.Connection, *, day: str, provider: str | None = None) -> int:
    if provider:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM ai_cost_calls WHERE day_key=? AND provider=? AND state!='CANCELLED'",
            (day, provider),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM ai_cost_calls WHERE day_key=? AND state!='CANCELLED'",
            (day,),
        ).fetchone()
    return int(row["n"] or 0)


def _daily_budget() -> float:
    return max(0.0, _env_float("AI_COST_DAILY_BUDGET_USD", DEFAULT_DAILY_BUDGET_USD))


def _monthly_budget() -> float:
    return max(0.0, _env_float("AI_COST_MONTHLY_BUDGET_USD", DEFAULT_MONTHLY_BUDGET_USD))


def _provider_daily_budget(provider: str) -> float:
    return max(0.0, _env_float(f"AI_COST_{provider.upper()}_DAILY_BUDGET_USD", 0.0))


def max_provider_retries() -> int:
    return max(0, min(_env_int("AI_COST_MAX_PROVIDER_RETRIES", DEFAULT_MAX_PROVIDER_RETRIES), 5))


def _max_daily_calls(provider: str) -> int:
    defaults = {"gpt": 50, "claude": 40, "gemini": 100, "deepseek": 250, "copilot": 40}
    return max(0, _env_int(f"AI_COST_{provider.upper()}_MAX_DAILY_CALLS", defaults.get(provider, 100)))


def reserve_call(
    provider: str,
    model: str,
    prompt: str,
    *,
    max_output_tokens: int,
    task_kind: str = "",
    route_level: int = 0,
) -> BudgetTicket:
    provider = _normalise_provider(provider)
    model = str(model or "provider-default").strip() or "provider-default"
    input_tokens = estimate_input_tokens(prompt)
    estimated = estimate_cost_usd(provider, model, input_tokens=input_tokens, output_tokens=max(0, int(max_output_tokens)))
    call_id = f"ai-{int(time.time() * 1000)}-{secrets.token_hex(5)}"
    day, month = _period_keys()
    enforce = _env_bool("AI_COST_BUDGET_ENFORCE", True)

    with _DB_LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            daily = _spend(conn, day=day)
            monthly = _spend(conn, month=month)
            provider_daily = _spend(conn, day=day, provider=provider)
            provider_budget = _provider_daily_budget(provider)
            max_calls = _max_daily_calls(provider)
            provider_calls = _call_count(conn, day=day, provider=provider)

            reason = ""
            if max_calls and provider_calls >= max_calls:
                reason = f"{provider} daily call cap reached ({provider_calls}/{max_calls})"
            elif _daily_budget() and daily + estimated > _daily_budget():
                reason = f"AI daily budget would be exceeded (${daily:.4f} + ${estimated:.4f} > ${_daily_budget():.2f})"
            elif _monthly_budget() and monthly + estimated > _monthly_budget():
                reason = f"AI monthly budget would be exceeded (${monthly:.4f} + ${estimated:.4f} > ${_monthly_budget():.2f})"
            elif provider_budget and provider_daily + estimated > provider_budget:
                reason = f"{provider} daily budget would be exceeded (${provider_daily:.4f} + ${estimated:.4f} > ${provider_budget:.2f})"

            allowed = not (reason and enforce)
            if allowed:
                conn.execute(
                    """
                    INSERT INTO ai_cost_calls(
                        call_id, created_epoch, day_key, month_key, provider, model,
                        task_kind, route_level, state, estimated_usd
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        call_id, int(time.time()), day, month, provider, model,
                        str(task_kind or "")[:120], max(0, min(int(route_level or 0), 4)),
                        "RESERVED", estimated,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    return BudgetTicket(
        call_id=call_id,
        allowed=allowed,
        reason=reason if reason else "",
        provider=provider,
        model=model,
        estimated_usd=estimated,
        estimated_input_tokens=input_tokens,
        max_output_tokens=max(0, int(max_output_tokens)),
    )


def finish_call(
    ticket: BudgetTicket,
    *,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    success: bool,
    error: str = "",
) -> float:
    if not ticket.allowed:
        return 0.0
    have_usage = any(int(x or 0) > 0 for x in (input_tokens, cached_input_tokens, output_tokens))
    actual = (
        estimate_cost_usd(
            ticket.provider, ticket.model,
            input_tokens=int(input_tokens or 0),
            cached_input_tokens=int(cached_input_tokens or 0),
            output_tokens=int(output_tokens or 0),
        )
        if have_usage else (ticket.estimated_usd if success else 0.0)
    )
    with _DB_LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                UPDATE ai_cost_calls
                SET finished_epoch=?, state=?, success=?, input_tokens=?, cached_input_tokens=?,
                    output_tokens=?, actual_usd=?, error=?
                WHERE call_id=?
                """,
                (
                    int(time.time()), "COMPLETED" if success else "FAILED", 1 if success else 0,
                    max(0, int(input_tokens or 0)), max(0, int(cached_input_tokens or 0)),
                    max(0, int(output_tokens or 0)), actual, str(error or "")[:500], ticket.call_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    try:
        maybe_send_telegram_alerts()
    except Exception:
        pass
    return actual


def cancel_call(ticket: BudgetTicket, reason: str = "") -> None:
    if not ticket.allowed:
        return
    with _DB_LOCK:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE ai_cost_calls SET finished_epoch=?, state='CANCELLED', actual_usd=0, error=? WHERE call_id=?",
                (int(time.time()), str(reason or "")[:500], ticket.call_id),
            )
            conn.commit()
        finally:
            conn.close()


def usage_from_response(provider: str, body: Any) -> dict[str, int]:
    provider = _normalise_provider(provider)
    if not isinstance(body, dict):
        return {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    try:
        if provider == "gpt":
            usage = body.get("usage") or {}
            details = usage.get("input_tokens_details") or {}
            return {
                "input_tokens": int(usage.get("input_tokens") or 0),
                "cached_input_tokens": int(details.get("cached_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
            }
        if provider == "gemini":
            usage = body.get("usageMetadata") or {}
            return {
                "input_tokens": int(usage.get("promptTokenCount") or 0),
                "cached_input_tokens": int(usage.get("cachedContentTokenCount") or 0),
                "output_tokens": int(usage.get("candidatesTokenCount") or 0),
            }
        if provider == "claude":
            usage = body.get("usage") or {}
            cached = int(usage.get("cache_read_input_tokens") or 0)
            return {
                "input_tokens": int(usage.get("input_tokens") or 0) + cached,
                "cached_input_tokens": cached,
                "output_tokens": int(usage.get("output_tokens") or 0),
            }
        if provider == "deepseek":
            usage = body.get("usage") or {}
            return {
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "cached_input_tokens": int(usage.get("prompt_cache_hit_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
            }
    except Exception:
        pass
    return {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}


def route_request(
    request: str,
    *,
    hard_protected_reasons: list[str] | tuple[str, ...] | None = None,
    protected_reasons: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    text = " " + " ".join(str(request or "").lower().split()) + " "
    hard = list(hard_protected_reasons or [])
    protected = list(protected_reasons or [])
    if hard or protected or any(term in text for term in _CRITICAL_TERMS):
        level, reason = 4, "critical/protected subject matter requires the full council"
    elif any(term in text for term in _LEVEL3_TERMS):
        level, reason = 3, "architecture/production complexity requires strong engineering review"
    elif any(term in text for term in _LEVEL2_TERMS):
        level, reason = 2, "normal engineering work gets a cheap first pass plus a second opinion"
    elif any(text.strip().startswith(prefix) for prefix in _MECHANICAL_PREFIXES):
        level, reason = 0, "mechanical/read-only work should use deterministic execution with no AI call"
    else:
        level, reason = 1, "routine low-risk work uses the cheapest independent adviser"

    advisers = list(LEVEL_ADVISERS[level])
    return {
        "level": level,
        "reason": reason,
        "advisers": advisers,
        "gpt_model": LEVEL_GPT_MODELS[level],
        "full_council": level == 4,
        "model_calls_before_implementation": len(advisers) + (1 if level > 0 else 0),
    }


def master_change_route(
    request: str,
    *,
    hard_protected_reasons: list[str] | tuple[str, ...] | None = None,
    protected_reasons: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    route = route_request(
        request,
        hard_protected_reasons=hard_protected_reasons,
        protected_reasons=protected_reasons,
    )
    if int(route["level"]) == 0:
        route = {
            **route,
            "level": 1,
            "reason": "repository change request keeps GPT final authority; using the minimum Level 1 council",
            "advisers": list(LEVEL_ADVISERS[1]),
            "gpt_model": LEVEL_GPT_MODELS[1],
            "full_council": False,
            "model_calls_before_implementation": 2,
        }
    return route


def _snapshot_rows(conn: sqlite3.Connection, day: str, month: str) -> tuple[dict[str, Any], dict[str, Any]]:
    expr = _effective_spend_expr()
    day_rows = conn.execute(
        f"""
        SELECT provider, COUNT(*) calls, COALESCE(SUM({expr}),0) usd,
               COALESCE(SUM(input_tokens),0) input_tokens,
               COALESCE(SUM(output_tokens),0) output_tokens
        FROM ai_cost_calls WHERE day_key=? AND state!='CANCELLED'
        GROUP BY provider ORDER BY usd DESC
        """,
        (day,),
    ).fetchall()
    month_rows = conn.execute(
        f"""
        SELECT provider, COUNT(*) calls, COALESCE(SUM({expr}),0) usd
        FROM ai_cost_calls WHERE month_key=? AND state!='CANCELLED'
        GROUP BY provider ORDER BY usd DESC
        """,
        (month,),
    ).fetchall()
    return ({str(r["provider"]): dict(r) for r in day_rows}, {str(r["provider"]): dict(r) for r in month_rows})


def snapshot() -> dict[str, Any]:
    day, month = _period_keys()
    with _DB_LOCK:
        conn = _connect()
        try:
            daily = _spend(conn, day=day)
            monthly = _spend(conn, month=month)
            by_day, by_month = _snapshot_rows(conn, day, month)
        finally:
            conn.close()
    daily_budget = _daily_budget()
    monthly_budget = _monthly_budget()
    return {
        "day": day,
        "month": month,
        "daily_usd": round(daily, 6),
        "monthly_usd": round(monthly, 6),
        "daily_budget_usd": daily_budget,
        "monthly_budget_usd": monthly_budget,
        "daily_percent": round((daily / daily_budget * 100.0), 2) if daily_budget else 0.0,
        "monthly_percent": round((monthly / monthly_budget * 100.0), 2) if monthly_budget else 0.0,
        "by_provider_today": by_day,
        "by_provider_month": by_month,
        "warning_percent": max(1.0, min(_env_float("AI_COST_WARNING_PERCENT", DEFAULT_WARNING_PERCENT), 100.0)),
        "budget_enforced": _env_bool("AI_COST_BUDGET_ENFORCE", True),
    }


def _alert_candidates() -> list[dict[str, Any]]:
    snap = snapshot()
    threshold = float(snap["warning_percent"])
    candidates: list[dict[str, Any]] = []
    for scope, pct, spent, limit, period in (
        ("daily", snap["daily_percent"], snap["daily_usd"], snap["daily_budget_usd"], snap["day"]),
        ("monthly", snap["monthly_percent"], snap["monthly_usd"], snap["monthly_budget_usd"], snap["month"]),
    ):
        if not limit:
            continue
        crossed = 100 if pct >= 100.0 else (int(threshold) if pct >= threshold else 0)
        if crossed:
            candidates.append({
                "scope": scope,
                "threshold": crossed,
                "percent": pct,
                "spent_usd": spent,
                "limit_usd": limit,
                "period": period,
                "alert_key": f"{scope}:{period}:{crossed}",
            })
    return candidates


def pending_budget_alerts(*, mark: bool = False) -> list[dict[str, Any]]:
    candidates = _alert_candidates()
    if not candidates:
        return []
    pending: list[dict[str, Any]] = []
    with _DB_LOCK:
        conn = _connect()
        try:
            for item in candidates:
                seen = conn.execute("SELECT 1 FROM ai_cost_alerts WHERE alert_key=?", (item["alert_key"],)).fetchone()
                if seen:
                    continue
                pending.append(item)
                if mark:
                    conn.execute(
                        "INSERT OR IGNORE INTO ai_cost_alerts(alert_key,sent_epoch) VALUES(?,?)",
                        (item["alert_key"], int(time.time())),
                    )
            if mark:
                conn.commit()
        finally:
            conn.close()
    return pending


def mark_budget_alerts(alerts: list[dict[str, Any]]) -> None:
    if not alerts:
        return
    with _DB_LOCK:
        conn = _connect()
        try:
            for item in alerts:
                key = str(item.get("alert_key") or "")
                if key:
                    conn.execute(
                        "INSERT OR IGNORE INTO ai_cost_alerts(alert_key,sent_epoch) VALUES(?,?)",
                        (key, int(time.time())),
                    )
            conn.commit()
        finally:
            conn.close()


def _master_chat_ids() -> list[str]:
    explicit = str(os.environ.get("AI_COST_ALERT_CHAT_IDS") or "").strip()
    if explicit:
        return list(dict.fromkeys(x.strip() for x in explicit.split(",") if x.strip()))

    csv_dir = Path(os.environ.get("CSV_DIR") or (Path(__file__).resolve().parents[1] / "CSVbot"))
    path = csv_dir / "users.csv"
    if not path.exists():
        return []
    out: list[str] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                role = str(row.get("role") or "").upper().strip()
                status = str(row.get("status") or "ACTIVE").upper().strip()
                tid = str(row.get("telegram_id") or row.get("chat_id") or "").strip()
                if role == "MASTER" and status not in {"DISABLED", "BANNED", "SUSPENDED"} and tid:
                    out.append(tid)
    except Exception:
        return []
    return list(dict.fromkeys(out))


def maybe_send_telegram_alerts() -> None:
    if not _env_bool("AI_COST_TELEGRAM_ALERTS", True):
        return
    alerts = pending_budget_alerts(mark=False)
    if not alerts:
        return
    token = str(os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_ids = _master_chat_ids()
    if not token or not chat_ids:
        return
    from .telegram import send_to_chats

    lines = ["🚨 AI COST BUDGET WARNING"]
    for item in alerts:
        lines.append(
            f"{str(item['scope']).upper()}: ${float(item['spent_usd']):.4f} / "
            f"${float(item['limit_usd']):.2f} ({float(item['percent']):.1f}%)"
        )
    lines.append("The Cost Router will block new provider calls before configured hard limits are exceeded.")
    send_to_chats(token, chat_ids, "\n".join(lines), disable_notification=False)
    mark_budget_alerts(alerts)


def format_snapshot() -> str:
    snap = snapshot()
    lines = [
        "AI COST ROUTER",
        f"Today: ${snap['daily_usd']:.4f} / ${snap['daily_budget_usd']:.2f} ({snap['daily_percent']:.1f}%)",
        f"Month: ${snap['monthly_usd']:.4f} / ${snap['monthly_budget_usd']:.2f} ({snap['monthly_percent']:.1f}%)",
        f"Warning: {snap['warning_percent']:.0f}% | hard caps: {'ON' if snap['budget_enforced'] else 'OFF'}",
        "",
        "Today by agent:",
    ]
    rows = snap.get("by_provider_today") or {}
    if not rows:
        lines.append("- no metered calls yet")
    else:
        for provider, row in rows.items():
            lines.append(f"- {provider}: ${float(row.get('usd') or 0):.4f} | {int(row.get('calls') or 0)} calls")
    return "\n".join(lines)


def request_fingerprint(request: str) -> str:
    canonical = " ".join(str(request or "").lower().split())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
