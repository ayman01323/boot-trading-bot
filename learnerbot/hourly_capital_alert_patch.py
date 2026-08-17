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
        "<b>⏰ Hourly Capital & Gas Check</b>",
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
            "🧪 <b>SHADOW only</b> — Solana LIVE capital is not used yet.",
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


def send_hourly_capital_alerts(app) -> dict:
    sent = 0
    failed = 0
    if not app.telegram_bot_token:
        return {"sent": 0, "failed": 0}
    for user in all_users(app.csv_dir, enabled_only=True):
        if str(user.get("status") or "").upper() != "ACTIVE":
            continue
        tid = str(user.get("telegram_id") or "").strip()
        if not tid:
            continue
        enabled = user_setting(app.csv_dir, tid, 0, "hourly_capital_alert_enabled", None)
        if enabled is not None and not _bool(enabled, True):
            continue
        try:
            send_message(app.telegram_bot_token, tid, build_hourly_capital_alert(app, tid), parse_mode="HTML")
            sent += 1
        except Exception as exc:
            failed += 1
            print(f"[hourly-capital:{tid}] {type(exc).__name__}: {exc}")
    return {"sent": sent, "failed": failed}


def _worker(app):
    interval = 3600
    next_due = time.monotonic() + interval
    while True:
        wait = max(1.0, next_due - time.monotonic())
        time.sleep(min(wait, 30.0))
        if time.monotonic() < next_due:
            continue
        try:
            current_app = type(app).load()
            cfg = current_app.telegram_settings()
            if _bool(cfg.get("hourly_capital_alert_enabled"), True):
                result = send_hourly_capital_alerts(current_app)
                print(f"[hourly-capital] sent={result['sent']} failed={result['failed']}")
        except Exception as exc:
            print(f"[hourly-capital] {type(exc).__name__}: {exc}")
        next_due = time.monotonic() + interval


def start_menu_thread(app):
    global _STARTED
    result = _ORIGINAL_START_MENU_THREAD(app)
    with _LOCK:
        if not _STARTED:
            _STARTED = True
            threading.Thread(target=_worker, args=(app,), daemon=True, name="hourly-capital-alert").start()
            print("[hourly-capital] 60-minute Telegram capital/gas reminder started")
    return result


def install():
    if getattr(_ui, "_hourly_capital_alert_installed", False):
        return
    _ui.start_menu_thread = start_menu_thread
    _ui._hourly_capital_alert_installed = True


install()
