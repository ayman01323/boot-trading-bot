from __future__ import annotations

import csv
import html
import json
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

from .config import load_chains

FINAL_STATES = {"TARGET_ACHIEVED", "DEADLINE", "STOPPED"}
SUCCESS_STATES = {"SUCCESS", "SUCCESS_FEE_PENDING"}


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _json(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _epoch(row: dict) -> int:
    try:
        return int(float(row.get("timestamp_epoch") or 0))
    except Exception:
        return 0


def _bool(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _float(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _service_active(name: str) -> bool:
    try:
        return subprocess.run(
            ["systemctl", "is-active", "--quiet", name],
            timeout=4,
            check=False,
        ).returncode == 0
    except Exception:
        return False


def _fmt_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _chain_symbols(app) -> dict[str, str]:
    out = {}
    try:
        for chain in load_chains(app, enabled_only=False):
            out[str(chain.slug).lower()] = str(chain.wrapped_base_symbol or chain.native_symbol or "native")
    except Exception:
        pass
    return out


def _opportunity_rows(csv_dir: Path) -> list[dict]:
    candidates = []
    seen = set()
    for path in (
        csv_dir / "auto" / "live_opportunities.csv",
        csv_dir / "live_opportunities.csv",
        csv_dir / "auto" / "full_power_opportunities.csv",
    ):
        for row in _rows(path):
            key = (
                row.get("route_id"),
                row.get("chain_slug"),
                row.get("route_path"),
                row.get("route_kind"),
            )
            if key in seen:
                continue
            seen.add(key)
            candidates.append(row)
    return candidates


def _expected_net(row: dict) -> float:
    for key in ("expected_net_base", "net_profit_base", "expected_user_net_base"):
        raw = row.get(key)
        if raw not in (None, ""):
            return _float(raw)
    gross = _float(row.get("expected_gross_profit_base"))
    gas = max(
        _float(row.get("gas_cost_base")),
        _float(row.get("estimated_gas_cost_base")),
        _float(row.get("gas_reserve_base")),
    )
    slip = _float(row.get("slippage_reserve_base"))
    fee = _float(row.get("profit_fee_base"))
    return gross - gas - slip - fee


def _best_opportunity(app, rows: list[dict]) -> str:
    if not rows:
        return "none"
    enabled = [r for r in rows if _bool(r.get("enabled"))]
    pool = enabled or rows
    row = max(pool, key=_expected_net)
    slug = str(row.get("chain_slug") or "?").upper()
    kind = str(row.get("route_kind") or row.get("behaviour") or "route").upper()
    net = _expected_net(row)
    symbols = _chain_symbols(app)
    symbol = symbols.get(slug.lower(), "native")
    source = "DIRECT" if str(row.get("wallet") or "").upper() == "DIRECT_MARKET" else "LEARNED"
    mark = "eligible" if _bool(row.get("enabled")) else "candidate"
    return f"{slug} {kind} [{source}/{mark}] ≈ {net:.8f} {symbol}"


def _recent_telemetry(csv_dir: Path, since: int) -> dict:
    sims = [r for r in _rows(csv_dir / "auto" / "auto_trade_simulations.csv") if _epoch(r) >= since]
    execs = [r for r in _rows(csv_dir / "auto" / "auto_trade_execution.csv") if _epoch(r) >= since]
    passed = sum(_bool(r.get("simulation_ok")) for r in sims)
    confirmed = sum(str(r.get("status") or "").upper() in SUCCESS_STATES for r in execs)
    rejects = Counter()
    for row in sims:
        if _bool(row.get("simulation_ok")):
            continue
        reason = str(row.get("reason") or "unknown").strip()
        if reason:
            rejects[reason[:110]] += 1
    return {
        "sims": len(sims),
        "passed": passed,
        "execs": len(execs),
        "confirmed": confirmed,
        "rejects": rejects.most_common(3),
    }


def _challenge_gas(csv_dir: Path, start_epoch: int, app) -> list[str]:
    if not start_epoch:
        return []
    symbols = _chain_symbols(app)
    totals = defaultdict(float)
    for row in _rows(csv_dir / "auto" / "auto_trade_execution.csv"):
        if _epoch(row) < start_epoch:
            continue
        if str(row.get("status") or "").upper() not in SUCCESS_STATES:
            continue
        slug = str(row.get("chain_slug") or "unknown").lower()
        totals[slug] += _float(row.get("gas_cost_base"))
    return [
        f"{slug.upper()} {amount:.8f} {symbols.get(slug, 'native')}"
        for slug, amount in sorted(totals.items())
        if amount > 0
    ]


def challenge_page(app) -> str:
    csv_dir = Path(app.csv_dir)
    now = int(time.time())
    state = _json(csv_dir / "auto" / "profit_challenge_status.json")
    adaptive = _json(csv_dir / "auto" / "adaptive_strategy_status.json")
    status = str(state.get("status") or "NOT_STARTED").upper()
    service = _service_active("boot-profit-challenge.service")

    start = int(state.get("start_epoch") or 0)
    deadline = int(state.get("deadline_epoch") or state.get("end_epoch") or 0)
    target = _float(state.get("target_usd"), 0.01)
    realised = _float(state.get("realised_user_net_usd"), 0.0)
    remaining = max(0.0, target - realised)
    elapsed = (now - start) if start else 0
    time_left = max(0, deadline - now) if deadline else 0
    progress_pct = min(100.0, (realised / target * 100.0) if target > 0 else 0.0)

    opps = _opportunity_rows(csv_dir)
    enabled = sum(_bool(r.get("enabled")) for r in opps)
    t = _recent_telemetry(csv_dir, now - 15 * 60)
    profile = str(adaptive.get("profile") or "waiting")
    stage = str(state.get("stage") or "-")
    successes = int(state.get("successful_trades") or 0)
    gas = _challenge_gas(csv_dir, start, app)

    if status == "TARGET_ACHIEVED":
        badge = "🏁 GOAL ACHIEVED"
    elif status == "RUNNING" and service:
        badge = "🟢 RUNNING"
    elif status == "RUNNING" and not service:
        badge = "⚠️ STATE SAYS RUNNING / SERVICE INACTIVE"
    elif status in FINAL_STATES:
        badge = f"⏹ {status}"
    else:
        badge = "⚪ NOT CONFIRMED STARTED"

    lines = [
        "<b>🎯 BOOT CHALLENGE CONTROL CENTRE</b>",
        "",
        f"Status: <b>{html.escape(badge)}</b>",
        f"Challenge service: <b>{'ACTIVE' if service else 'INACTIVE'}</b>",
        f"Target: <b>${target:.6f}</b>",
        f"Realised user net: <b>${realised:.6f}</b> ({progress_pct:.1f}%)",
        f"Remaining to goal: <b>${remaining:.6f}</b>",
        f"Successful closed trades: <b>{successes}</b>",
    ]
    if start:
        lines.append(f"Elapsed: <b>{_fmt_duration(elapsed)}</b>")
    if deadline and status == "RUNNING":
        lines.append(f"Time remaining: <b>{_fmt_duration(time_left)}</b>")

    lines += [
        "",
        "<b>🧠 Strategy</b>",
        f"Challenge stage: <b>{html.escape(stage)}</b>",
        f"Adaptive profile: <b>{html.escape(profile)}</b>",
        "",
        "<b>🔎 Current opportunity engine</b>",
        f"Current routes/candidates: <b>{len(opps)}</b> | eligible: <b>{enabled}</b>",
        f"Best current expected net: <b>{html.escape(_best_opportunity(app, opps))}</b>",
        "",
        "<b>🧪 Last 15 minutes</b>",
        f"Wallet simulations: <b>{t['sims']}</b> | passed: <b>{t['passed']}</b>",
        f"Execution records: <b>{t['execs']}</b> | confirmed successes: <b>{t['confirmed']}</b>",
    ]

    if t["rejects"]:
        lines.append("Top rejects:")
        for reason, count in t["rejects"]:
            lines.append(f"• {count}× {html.escape(reason)}")
    if gas:
        lines += ["", "<b>⛽ Recorded gas on successful challenge trades</b>"]
        lines.extend(f"• {html.escape(x)}" for x in gas[:5])

    lines += [
        "",
        "<i>Only realised closed-trade profit counts toward the $0.01 goal. The controller may broaden discovery, but it cannot increase capital/slippage, lower economic safety thresholds, or bypass final simulation/eth_call.</i>",
    ]
    return "\n".join(lines)


def challenge_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🔄 Refresh Challenge", "callback_data": "challenge:refresh"}],
            [
                {"text": "🛰 Opportunities", "callback_data": "menu:opportunities"},
                {"text": "🔥 Full Power", "callback_data": "menu:power"},
            ],
            [{"text": "⬅️ Menu", "callback_data": "menu:home"}],
        ]
    }


def install_challenge_control_patch() -> None:
    """Add a MASTER-only /challenge page without exposing shell access."""
    from . import telegram_ui as ui

    if getattr(ui, "_challenge_control_patch_installed", False):
        return

    original_menu_keyboard = ui.menu_keyboard
    original_handle_update = ui.handle_update

    def menu_keyboard(app=None, chat_id=None):
        kb = original_menu_keyboard(app, chat_id)
        try:
            if app is not None and chat_id is not None and ui._master(app, chat_id):
                rows = list((kb or {}).get("inline_keyboard") or [])
                insert_at = min(2, len(rows))
                rows.insert(insert_at, [{"text": "🎯 Challenge Control", "callback_data": "challenge:show"}])
                return {"inline_keyboard": rows}
        except Exception:
            pass
        return kb

    def _show(app, chat_id):
        ui._send(app, chat_id, challenge_page(app), challenge_keyboard())

    def handle_update(app, update):
        cb = update.get("callback_query") or {}
        data = str(cb.get("data") or "")
        if data.startswith("challenge:"):
            chat_id = (((cb.get("message") or {}).get("chat") or {}).get("id"))
            cqid = cb.get("id")
            if not ui._auth(app, chat_id):
                if cqid:
                    ui.answer_callback_query(app.telegram_bot_token, cqid, "Not authorised.")
                return
            if not ui._master(app, chat_id):
                if cqid:
                    ui.answer_callback_query(app.telegram_bot_token, cqid, "MASTER only")
                return
            if cqid:
                ui.answer_callback_query(app.telegram_bot_token, cqid)
            _show(app, chat_id)
            return

        message = update.get("message") or {}
        text = str(message.get("text") or "").strip()
        chat_id = (message.get("chat") or {}).get("id")
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        if command == "/challenge":
            if not ui._auth(app, chat_id):
                return
            try:
                ui._require_master(app, chat_id)
                _show(app, chat_id)
            except Exception as exc:
                ui._send(app, chat_id, f"❌ Challenge status failed: {html.escape(str(exc))}", ui.back_keyboard())
            return

        return original_handle_update(app, update)

    ui.menu_keyboard = menu_keyboard
    ui.handle_update = handle_update
    ui._challenge_control_patch_installed = True
