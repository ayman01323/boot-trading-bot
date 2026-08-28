from __future__ import annotations

"""45-second presentation-only open-position reporting for the isolated learner.

This patch does not alter the 15-second Solana safety/exit monitor. It starts a
separate reporting thread after the existing Telegram/Solana workers are started.
"""

import html
import sys
import threading
import time
from collections import defaultdict
from decimal import Decimal
from urllib.parse import quote as urlquote

from . import solana_pool_risk_gate as _pool
from . import solana_sibot as _sol
from . import telegram as _tg
from . import telegram_ui as _ui
from . import telegram_usd_everywhere_patch as _usd

_REPORT_SECONDS = 45
_PREV_START_MENU = _ui.start_menu_thread
_START_LOCK = threading.Lock()
_STARTED = False

# Last successfully delivered NewPoll45 snapshot per position. Process-local on
# purpose: no trading state or wallet data is mutated by this presentation layer.
_PREVIOUS: dict[str, dict[str, Decimal]] = {}


def _d(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(str(default))


def _sol_usd(app) -> Decimal:
    try:
        global_map, by_chain, _ = _usd._price_maps(app)
        price = (by_chain.get("solana") or {}).get("SOL")
        if price is None:
            price = global_map.get("SOL")
        return max(Decimal(0), _d(price, 0))
    except Exception:
        return Decimal(0)


def _usd_text(value: Decimal) -> str:
    try:
        return _usd._fmt_usd(_d(value))
    except Exception:
        return "USD unavailable"


def _sol_usd_pair(sol_value: Decimal, sol_price: Decimal) -> str:
    sol_value = _d(sol_value)
    if sol_price > 0:
        return f"{sol_value:,.9f} SOL (≈ {_usd_text(sol_value * sol_price)})"
    return f"{sol_value:,.9f} SOL (USD unavailable)"


def _pool_snapshot(mint: str, cfg: dict, sol_price: Decimal) -> dict[str, Decimal | bool]:
    """Read current DexScreener pool depth through the existing shared cache."""
    try:
        encoded = urlquote(str(mint), safe="")
        ttl = float(max(15, _sol._int(cfg.get("live_pool_dex_cache_seconds"), 60)))
        pairs, cached = _pool._fetch_json(
            "dexscreener",
            _pool._DEX_URL.format(mint=encoded),
            str(mint),
            ttl,
            _pool._timeout(cfg),
        )
        if not isinstance(pairs, list):
            raise ValueError("invalid DexScreener pool response")
        pairs = [
            p for p in pairs
            if isinstance(p, dict)
            and str(p.get("chainId") or "solana").lower() == "solana"
        ]
        total_usd = sum((_pool._liq_usd(p) for p in pairs), Decimal(0))
        sol_quote = sum(
            (
                max(
                    Decimal(0),
                    _d((p.get("liquidity") or {}).get("quote"), 0),
                )
                for p in pairs
                if str((p.get("quoteToken") or {}).get("address") or "") == _sol.WSOL_MINT
            ),
            Decimal(0),
        )
        sol_equiv = total_usd / sol_price if total_usd > 0 and sol_price > 0 else Decimal(0)
        return {
            "available": True,
            "usd": total_usd,
            "sol_equiv": sol_equiv,
            "sol_quote": sol_quote,
            "cached": bool(cached),
        }
    except Exception:
        return {
            "available": False,
            "usd": Decimal(0),
            "sol_equiv": Decimal(0),
            "sol_quote": Decimal(0),
            "cached": False,
        }


def _age_text(entry_ts) -> str:
    seconds = max(0, int(time.time()) - int(_d(entry_ts, 0)))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {mins}m"
    if hours:
        return f"{hours}h {mins}m {secs}s"
    return f"{mins}m {secs}s"


def _signed_pct(value: Decimal) -> str:
    return f"{_d(value):+.2f}%"


def _position_section(app, position: dict, index: int, total: int, cfg: dict, sol_price: Decimal):
    pid = str(position.get("position_id") or "")
    mint = str(position.get("mint") or "")
    current_pct = _d(position.get("unrealised_pct"), 0)
    net_sol = _d(position.get("unrealised_net_sol"), 0)
    exit_sol = _d(position.get("current_exit_sol"), 0)
    entry_cost = _d(position.get("entry_cost_sol"), 0)
    peak_pct = _d(position.get("peak_unrealised_pct"), 0)
    pool = _pool_snapshot(mint, cfg, sol_price)
    previous = _PREVIOUS.get(pid)

    icon = "🟢" if net_sol > 0 else "🔴" if net_sol < 0 else "⚪"
    lines = [
        f"<b>Open Position {index} of {total}</b>",
        f"🪙 Mint: <code>{html.escape(mint)}</code>",
        f"💼 Entry cost: <b>{_sol_usd_pair(entry_cost, sol_price)}</b>",
        f"💱 Current exit value: <b>{_sol_usd_pair(exit_sol, sol_price)}</b>",
        (
            f"{icon} Since open: <b>{_signed_pct(current_pct)}</b> • "
            f"<b>{net_sol:+.9f} SOL</b>"
            + (f" (≈ {_usd_text(net_sol * sol_price)})" if sol_price > 0 else " (USD unavailable)")
        ),
        f"📈 Peak since open: <b>{_signed_pct(peak_pct)}</b>",
    ]

    if previous is None:
        lines.append("🔁 Since previous NewPoll45: <b>first poll baseline</b>")
    else:
        delta_pct = current_pct - previous.get("pct", Decimal(0))
        delta_net = net_sol - previous.get("net_sol", Decimal(0))
        lines.append(
            f"🔁 Since previous NewPoll45: <b>{delta_pct:+.2f} percentage points</b> • "
            f"<b>{delta_net:+.9f} SOL</b>"
            + (f" (≈ {_usd_text(delta_net * sol_price)})" if sol_price > 0 else " (USD unavailable)")
        )

    if bool(pool.get("available")):
        pool_usd = _d(pool.get("usd"), 0)
        pool_sol = _d(pool.get("sol_equiv"), 0)
        sol_quote = _d(pool.get("sol_quote"), 0)
        if sol_price > 0:
            lines.append(
                f"💧 Pool liquidity: <b>≈ {pool_sol:,.4f} SOL-equivalent</b> • "
                f"<b>{_usd_text(pool_usd)}</b>"
            )
        else:
            lines.append(f"💧 Pool liquidity: <b>SOL-equivalent unavailable</b> • <b>{_usd_text(pool_usd)}</b>")
        if sol_quote > 0:
            lines.append(f"🌊 SOL-quoted pool depth: <b>{sol_quote:,.4f} SOL</b>")
        if previous is not None and "pool_usd" in previous:
            prior_pool = previous.get("pool_usd", Decimal(0))
            delta_pool = pool_usd - prior_pool
            if prior_pool > 0:
                delta_pool_pct = delta_pool * Decimal(100) / prior_pool
                lines.append(
                    f"💧 Pool Δ vs previous NewPoll45: <b>{delta_pool_pct:+.2f}%</b> • "
                    f"<b>{_usd_text(delta_pool)}</b>"
                )
    else:
        lines.append("💧 Pool liquidity: <b>temporarily unavailable</b>")

    lines += [
        f"⏱ Open for: <b>{_age_text(position.get('entry_ts'))}</b>",
        f'🔎 <a href="https://www.dexview.com/solana/{html.escape(mint, quote=True)}">DEX Viewer</a>',
    ]

    snapshot = {
        "pct": current_pct,
        "net_sol": net_sol,
        "exit_sol": exit_sol,
    }
    if bool(pool.get("available")):
        snapshot["pool_usd"] = _d(pool.get("usd"), 0)
    return "\n".join(lines), pid, snapshot


def _open_positions(app) -> list[dict]:
    try:
        with _sol._DB_LOCK, _sol.connect(app) as conn:
            rows = conn.execute(
                """SELECT * FROM positions
                   WHERE status='OPEN' AND mode='LIVE'
                   ORDER BY telegram_id,entry_ts,position_id"""
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def _emit(app) -> None:
    positions = _open_positions(app)
    live_ids = {str(p.get("position_id") or "") for p in positions}
    for stale in list(_PREVIOUS):
        if stale not in live_ids:
            _PREVIOUS.pop(stale, None)
    if not positions:
        return

    grouped: dict[str, list[dict]] = defaultdict(list)
    for p in positions:
        tid = str(p.get("telegram_id") or "")
        if tid:
            grouped[tid].append(p)

    cfg = _sol.settings(app)
    sol_price = _sol_usd(app)

    for tid, rows in grouped.items():
        sections = []
        snapshots = []
        total = len(rows)
        for index, position in enumerate(rows, start=1):
            section, pid, snapshot = _position_section(app, position, index, total, cfg, sol_price)
            sections.append(section)
            snapshots.append((pid, snapshot))

        text = "\n".join([
            "📡 <b>LEARNER POSITION UPDATE — NewPoll45</b>",
            "🔒 <b>LEARNER ONLY • GOOGLE TEST</b>",
            f"⏱ Report: <b>45s</b> • safety/exit monitor remains <b>{html.escape(str(cfg.get('position_poll_seconds', '15')))}s</b>",
            "━━━━━━━━━━━━",
            "",
            "\n\n━━━━━━━━━━━━\n\n".join(sections),
        ])
        try:
            _tg.send_message(
                app.telegram_bot_token,
                tid,
                text,
                parse_mode="HTML",
                protect_content=True,
                disable_notification=True,
            )
        except Exception as exc:
            print("[learner-newpoll45] send", type(exc).__name__, exc, flush=True)
            continue

        # "Previous poll" means previous successfully delivered NewPoll45.
        for pid, snapshot in snapshots:
            if pid:
                _PREVIOUS[pid] = snapshot


def _report_worker(app) -> None:
    # Let the normal workers populate the first current-exit valuation before the
    # first presentation report.
    time.sleep(_REPORT_SECONDS)
    while True:
        started = time.monotonic()
        try:
            _emit(app)
        except Exception as exc:
            print("[learner-newpoll45]", type(exc).__name__, exc, flush=True)
        elapsed = time.monotonic() - started
        time.sleep(max(1.0, _REPORT_SECONDS - elapsed))


def _start_reporter(app) -> None:
    global _STARTED
    with _START_LOCK:
        if _STARTED:
            return
        _STARTED = True
    threading.Thread(
        target=_report_worker,
        args=(app,),
        daemon=True,
        name="learner-newpoll45-position-reporter",
    ).start()
    print(
        "[learner-newpoll45] reporting=45s safety_monitor=unchanged pool=dexscreener usd=enabled",
        flush=True,
    )


def start_menu_thread(app):
    result = _PREV_START_MENU(app)
    _start_reporter(app)
    return result


def install() -> None:
    if getattr(_ui, "_learner_newpoll45_installed", False):
        return
    _ui.start_menu_thread = start_menu_thread

    # cli imports start_menu_thread by value. On this heavily patched runtime the
    # cli module may already have been imported before this final isolated-only
    # presentation patch is installed, so update that cached symbol as well. This
    # is presentation/thread startup only; no trading or Solana monitor hook is
    # replaced.
    cli_mod = sys.modules.get("learnerbot.cli")
    if cli_mod is not None:
        setattr(cli_mod, "start_menu_thread", start_menu_thread)

    _ui._learner_newpoll45_installed = True


install()
