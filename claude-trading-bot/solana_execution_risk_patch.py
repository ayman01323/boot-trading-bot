"""Wires Claude-specific hard limits into the actual Solana LIVE execution path.

The wrapper sits immediately in front of SolanaLiveExecutor.buy/sell. New
entries must pass identity, signer, chain, position/exposure/count, daily-loss
and drawdown checks. Exits deliberately keep only identity/signing checks so a
risk stop can never trap capital in an existing position.

Drawdown is a persistent circuit breaker. Once the configured threshold is
reached, new entries are latched OFF and the wallet owner is notified on
Telegram. The halt survives process restarts and does not clear at midnight.
Only CLAUDE_BOT_WALLET_OWNER_ID may clear it with:

    /sibot1riskresume CONFIRM

Authorising a restart establishes a new realized-P&L drawdown baseline; it does
not change LIVE/ARM/AUTO, signer state, daily-loss limits, pool checks, canary
limits or any other execution control.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from learnerbot import solana_live_executor as _executor
from learnerbot import solana_sibot as _sol
from learnerbot import telegram as _telegram
from learnerbot import telegram_ui as _ui

import risk_engine_guard
import signing_interface

_SOL_MINT = "So11111111111111111111111111111111111111112"
_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
_SECONDS_PER_DAY = 86400
_STATE_FILENAME = "claude_drawdown_halt.json"
_STATE_LOCK = threading.RLock()

_original_buy = _executor.SolanaLiveExecutor.buy
_original_sell = _executor.SolanaLiveExecutor.sell
_PREV_HANDLE_UPDATE = _ui.handle_update


class ExecutionGuardError(RuntimeError):
    """Raised when a guarded call is refused. Never bypassable from outside this module."""


def _owner_id() -> str:
    return os.environ.get("CLAUDE_BOT_WALLET_OWNER_ID", "").strip()


def _sol_usd_price() -> Decimal:
    """Live SOL/USD price via Jupiter's public quote API; failure is fail-closed."""
    url = (
        "https://lite-api.jup.ag/swap/v1/quote?"
        f"inputMint={_SOL_MINT}&outputMint={_USDC_MINT}&amount=1000000000&slippageBps=50"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    out_amount = Decimal(str(data["outAmount"]))
    return out_amount / Decimal(1_000_000)


def _check_identity_and_signer(executor) -> None:
    owner_id = _owner_id()
    if not owner_id:
        raise ExecutionGuardError("CLAUDE_BOT_WALLET_OWNER_ID is not set")
    if str(executor.telegram_id) != owner_id:
        raise ExecutionGuardError(
            f"Executor identity {executor.telegram_id!r} does not match "
            f"CLAUDE_BOT_WALLET_OWNER_ID={owner_id!r} -- refusing to sign/broadcast "
            f"for an identity this bot did not explicitly authorise"
        )
    status = signing_interface.get_signer_status(executor.app)
    if not status.ready:
        raise ExecutionGuardError(f"Refusing to sign/broadcast: {status.reason}")


def _check_chain_authorised(chain: str) -> None:
    authorised = {c.strip().lower() for c in os.environ.get("AUTHORISED_CHAINS", "").split(",") if c.strip()}
    if chain.lower() not in authorised:
        raise ExecutionGuardError(
            f"Chain {chain!r} is not in AUTHORISED_CHAINS={sorted(authorised) or '(none)'} "
            f"-- no chain is authorised by default, the operator must set this explicitly"
        )


def _state_path(app) -> Path:
    return Path(app.data_dir) / _STATE_FILENAME


def _default_state() -> dict:
    return {
        "halted": False,
        "baseline_epoch": 0,
        "triggered_at": 0,
        "drawdown_pct": 0.0,
        "drawdown_usd": 0.0,
        "limit_pct": 0.0,
        "authorized_at": 0,
        "authorized_by": "",
    }


def _load_state(app) -> dict:
    path = _state_path(app)
    if not path.exists():
        return _default_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("state is not an object")
        state = _default_state()
        state.update(raw)
        state["halted"] = bool(state.get("halted"))
        state["baseline_epoch"] = max(0, int(state.get("baseline_epoch") or 0))
        return state
    except Exception:
        # A corrupt/missing-readable safety latch must fail closed, not silently
        # resume trading. The owner can explicitly authorize a fresh baseline.
        state = _default_state()
        state["halted"] = True
        state["state_error"] = "drawdown state unreadable/corrupt"
        return state


def _save_state(app, state: dict) -> None:
    path = _state_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, path)
    path.chmod(0o600)


def _telegram_token(app) -> str:
    return str(getattr(app, "telegram_bot_token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()


def _send(app, chat_id: str, text: str) -> None:
    token = _telegram_token(app)
    if not token or not str(chat_id).strip():
        return
    try:
        _telegram.send_message(token, str(chat_id), text, parse_mode="HTML", protect_content=True)
    except Exception as exc:
        print("[claude-drawdown-alert]", type(exc).__name__, str(exc)[:240])


def _latch_drawdown(app, breach: risk_engine_guard.DrawdownLimitBreached) -> bool:
    """Persist the halt. Return True only when this call created the first latch."""
    with _STATE_LOCK:
        state = _load_state(app)
        first = not bool(state.get("halted"))
        state.update(
            {
                "halted": True,
                "triggered_at": int(time.time()),
                "drawdown_pct": float(breach.drawdown_pct),
                "drawdown_usd": float(breach.drawdown_usd),
                "limit_pct": float(breach.limit_pct),
            }
        )
        _save_state(app, state)
        return first


def _authorize_restart(app, owner_id: str) -> dict:
    """Clear a latched stop and start a fresh drawdown measurement baseline."""
    now = int(time.time())
    with _STATE_LOCK:
        state = _load_state(app)
        state.update(
            {
                "halted": False,
                "baseline_epoch": now,
                "authorized_at": now,
                "authorized_by": str(owner_id),
                "triggered_at": 0,
                "drawdown_pct": 0.0,
                "drawdown_usd": 0.0,
                "limit_pct": 0.0,
            }
        )
        state.pop("state_error", None)
        _save_state(app, state)
        return state


def _current_live_exposure_sol(app, telegram_id: str) -> Decimal:
    with closing(_sol.connect(app)) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(entry_cost_sol), 0) AS total FROM positions "
            "WHERE telegram_id=? AND status='OPEN' AND mode='LIVE'",
            (str(telegram_id),),
        ).fetchone()
        return Decimal(str(row["total"] or 0))


def _current_live_open_count(app, telegram_id: str) -> int:
    with closing(_sol.connect(app)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE telegram_id=? AND status='OPEN' AND mode='LIVE'",
            (str(telegram_id),),
        ).fetchone()
        return int(row["n"])


def _realized_pnl_sol_today(app, telegram_id: str) -> Decimal:
    day_start = int(time.time() // _SECONDS_PER_DAY) * _SECONDS_PER_DAY
    with closing(_sol.connect(app)) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(realised_net_sol), 0) AS total FROM positions "
            "WHERE telegram_id=? AND status='CLOSED' AND mode='LIVE' AND closed_at >= ?",
            (str(telegram_id), day_start),
        ).fetchone()
        return Decimal(str(row["total"] or 0))


def _peak_to_current_drawdown_sol(app, telegram_id: str, *, since_epoch: int = 0) -> Decimal:
    """Current running-peak realized-P&L drawdown since the active risk baseline."""
    with closing(_sol.connect(app)) as conn:
        if int(since_epoch or 0) > 0:
            rows = conn.execute(
                "SELECT realised_net_sol FROM positions "
                "WHERE telegram_id=? AND status='CLOSED' AND mode='LIVE' AND closed_at >= ? "
                "ORDER BY closed_at ASC",
                (str(telegram_id), int(since_epoch)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT realised_net_sol FROM positions "
                "WHERE telegram_id=? AND status='CLOSED' AND mode='LIVE' ORDER BY closed_at ASC",
                (str(telegram_id),),
            ).fetchall()
    cumulative = Decimal(0)
    peak = Decimal(0)
    for row in rows:
        cumulative += Decimal(str(row["realised_net_sol"] or 0))
        peak = max(peak, cumulative)
    return peak - cumulative


def _guarded_buy(self, output_mint: str, amount_sol, reserve_sol) -> dict:
    _check_identity_and_signer(self)
    _check_chain_authorised("solana")

    state = _load_state(self.app)
    if state.get("halted"):
        raise ExecutionGuardError(
            "Drawdown circuit breaker is latched. New entries stay blocked until the wallet owner sends "
            "/sibot1riskresume CONFIRM. Exits remain available."
        )

    limits = risk_engine_guard.RiskLimits.load()
    price = _sol_usd_price()

    proposed_usd = float(Decimal(str(amount_sol)) * price)
    current_exposure_usd = float(_current_live_exposure_sol(self.app, self.telegram_id) * price)
    open_positions = _current_live_open_count(self.app, self.telegram_id)
    limits.check_new_position(
        proposed_usd=proposed_usd,
        current_exposure_usd=current_exposure_usd,
        open_positions=open_positions,
    )

    realized_pnl_usd_today = float(_realized_pnl_sol_today(self.app, self.telegram_id) * price)
    drawdown_usd = float(
        _peak_to_current_drawdown_sol(
            self.app,
            self.telegram_id,
            since_epoch=int(state.get("baseline_epoch") or 0),
        )
        * price
    )
    try:
        limits.check_daily_loss_and_drawdown(
            realized_pnl_usd_today=realized_pnl_usd_today,
            peak_to_current_drawdown_usd=drawdown_usd,
        )
    except risk_engine_guard.DrawdownLimitBreached as breach:
        first = _latch_drawdown(self.app, breach)
        if first:
            owner_id = _owner_id()
            _send(
                self.app,
                owner_id,
                "🛑 <b>20% DRAWDOWN CIRCUIT BREAKER</b>\n"
                f"Current realized drawdown: <b>{breach.drawdown_pct:.2f}%</b> "
                f"(${breach.drawdown_usd:.2f})\n"
                f"Configured limit: <b>{breach.limit_pct:.2f}%</b>\n\n"
                "New LIVE entries are now <b>HALTED</b>. Existing exits remain enabled so capital is not trapped.\n"
                "This stop does <b>not</b> reset automatically. Trading will restart only after the wallet owner explicitly authorises it with:\n"
                "<code>/sibot1riskresume CONFIRM</code>\n\n"
                "Authorisation creates a new drawdown baseline; all other risk, PoolCheck, LIVE/ARM/AUTO and signer controls remain unchanged.",
            )
        raise ExecutionGuardError(str(breach)) from breach

    return _original_buy(self, output_mint, amount_sol, reserve_sol)


def _guarded_sell(self, input_mint: str, amount_raw: int) -> dict:
    # Exits remain possible during a drawdown halt: reducing risk must never be
    # blocked by an entry-only circuit breaker.
    _check_identity_and_signer(self)
    return _original_sell(self, input_mint, amount_raw)


def _risk_status_text(app) -> str:
    state = _load_state(app)
    limits = risk_engine_guard.RiskLimits.load()
    if state.get("halted"):
        status = "🛑 HALTED — owner authorisation required"
    else:
        status = "🟢 ACTIVE"
    return (
        "<b>SiBot 1 — Risk Circuit Breaker</b>\n"
        f"Status: <b>{status}</b>\n"
        f"Drawdown limit: <b>{limits.max_drawdown_pct:.2f}%</b>\n"
        f"Maximum open positions: <b>{limits.max_open_positions}</b>\n"
        f"Maximum position: <b>${limits.max_position_usd:.2f}</b> "
        f"({limits.max_position_usd / limits.max_capital_usd * 100:.2f}% of capital)\n"
        f"Maximum total exposure: <b>${limits.max_total_exposure_usd:.2f}</b>\n"
        f"Active baseline epoch: <code>{int(state.get('baseline_epoch') or 0)}</code>\n"
        "Restart command after a drawdown halt: <code>/sibot1riskresume CONFIRM</code>"
    )


def handle_update(app, update):
    message = update.get("message") or {}
    text = str(message.get("text") or "").strip()
    if not text:
        return _PREV_HANDLE_UPDATE(app, update)
    parts = text.split()
    cmd = parts[0].lower().split("@", 1)[0]
    if cmd not in {"/sibot1riskresume", "/sibot1riskstatus"}:
        return _PREV_HANDLE_UPDATE(app, update)

    chat_id = str((message.get("chat") or {}).get("id") or "")
    sender_id = str((message.get("from") or {}).get("id") or chat_id)
    owner_id = _owner_id()
    if not owner_id or sender_id != owner_id:
        _send(app, chat_id, "❌ <b>Not authorised.</b> Only the configured wallet owner may control the drawdown restart latch.")
        return

    if cmd == "/sibot1riskstatus":
        try:
            _send(app, chat_id, _risk_status_text(app))
        except Exception as exc:
            _send(app, chat_id, f"❌ Risk status unavailable: <code>{type(exc).__name__}</code>")
        return

    if len(parts) != 2 or parts[1].upper() != "CONFIRM":
        _send(app, chat_id, "❌ To authorise restart use exactly: <code>/sibot1riskresume CONFIRM</code>")
        return

    state = _load_state(app)
    if not state.get("halted"):
        _send(app, chat_id, "ℹ️ No drawdown halt is active. No risk baseline was changed.")
        return

    _authorize_restart(app, owner_id)
    _send(
        app,
        chat_id,
        "✅ <b>DRAWDOWN RESTART AUTHORISED</b>\n"
        "The wallet owner has explicitly authorised new entries. A fresh drawdown baseline starts now.\n"
        "LIVE/ARM/AUTO and signer state were not changed; all normal execution and risk checks still apply.",
    )


def install() -> None:
    if not getattr(_executor.SolanaLiveExecutor, "_claude_risk_guard_installed", False):
        _executor.SolanaLiveExecutor.buy = _guarded_buy
        _executor.SolanaLiveExecutor.sell = _guarded_sell
        _executor.SolanaLiveExecutor._claude_risk_guard_installed = True
    if not getattr(_ui, "_claude_drawdown_restart_handler_installed", False):
        _ui.handle_update = handle_update
        _ui._claude_drawdown_restart_handler_installed = True
