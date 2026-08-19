from __future__ import annotations

import html
import threading
import time
from decimal import Decimal
from pathlib import Path

from . import sibot as _sibot
from . import solana_sibot as _sol
from . import telegram_ui as _ui
from .capital_dashboard import user_dashboard_data
from .config import load_chains, load_kv_scoped
from .telegram import send_message
from .user_registry import all_users, user_setting

_ORIGINAL_START_MENU_THREAD = _ui.start_menu_thread
_STARTED = False
_LOCK = threading.Lock()
_REPORT_LAST_SENT = {}
_LOSS_ACTIVE = set()

REPORT_ENABLED_KEY = "hourly_capital_alert_enabled"
REPORT_INTERVAL_KEY = "hourly_capital_alert_interval_minutes"
LOSS_ALERT_ENABLED_KEY = "live_loss_alert_enabled"
LOSS_ALERT_THRESHOLD_KEY = "live_loss_alert_threshold_pct"


def _bool(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y"}


def _dec(v, default="0"):
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal(str(default))


def _fmt_usd(v: Decimal) -> str:
    return f"${v:,.2f}"


def _fmt_native(v: Decimal) -> str:
    a = abs(v)
    if a >= 1:
        return f"{v:.5f}".rstrip("0").rstrip(".")
    return f"{v:.8f}".rstrip("0").rstrip(".") or "0"


def report_enabled(app, tid) -> bool:
    # Per-user reporting is deliberately opt-in. Existing explicit user values
    # continue to be honoured, but a missing row means OFF.
    return _bool(user_setting(app.csv_dir, tid, 0, REPORT_ENABLED_KEY, "false"), False)


def report_interval_minutes(app, tid) -> int:
    raw = user_setting(app.csv_dir, tid, 0, REPORT_INTERVAL_KEY, "60")
    try:
        value = int(Decimal(str(raw)))
    except Exception:
        value = 60
    return min(1440, max(5, value))


def loss_alert_enabled(app, tid) -> bool:
    return _bool(user_setting(app.csv_dir, tid, 0, LOSS_ALERT_ENABLED_KEY, "false"), False)


def loss_alert_threshold_pct(app, tid) -> Decimal:
    raw = user_setting(app.csv_dir, tid, 0, LOSS_ALERT_THRESHOLD_KEY, "10")
    value = _dec(raw, "10")
    return min(Decimal("95"), max(Decimal("1"), value))


def _reserve_for(app, tid, chain) -> Decimal:
    cfg = load_kv_scoped(Path(app.csv_dir) / "live_trading_settings.csv", chain.chain_id)
    raw = user_setting(app.csv_dir, tid, chain.chain_id, "min_native_gas_reserve", None)
    if raw is None:
        raw = cfg.get("min_native_gas_reserve", "0.005")
    return max(Decimal(0), _dec(raw, "0.005"))


def _min_trade_for(app, tid, chain) -> Decimal:
    try:
        return max(Decimal(0), _dec(_sibot.user_settings(app, tid, chain.chain_id).get("min_trade_native"), "0.0001"))
    except Exception:
        return Decimal("0.0001")


def build_hourly_capital_alert(app, tid) -> str:
    data = user_dashboard_data(app, tid)
    wallets = list(data.get("wallets") or [])
    active = next((w for w in wallets if _bool(w.get("active"), False)), wallets[0] if wallets else None)
    total = _dec(data.get("capital_usd"), 0)
    lines = [
        "<b>⏰ Scheduled Capital & Gas Check</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💰 Total priced capital  <b>{_fmt_usd(total)}</b>",
    ]
    if active:
        addr = str(active.get("address") or "")
        short = addr if len(addr) <= 18 else f"{addr[:8]}…{addr[-6:]}"
        lines.append(f"👛 Active wallet  <code>{html.escape(short)}</code>")
    else:
        lines += ["", "⚠️ <b>No trading wallet is configured.</b>"]
        return "\n".join(lines)

    chain_map = {c.chain_id: c for c in load_chains(app, enabled_only=True)}
    snaps = {int(s.get("chain_id")): s for s in active.get("chains", [])}
    warnings = []
    opportunity_without_capital = []

    for cid, chain in chain_map.items():
        snap = snaps.get(int(cid))
        if not snap:
            continue
        native = _dec(snap.get("native_balance"), 0)
        capital = _dec(snap.get("capital_usd"), 0)
        reserve = _reserve_for(app, tid, chain)
        min_trade = _min_trade_for(app, tid, chain)
        usable_native = max(Decimal(0), native - reserve)
        research = _sibot.ranking_rows(app, tid, chain.chain_id)
        profitable_wallets = len(research)

        if native < reserve:
            gas = "🔴 GAS BELOW RESERVE"
            warnings.append(f"{chain.name}: {native:f} {chain.native_symbol} < reserve {reserve:f}")
        elif reserve > 0 and native < reserve * Decimal("1.5"):
            gas = "🟠 GAS LOW"
            warnings.append(f"{chain.name}: gas balance is close to the configured reserve")
        else:
            gas = "🟢 gas OK"

        lines += [
            "",
            f"<b>🌐 {html.escape(chain.name)}</b>",
            f"💵 Capital  <b>{_fmt_usd(capital)}</b>",
            f"⛽ Native  <b>{_fmt_native(native)} {html.escape(chain.native_symbol)}</b>  •  {gas}",
            f"📤 Usable after gas reserve  <b>{_fmt_native(usable_native)} {html.escape(chain.native_symbol)}</b>",
            f"📈 Positive-profit wallets found  <b>{profitable_wallets}</b>",
        ]

        if profitable_wallets > 0 and usable_native < min_trade:
            opportunity_without_capital.append(
                f"{chain.name}: {profitable_wallets} positive-profit wallet(s) found, but usable native capital is below {min_trade:f} {chain.native_symbol}"
            )

    try:
        sol_rows = _sol.ranking_rows(app, tid)
    except Exception:
        sol_rows = []
    if sol_rows:
        lines += [
            "",
            "<b>🟣 Solana</b>",
            f"📈 Positive-profit wallets found  <b>{len(sol_rows)}</b>",
        ]

    if warnings:
        lines += ["", "<b>⚠️ GAS WARNINGS</b>"]
        lines.extend(f"▫️ {html.escape(x)}" for x in warnings[:8])
    if opportunity_without_capital:
        lines += ["", "<b>🚨 PROFIT EVIDENCE BUT NO USABLE CAPITAL</b>"]
        lines.extend(f"▫️ {html.escape(x)}" for x in opportunity_without_capital[:8])

    lines += [
        "",
        "<i>“Positive-profit wallets” means SiBot has measured positive historical P&L evidence on that chain; it is not a guarantee that the next copied trade will profit.</i>",
    ]
    return "\n".join(lines)


def scheduled_report_text(app, tid) -> str:
    # telegram_live_reporting_patch replaces the builder later in import order.
    # Normalise its legacy "Hourly" heading so custom per-user intervals remain true.
    text = build_hourly_capital_alert(app, tid)
    text = text.replace("⏰ Hourly Capital &amp; Gas Check — ALL CHAINS", "⏰ Scheduled Capital &amp; Gas Check — ALL CHAINS")
    text = text.replace("⏰ Hourly Capital & Gas Check — ALL CHAINS", "⏰ Scheduled Capital & Gas Check — ALL CHAINS")
    text = text.replace("⏰ Hourly Capital & Gas Check", "⏰ Scheduled Capital & Gas Check")
    interval = report_interval_minutes(app, tid)
    return text + f"\n\n<i>Personal schedule: every {interval} minute{'s' if interval != 1 else ''}.</i>"


def send_hourly_capital_alerts(app) -> dict:
    """Compatibility entry point: send the report only to users who opted in."""
    sent = 0
    failed = 0
    if not app.telegram_bot_token:
        return {"sent": 0, "failed": 0}
    for user in all_users(app.csv_dir, enabled_only=True):
        if str(user.get("status") or "").upper() != "ACTIVE":
            continue
        tid = str(user.get("telegram_id") or "").strip()
        if not tid or not report_enabled(app, tid):
            continue
        try:
            send_message(app.telegram_bot_token, tid, scheduled_report_text(app, tid), parse_mode="HTML")
            sent += 1
        except Exception as exc:
            failed += 1
            print(f"[scheduled-capital:{tid}] {type(exc).__name__}: {exc}")
    return {"sent": sent, "failed": failed}


def _short_asset(value) -> str:
    value = str(value or "")
    if len(value) <= 18:
        return value or "unknown"
    return f"{value[:8]}…{value[-6:]}"


def _live_loss_rows(app, tid, threshold: Decimal):
    rows = []
    chain_map = {int(c.chain_id): c for c in load_chains(app, enabled_only=False)}

    try:
        evm_positions = _sibot.position_rows(app, tid, open_only=True)
    except Exception:
        evm_positions = []
    for p in evm_positions:
        if str(p.get("mode") or "").upper() != "LIVE":
            continue
        pct = _dec(p.get("unrealised_pct"), "0")
        if pct > -threshold:
            continue
        cid = int(p.get("chain_id") or 0)
        chain = chain_map.get(cid)
        name = chain.name if chain else f"chain {cid}"
        asset = p.get("symbol") or p.get("token") or "token"
        pid = str(p.get("position_id") or f"evm:{cid}:{p.get('token')}")
        rows.append({
            "key": (str(tid), "evm", pid),
            "chain": name,
            "asset": _short_asset(asset),
            "pct": pct,
            "pending": bool(int(p.get("leader_exit_pending") or 0)),
        })

    try:
        sol_positions = _sol.position_rows(app, tid, open_only=True)
    except Exception:
        sol_positions = []
    for p in sol_positions:
        if str(p.get("mode") or "").upper() != "LIVE":
            continue
        pct = _dec(p.get("unrealised_pct"), "0")
        if pct > -threshold:
            continue
        mint = p.get("symbol") or p.get("mint") or "token"
        pid = str(p.get("position_id") or f"sol:{p.get('mint')}")
        rows.append({
            "key": (str(tid), "solana", pid),
            "chain": "Solana",
            "asset": _short_asset(mint),
            "pct": pct,
            "pending": bool(int(p.get("leader_exit_pending") or 0)),
        })
    return rows


def send_new_loss_alerts(app, tid) -> int:
    """Alert once per threshold crossing for real LIVE positions only."""
    global _LOSS_ACTIVE
    if not loss_alert_enabled(app, tid) or not app.telegram_bot_token:
        _LOSS_ACTIVE = {k for k in _LOSS_ACTIVE if k[0] != str(tid)}
        return 0

    threshold = loss_alert_threshold_pct(app, tid)
    rows = _live_loss_rows(app, tid, threshold)
    current = {r["key"] for r in rows}
    previous = {k for k in _LOSS_ACTIVE if k[0] == str(tid)}
    new_rows = [r for r in rows if r["key"] not in previous]

    # Re-arm a position only after it recovers above the user's threshold or closes.
    _LOSS_ACTIVE = {k for k in _LOSS_ACTIVE if k[0] != str(tid)} | current
    if not new_rows:
        return 0

    lines = [
        f"<b>🚨 LIVE LOSS ALERT — {threshold:g}% threshold</b>",
        "━━━━━━━━━━━━",
    ]
    for r in new_rows[:10]:
        state = " • ⏳ exit pending" if r["pending"] else ""
        lines.append(
            f"🔻 <b>{html.escape(r['chain'])}</b> • <code>{html.escape(r['asset'])}</code> • "
            f"P&amp;L <b>{r['pct']:+.2f}%</b>{state}"
        )
    lines += [
        "",
        "<i>This is a Telegram warning only. It does not change the configured stop-loss or submit an extra trade.</i>",
    ]
    send_message(app.telegram_bot_token, str(tid), "\n".join(lines), parse_mode="HTML")
    return len(new_rows)


def _process_user(app, tid, now_mono):
    # Periodic capital report: each user has an independent opt-in switch and interval.
    if report_enabled(app, tid):
        interval = report_interval_minutes(app, tid) * 60
        last = _REPORT_LAST_SENT.get(str(tid))
        if last is None:
            _REPORT_LAST_SENT[str(tid)] = now_mono
        elif now_mono - last >= interval:
            try:
                send_message(app.telegram_bot_token, str(tid), scheduled_report_text(app, tid), parse_mode="HTML")
                _REPORT_LAST_SENT[str(tid)] = now_mono
            except Exception as exc:
                print(f"[scheduled-capital:{tid}] {type(exc).__name__}: {exc}")
    else:
        _REPORT_LAST_SENT.pop(str(tid), None)

    try:
        send_new_loss_alerts(app, tid)
    except Exception as exc:
        print(f"[live-loss-alert:{tid}] {type(exc).__name__}: {exc}")


def _worker(app):
    """Small scheduler tick; actual report cadence remains independently per user."""
    while True:
        time.sleep(30.0)
        if not getattr(app, "telegram_bot_token", ""):
            continue
        now_mono = time.monotonic()
        try:
            users = all_users(app.csv_dir, enabled_only=True)
        except Exception as exc:
            print(f"[telegram-user-reports] {type(exc).__name__}: {exc}")
            continue
        for user in users:
            if str(user.get("status") or "").upper() != "ACTIVE":
                continue
            tid = str(user.get("telegram_id") or "").strip()
            if tid:
                _process_user(app, tid, now_mono)


def start_menu_thread(app):
    global _STARTED
    result = _ORIGINAL_START_MENU_THREAD(app)
    with _LOCK:
        if not _STARTED:
            _STARTED = True
            threading.Thread(target=_worker, args=(app,), daemon=True, name="telegram-user-report-alerts").start()
            print("[telegram-user-reports] per-user report schedule + LIVE loss alerts started")
    return result


def install():
    if getattr(_ui, "_hourly_capital_alert_installed", False):
        return
    _ui.start_menu_thread = start_menu_thread
    _ui._hourly_capital_alert_installed = True


install()
