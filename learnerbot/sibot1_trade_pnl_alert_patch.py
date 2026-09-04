from __future__ import annotations

import html
import threading
import time
from decimal import Decimal
from urllib.parse import quote

from . import sibot1_live_bridge_patch as _base
from . import sibot1_solana_live_bridge_patch as _sol
from .trade_pnl_accounting import as_decimal, entry_cash_cost, exit_accounting

# Reporting/accounting-only patch for SiBot 1 confirmed LIVE trades.
# It does not change candidate selection, risk gates, signer authority, order size,
# PoolCheck, simulation, slippage/impact limits, or broadcast behaviour.

_TLS = threading.local()
_INSTALLED = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_alert_accounting(
  telegram_id TEXT NOT NULL,
  shadow_lot_id TEXT NOT NULL,
  chain TEXT NOT NULL,
  entry_cost_native TEXT,
  remaining_cost_native TEXT,
  realised_net_native TEXT NOT NULL DEFAULT '0',
  realised_cost_native TEXT NOT NULL DEFAULT '0',
  accounting_quality TEXT NOT NULL,
  entry_tx TEXT,
  last_exit_tx TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(telegram_id,shadow_lot_id)
);
"""


def _native_symbol(chain: str) -> str:
    return "SOL" if chain == "solana" else "ETH"


def _chain_title(chain: str) -> str:
    return "Solana" if chain == "solana" else "Base"


def _dexview_url(chain: str, asset: str) -> str:
    value = str(asset or "").strip()
    if not value:
        return ""
    slug = "solana" if chain == "solana" else "base"
    return f"https://www.dexview.com/{slug}/" + quote(value, safe="")


def _fmt_native(value, symbol: str, *, signed: bool = False) -> str:
    d = as_decimal(value)
    if d is None:
        return "unavailable"
    sign = "+" if signed else ""
    return f"{d:{sign}.9f} {symbol}"


def _fmt_pct(value) -> str:
    d = as_decimal(value)
    if d is None:
        return "unavailable"
    return f"{d:+.2f}%"


def _db_row(module, app, sql: str, params=()):
    with module._DB_LOCK:
        conn = module._db(app)
        try:
            conn.executescript(_SCHEMA)
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def _position_any(module, app, tid, lot_id):
    return _db_row(
        module,
        app,
        "SELECT * FROM positions WHERE telegram_id=? AND shadow_lot_id=?",
        (str(tid), str(lot_id or "")),
    )


def _attempt_row(module, app, key):
    return _db_row(
        module,
        app,
        "SELECT * FROM attempts WHERE attempt_key=?",
        (str(key),),
    ) or {}


def _account_row(module, app, tid, lot_id):
    return _db_row(
        module,
        app,
        "SELECT * FROM trade_alert_accounting WHERE telegram_id=? AND shadow_lot_id=?",
        (str(tid), str(lot_id or "")),
    )


def _save_entry_accounting(module, app, tid, lot_id, chain: str, entry_cost, tx: str) -> None:
    now = int(time.time())
    cost = as_decimal(entry_cost)
    quality = "WALLET_DELTA" if cost is not None and cost > 0 else "UNAVAILABLE"
    cost_text = str(cost) if quality == "WALLET_DELTA" else None
    with module._DB_LOCK:
        conn = module._db(app)
        try:
            conn.executescript(_SCHEMA)
            conn.execute(
                """INSERT INTO trade_alert_accounting(
                     telegram_id,shadow_lot_id,chain,entry_cost_native,remaining_cost_native,
                     realised_net_native,realised_cost_native,accounting_quality,entry_tx,last_exit_tx,
                     created_at,updated_at
                   ) VALUES(?,?,?,?,?,'0','0',?,?,?, ?,?)
                   ON CONFLICT(telegram_id,shadow_lot_id) DO UPDATE SET
                     chain=excluded.chain,
                     entry_cost_native=excluded.entry_cost_native,
                     remaining_cost_native=excluded.remaining_cost_native,
                     realised_net_native='0',
                     realised_cost_native='0',
                     accounting_quality=excluded.accounting_quality,
                     entry_tx=excluded.entry_tx,
                     last_exit_tx='',
                     updated_at=excluded.updated_at""",
                (
                    str(tid), str(lot_id or ""), str(chain), cost_text, cost_text,
                    quality, str(tx or ""), "", now, now,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def _apply_exit_accounting(
    module,
    app,
    tid,
    lot_id,
    *,
    sold_raw: int,
    raw_before: int,
    wallet_cash_change,
    tx: str,
):
    with module._DB_LOCK:
        conn = module._db(app)
        try:
            conn.executescript(_SCHEMA)
            row = conn.execute(
                "SELECT * FROM trade_alert_accounting WHERE telegram_id=? AND shadow_lot_id=?",
                (str(tid), str(lot_id or "")),
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            if str(data.get("accounting_quality") or "") != "WALLET_DELTA":
                return None
            calc = exit_accounting(
                remaining_cost_native=data.get("remaining_cost_native"),
                realised_net_native=data.get("realised_net_native"),
                realised_cost_native=data.get("realised_cost_native"),
                sold_raw=sold_raw,
                position_raw_before=raw_before,
                wallet_cash_change_native=wallet_cash_change,
            )
            if not calc:
                return None
            conn.execute(
                """UPDATE trade_alert_accounting
                   SET remaining_cost_native=?,realised_net_native=?,realised_cost_native=?,
                       last_exit_tx=?,updated_at=?
                   WHERE telegram_id=? AND shadow_lot_id=?""",
                (
                    str(calc["remaining_cost_native"]),
                    str(calc["realised_net_total_native"]),
                    str(calc["realised_cost_total_native"]),
                    str(tx or ""), int(time.time()), str(tid), str(lot_id or ""),
                ),
            )
            conn.commit()
            return calc
        finally:
            conn.close()


def _native_balance(module, chain: str, app, tid):
    try:
        if chain == "base":
            trader = module.LiveTrader(app, "base", telegram_id=tid)
            return as_decimal(trader.native_balance())
        ready, _detail, balance = module._signer_and_balance(app, tid)
        return as_decimal(balance) if ready else None
    except Exception:
        return None


def _requested_entry(module, chain: str, app, tid):
    try:
        if chain == "base":
            return as_decimal(module._fixed_entry_size(module.control(app, tid)))
        return as_decimal(module._entry_size(module.control(app, tid)))
    except Exception:
        return None


def _asset(candidate, position=None) -> str:
    candidate = candidate or {}
    position = position or {}
    return str(
        candidate.get("asset_out")
        or candidate.get("asset")
        or candidate.get("token")
        or position.get("mint")
        or position.get("token")
        or ""
    ).strip()


def _tx_from_attempt(module, app, key) -> str:
    row = _attempt_row(module, app, key)
    return str(row.get("tx_hash") or row.get("tx_signature") or "")


def _confirmation_marker(chain: str, action: str) -> str:
    return f"SiBot 1 {_chain_title(chain)} CANARY {action} confirmed"


def _wrap_notify(module, chain: str) -> None:
    original = module._notify
    if getattr(original, "_sibot1_pnl_alert_wrapped", False):
        return

    def wrapped(app, tid, text):
        body = str(text or "")
        ctx = getattr(_TLS, "execution", None)
        if (
            isinstance(ctx, dict)
            and ctx.get("module") is module
            and _confirmation_marker(chain, str(ctx.get("action") or "")) in body
        ):
            ctx["captured_confirmation"] = body
            return None
        return original(app, tid, text)

    wrapped._sibot1_pnl_alert_wrapped = True
    wrapped._sibot1_pnl_alert_original = original
    module._notify = wrapped


def _emit_buy(module, chain: str, app, tid, candidate, key, before_balance, captured: str) -> None:
    lot_id = str(candidate.get("shadow_lot_id") or "")
    after_balance = _native_balance(module, chain, app, tid)
    cost = entry_cash_cost(before_balance, after_balance)
    tx = _tx_from_attempt(module, app, key)
    pos = _position_any(module, app, tid, lot_id) or {}
    asset = _asset(candidate, pos)
    requested = _requested_entry(module, chain, app, tid)
    symbol = _native_symbol(chain)
    _save_entry_accounting(module, app, tid, lot_id, chain, cost, tx)

    lines = [
        f"🚀 <b>{html.escape(_confirmation_marker(chain, 'BUY'))}</b>",
        f"Engine: <b>{html.escape(str(candidate.get('engine_id') or 'unknown'))}</b>",
        f"Token: <code>{html.escape(asset or 'unknown')}</code>",
    ]
    if requested is not None:
        lines.append(f"Order size: <b>{_fmt_native(requested, symbol)}</b>")
    if cost is not None:
        lines.append(
            "Actual wallet cash outflow: "
            f"<b>{_fmt_native(cost, symbol)}</b> "
            "<i>(includes execution costs/account funding)</i>"
        )
    else:
        lines.append("Actual wallet cash outflow: <b>unavailable</b>")
    lines.append("Net P/L: <b>not realised — position remains open</b>")
    viewer = _dexview_url(chain, asset)
    if viewer:
        lines.append(f"DexView: <a href=\"{html.escape(viewer, quote=True)}\">open chart</a>")
    if tx:
        lines.append(f"TX: <code>{html.escape(tx)}</code>")
    elif captured:
        lines.append("TX: <b>confirmed by bridge; hash unavailable to alert formatter</b>")
    module._notify(app, tid, "\n".join(lines))


def _emit_sell(module, chain: str, app, tid, candidate, key, before_balance, pos_before, captured: str) -> None:
    lot_id = str(candidate.get("shadow_lot_id") or "")
    after_balance = _native_balance(module, chain, app, tid)
    tx = _tx_from_attempt(module, app, key)
    pos_after = _position_any(module, app, tid, lot_id) or {}
    raw_before = max(0, int((pos_before or {}).get("token_raw") or 0))
    raw_after = max(0, int(pos_after.get("token_raw") or 0))
    sold_raw = max(0, raw_before - raw_after)
    wallet_change = None
    if before_balance is not None and after_balance is not None:
        wallet_change = after_balance - before_balance

    calc = None
    if sold_raw > 0 and wallet_change is not None:
        calc = _apply_exit_accounting(
            module,
            app,
            tid,
            lot_id,
            sold_raw=sold_raw,
            raw_before=raw_before,
            wallet_cash_change=wallet_change,
            tx=tx,
        )

    asset = _asset(candidate, pos_before)
    symbol = _native_symbol(chain)
    reason = str(candidate.get("reason") or "strategy_exit")
    actual_pct = (
        Decimal(sold_raw) / Decimal(raw_before) * Decimal(100)
        if raw_before > 0 and sold_raw > 0
        else None
    )
    lines = [
        f"✅ <b>{html.escape(_confirmation_marker(chain, 'SELL'))}</b>",
        f"Reason: <code>{html.escape(reason)}</code>",
        f"Token: <code>{html.escape(asset or 'unknown')}</code>",
    ]
    if actual_pct is not None:
        lines.append(f"Sold: <b>{actual_pct:.2f}%</b> of tracked position")
    if wallet_change is not None:
        lines.append(
            "Wallet cash change on exit: "
            f"<b>{_fmt_native(wallet_change, symbol, signed=True)}</b>"
        )
    else:
        lines.append("Wallet cash change on exit: <b>unavailable</b>")

    if calc:
        lines.extend([
            f"Cost basis sold: <b>{_fmt_native(calc['cost_basis_native'], symbol)}</b>",
            f"Net P/L this sale: <b>{_fmt_native(calc['net_this_native'], symbol, signed=True)}</b> ({_fmt_pct(calc['net_pct_this'])})",
            f"Cumulative realised net P/L: <b>{_fmt_native(calc['realised_net_total_native'], symbol, signed=True)}</b> ({_fmt_pct(calc['net_pct_total'])})",
        ])
    else:
        acct = _account_row(module, app, tid, lot_id)
        if acct is None:
            detail = "entry predates this net-P/L accounting record"
        elif str(acct.get("accounting_quality") or "") != "WALLET_DELTA":
            detail = "entry wallet cash cost could not be proven"
        else:
            detail = "exit wallet cash delta or sold quantity could not be proven"
        lines.append(f"Net P/L: <b>unavailable</b> <i>({html.escape(detail)})</i>")

    viewer = _dexview_url(chain, asset)
    if viewer:
        lines.append(f"DexView: <a href=\"{html.escape(viewer, quote=True)}\">open chart</a>")
    if tx:
        lines.append(f"TX: <code>{html.escape(tx)}</code>")
    elif captured:
        lines.append("TX: <b>confirmed by bridge; hash unavailable to alert formatter</b>")
    module._notify(app, tid, "\n".join(lines))


def _wrap_execute(module, chain: str, action: str) -> None:
    attr = "_execute_entry" if action == "BUY" else "_execute_exit"
    original = getattr(module, attr)
    if getattr(original, "_sibot1_pnl_alert_wrapped", False):
        return

    def wrapped(app, tid, candidate, key):
        lot_id = str(candidate.get("shadow_lot_id") or "")
        pos_before = _position_any(module, app, tid, lot_id) if action == "SELL" else None
        before_balance = _native_balance(module, chain, app, tid)
        previous = getattr(_TLS, "execution", None)
        ctx = {
            "module": module,
            "chain": chain,
            "action": action,
            "captured_confirmation": "",
        }
        _TLS.execution = ctx
        try:
            result = original(app, tid, candidate, key)
        finally:
            if previous is None:
                try:
                    delattr(_TLS, "execution")
                except AttributeError:
                    pass
            else:
                _TLS.execution = previous

        captured = str(ctx.get("captured_confirmation") or "")
        if not captured:
            return result
        try:
            if action == "BUY":
                _emit_buy(module, chain, app, tid, candidate, key, before_balance, captured)
            else:
                _emit_sell(module, chain, app, tid, candidate, key, before_balance, pos_before or {}, captured)
        except Exception as exc:
            # A confirmed execution must never be hidden because reporting failed.
            fallback = captured + (
                "\nNet P/L alert enhancement: <b>unavailable</b>\n"
                f"Reporting error: <code>{html.escape(type(exc).__name__ + ': ' + str(exc)[:300])}</code>"
            )
            module._notify(app, tid, fallback)
        return result

    wrapped._sibot1_pnl_alert_wrapped = True
    wrapped._sibot1_pnl_alert_original = original
    setattr(module, attr, wrapped)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    for module, chain in ((_base, "base"), (_sol, "solana")):
        _wrap_notify(module, chain)
        _wrap_execute(module, chain, "BUY")
        _wrap_execute(module, chain, "SELL")
    _INSTALLED = True
    print(
        "[sibot1-trade-pnl-alerts] installed=true chains=base,solana "
        "buy_alert=true sell_alert=true net_pnl=wallet_delta_cost_basis safety_gates=unchanged"
    )


install()
