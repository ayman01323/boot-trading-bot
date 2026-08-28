from __future__ import annotations

import hashlib
import time
from contextlib import closing
from decimal import Decimal

from . import solana_sibot as _sol
from .solana_live_executor import SolanaLiveError, SolanaLiveExecutor, SolanaLivePostExecutionError
from .telegram import send_message
from .user_registry import all_users, set_user_setting, user_bool, user_setting

# LIVE execution defaults. These controls deliberately fail closed around landed
# transactions whose economic result cannot be proved.
_sol.DEFAULTS.update({
    "live_trade_sol": ("0.009", "Real SOL amount per guarded Solana LIVE copied BUY (fixed; see live_limits)"),
    "live_min_sol_reserve": ("0.02", "SOL that must remain untouched for fees and emergency exits"),
    "live_max_positions": ("1", "Maximum simultaneous Solana LIVE positions per Telegram user"),
    "live_require_simulation": ("true", "Require successful Solana simulation before Jupiter execute"),
    "live_require_execute_output": ("true", "Require positive Jupiter executed input and output after LIVE submission"),
    "live_require_swap_events": ("true", "Require Jupiter swapEvents after a successful LIVE execution"),
    "live_no_output_disable_after": ("2", "Disable Solana LIVE after this many landed-but-invalid executions"),
    "live_min_economic_trade_sol": ("0.009", "Reject configured LIVE buys below this economic minimum instead of silently upsizing"),
    "live_max_observed_overhead_pct": ("35", "Reject new LIVE buys when recent actual SOL overhead exceeds this percentage of trade size"),
})

_LIVE_SAFETY_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS live_execution_attempts(
  attempt_key TEXT PRIMARY KEY,
  telegram_id TEXT NOT NULL,
  leader_wallet TEXT NOT NULL,
  leader_signature TEXT NOT NULL,
  mint TEXT NOT NULL,
  action TEXT NOT NULL,
  status TEXT NOT NULL,
  tx_signature TEXT,
  input_raw TEXT,
  output_raw TEXT,
  wallet_before_lamports TEXT,
  wallet_after_lamports TEXT,
  wallet_delta_lamports TEXT,
  error TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sol_live_attempts_user ON live_execution_attempts(telegram_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sol_live_attempts_status ON live_execution_attempts(telegram_id,status,created_at DESC);
"""


def live_enabled(app, telegram_id) -> bool:
    return user_bool(app.csv_dir, telegram_id, _sol.SOLANA_CHAIN_ID, "solana_live_enabled", False)


# Fixed LIVE Solana trade size. Every guarded LIVE copied BUY spends exactly this
# amount of SOL. It is deliberately not configurable and not reducible by a
# per-user override: a smaller size cannot reliably cover Solana execution
# overhead, and a larger size is a separate risk decision that must go through
# review rather than a settings edit.
LIVE_TRADE_SOL_FIXED = Decimal("0.009")


def live_limits(app, telegram_id, cfg=None):
    """Return the fixed LIVE trade amount and the effective untouched SOL reserve.

    Trade size is fixed at ``LIVE_TRADE_SOL_FIXED`` (0.009 SOL) for every user.
    ``live_trade_sol`` and any per-user ``solana_live_trade_sol`` override no
    longer change it. The reserve still honours a per-user override, floored at
    0.005 SOL.
    """
    cfg = dict(cfg or _sol.settings(app))
    base_reserve = _sol._dec(cfg.get("live_min_sol_reserve"), ".02")
    reserve_override = user_setting(
        app.csv_dir, telegram_id, _sol.SOLANA_CHAIN_ID, "solana_live_min_reserve_sol", None
    )
    trade = LIVE_TRADE_SOL_FIXED
    if reserve_override is None:
        reserve = max(Decimal("0.01"), base_reserve)
    else:
        reserve = max(Decimal("0.005"), _sol._dec(reserve_override, base_reserve))
    return trade, reserve


def _notify(app, tid, text):
    try:
        if getattr(app, "telegram_bot_token", ""):
            send_message(app.telegram_bot_token, str(tid), text, parse_mode="HTML", protect_content=True)
    except Exception:
        pass


def _ensure_live_safety_schema(conn):
    conn.executescript(_LIVE_SAFETY_SCHEMA)


def _attempt_key(tid, event):
    raw = "|".join([
        str(tid),
        str(event.get("leader_wallet") or ""),
        str(event.get("signature") or ""),
        str(event.get("mint") or ""),
        str(event.get("action") or "").upper(),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def _claim_attempt(app, tid, event):
    """Atomically reserve one chain attempt for a user+leader signature+mint+action."""
    key = _attempt_key(tid, event)
    now = int(time.time())
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        _ensure_live_safety_schema(conn)
        cur = conn.execute(
            """INSERT OR IGNORE INTO live_execution_attempts(
                 attempt_key,telegram_id,leader_wallet,leader_signature,mint,action,status,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (key, str(tid), str(event.get("leader_wallet") or ""), str(event.get("signature") or ""),
             str(event.get("mint") or ""), str(event.get("action") or "").upper(), "CLAIMED", now, now),
        )
        conn.commit()
        return cur.rowcount == 1, key


def _execution_fields(trade):
    trade = dict(trade or {})
    def s(key):
        value = trade.get(key)
        return "" if value is None else str(value)
    return {
        "tx_signature": s("signature"),
        "input_raw": s("totalInputAmount") or s("inputAmountResult"),
        "output_raw": s("totalOutputAmount") or s("outputAmountResult"),
        "wallet_before_lamports": s("wallet_before_lamports"),
        "wallet_after_lamports": s("wallet_after_lamports"),
        "wallet_delta_lamports": s("wallet_delta_lamports"),
    }


def _update_attempt(app, key, status, trade=None, error=""):
    fields = _execution_fields(trade)
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        _ensure_live_safety_schema(conn)
        conn.execute(
            """UPDATE live_execution_attempts
               SET status=?,tx_signature=?,input_raw=?,output_raw=?,wallet_before_lamports=?,
                   wallet_after_lamports=?,wallet_delta_lamports=?,error=?,updated_at=?
               WHERE attempt_key=?""",
            (str(status), fields["tx_signature"], fields["input_raw"], fields["output_raw"],
             fields["wallet_before_lamports"], fields["wallet_after_lamports"], fields["wallet_delta_lamports"],
             str(error or "")[:1200], int(time.time()), str(key)),
        )
        conn.commit()


def _landed_fault_count(app, tid):
    with closing(_sol.connect(app)) as conn:
        _ensure_live_safety_schema(conn)
        return int(conn.execute(
            "SELECT COUNT(*) n FROM live_execution_attempts WHERE telegram_id=? AND status='LANDED_INVALID_OUTPUT'",
            (str(tid),),
        ).fetchone()["n"])


def _record_execution_fault(app, tid, cfg, message):
    count = _landed_fault_count(app, tid)
    threshold = max(1, _sol._int(cfg.get("live_no_output_disable_after"), 2))
    if count >= threshold:
        set_user_setting(
            app.csv_dir, tid, "solana_live_enabled", "false",
            chain_id=_sol.SOLANA_CHAIN_ID,
            description="Automatically disabled after repeated landed-but-invalid Solana executions",
        )
        _notify(
            app, tid,
            f"🛑 <b>Solana LIVE automatically disabled</b>\n"
            f"Landed-but-invalid execution faults: <b>{count}</b> (limit {threshold}).\n"
            f"Last fault: <code>{str(message)[:450]}</code>\n"
            "No new Solana LIVE entries will be submitted until you explicitly re-arm LIVE after investigation.",
        )
        return True
    _notify(
        app, tid,
        f"⚠️ <b>Solana LIVE execution fault {count}/{threshold}</b>\n"
        f"<code>{str(message)[:500]}</code>\n"
        "This leader signal is permanently deduplicated and will not be submitted again.",
    )
    return False


def _recent_buy_overhead_sol(app, tid):
    """Return the median-like middle recent observed BUY overhead, if reconcilable."""
    values = []
    with closing(_sol.connect(app)) as conn:
        _ensure_live_safety_schema(conn)
        rows = conn.execute(
            """SELECT input_raw,wallet_delta_lamports FROM live_execution_attempts
               WHERE telegram_id=? AND action='BUY' AND status='EXECUTED'
               ORDER BY created_at DESC LIMIT 7""",
            (str(tid),),
        ).fetchall()
    for row in rows:
        try:
            inp = int(row["input_raw"] or 0)
            delta = int(row["wallet_delta_lamports"] or 0)
        except Exception:
            continue
        if inp > 0 and delta < 0:
            overhead = max(0, -delta - inp)
            values.append(Decimal(overhead) / Decimal(1_000_000_000))
    if not values:
        return Decimal(0)
    values.sort()
    return values[len(values) // 2]


def _economic_entry_gate(app, tid, allocation, cfg):
    minimum = max(Decimal("0.0001"), _sol._dec(cfg.get("live_min_economic_trade_sol"), "0.009"))
    if Decimal(allocation) < minimum:
        return False, f"LIVE allocation {allocation} SOL is below economic minimum {minimum} SOL"
    overhead = _recent_buy_overhead_sol(app, tid)
    max_pct = max(Decimal(0), _sol._dec(cfg.get("live_max_observed_overhead_pct"), 35))
    if overhead > 0 and Decimal(allocation) > 0:
        pct = overhead * Decimal(100) / Decimal(allocation)
        if pct > max_pct:
            return False, f"recent actual execution overhead {overhead:.9f} SOL is {pct:.1f}% of trade size (limit {max_pct}%)"
    return True, "ok"


def _open_live_count(app, tid):
    with closing(_sol.connect(app)) as conn:
        return int(conn.execute("SELECT COUNT(*) n FROM positions WHERE telegram_id=? AND status='OPEN' AND mode='LIVE'", (str(tid),)).fetchone()["n"])


def _insert_live_position(app, tid, rank, event, trade, allocation, cfg):
    now = int(time.time())
    pid = hashlib.sha256(f"solana-live|{tid}|{event['leader_wallet']}|{event['mint']}|{event['signature']}".encode()).hexdigest()[:32]
    out_raw = int(trade.get("totalOutputAmount") or trade.get("outputAmountResult") or (trade.get("order") or {}).get("outAmount") or 0)
    if out_raw <= 0:
        raise SolanaLiveError("LIVE BUY confirmed but Jupiter returned no output token amount")
    input_lamports = int(trade.get("totalInputAmount") or trade.get("inputAmountResult") or int(Decimal(str(allocation)) * Decimal(1_000_000_000)))
    delta_raw = trade.get("wallet_delta_lamports")
    try:
        delta = int(delta_raw) if delta_raw is not None else 0
    except Exception:
        delta = 0
    if delta < 0:
        # Actual wallet cash spend includes network/priority fees and temporary
        # account funding. Any later rent refund is naturally reflected in the
        # actual exit wallet delta, so the round trip reconciles in cash terms.
        entry_cost = Decimal(-delta) / Decimal(1_000_000_000)
    else:
        entry_cost = Decimal(input_lamports) / Decimal(1_000_000_000) + _sol._dec(cfg.get("estimated_entry_fee_sol"), ".00002")
    tx = str(trade.get("signature") or "")
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO positions(position_id,telegram_id,leader_wallet,leader_rank,mint,mode,status,token_amount_raw,
                                                entry_cost_sol,entry_ts,leader_buy_signature,leader_entry_sol,leader_entry_token_raw,
                                                signal_count,current_exit_sol,unrealised_net_sol,unrealised_pct,peak_unrealised_pct,
                                                leader_exit_pending,realised_net_sol,exit_signature,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, str(tid), event["leader_wallet"], rank, event["mint"], "LIVE", "OPEN", str(out_raw),
             str(entry_cost), now, event["signature"], str(event["sol_amount"]), str(event["token_amount_raw"]),
             1, "0", "0", 0.0, 0.0, 0, "0", tx, now),
        )
        conn.commit()
    return pid, out_raw, entry_cost


def _close_live(app, tid, position, fraction: Decimal, reason: str):
    executor = SolanaLiveExecutor(app, tid)
    old_raw = max(1, _sol._int(position.get("token_amount_raw"), 0))
    f = max(Decimal("0.0001"), min(Decimal(1), Decimal(str(fraction))))
    planned = max(1, int(Decimal(old_raw) * f))
    actual = executor.token_balance_raw(position["mint"])
    sell_raw = min(planned, actual)
    if sell_raw <= 0:
        raise SolanaLiveError("LIVE exit cannot execute because the wallet holds no matching token")
    trade = executor.sell(position["mint"], sell_raw)
    out_lamports = int(trade.get("totalOutputAmount") or trade.get("outputAmountResult") or 0)
    delta_raw = trade.get("wallet_delta_lamports")
    try:
        delta = int(delta_raw) if delta_raw is not None else 0
    except Exception:
        delta = 0
    cfg = _sol.settings(app)
    if delta > 0:
        proceeds = Decimal(delta) / Decimal(1_000_000_000)
    else:
        proceeds = Decimal(out_lamports) / Decimal(1_000_000_000) - _sol._dec(cfg.get("estimated_exit_fee_sol"), ".00002")
    old_cost = _sol._dec(position.get("entry_cost_sol"), 0)
    cost_fraction = old_cost * Decimal(sell_raw) / Decimal(old_raw)
    net = proceeds - cost_fraction
    remaining = max(0, old_raw - sell_raw)
    remaining_cost = max(Decimal(0), old_cost - cost_fraction)
    closed = remaining <= max(1, int(old_raw * .001)) or f >= Decimal("0.999")
    realised = _sol._dec(position.get("realised_net_sol"), 0) + net
    now = int(time.time())
    sig = str(trade.get("signature") or "")
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        conn.execute(
            """UPDATE positions SET token_amount_raw=?,entry_cost_sol=?,realised_net_sol=?,exit_signature=?,exit_reason=?,closed_at=?,
                                    status=?,leader_exit_pending=?,updated_at=? WHERE position_id=?""",
            (str(0 if closed else remaining), str(0 if closed else remaining_cost), str(realised), sig, reason,
             now if closed else None, "CLOSED" if closed else "OPEN", 0 if closed else int(position.get("leader_exit_pending") or 0), now, position["position_id"]),
        )
        conn.commit()
    _notify(app, tid, f"✅ <b>Solana LIVE SELL confirmed</b>\nReason: <code>{reason}</code>\nReceived wallet delta: <b>{proceeds:.9f} SOL</b>\nNet on sold portion: <b>{net:+.9f} SOL</b>\nTX: <code>{sig}</code>")
    return {"closed": closed, "net_sol": net, "signature": sig, "reason": reason, "trade": trade}


def process_leader_event(app, event: dict):
    """LIVE-only Solana entry path with durable one-attempt signal deduplication."""
    cfg = _sol.settings(app)
    actions = []
    for u in all_users(app.csv_dir, enabled_only=True):
        tid = str(u.get("telegram_id") or "")
        if not tid or not live_enabled(app, tid):
            continue
        if not _sol._sibot._bool(_sol._sibot.user_settings(app, tid, 0).get("enabled"), False):
            continue
        rank = _sol._leader_rank(app, tid, event["leader_wallet"])
        if rank is None:
            continue
        if event["action"] == "BUY":
            max_positions = max(1, min(5, _sol._int(cfg.get("live_max_positions"), 1)))
            if _open_live_count(app, tid) >= max_positions or _sol._open_position(app, tid, event["mint"]):
                actions.append({"telegram_id": tid, "action": "SKIP", "reason": "LIVE position limit/already held"})
                continue
            allocation, reserve = live_limits(app, tid, cfg)
            try:
                ok, reason, _ = _sol._validate_shadow_entry(app, event, allocation, cfg)
                if not ok:
                    actions.append({"telegram_id": tid, "action": "REJECT", "reason": reason})
                    continue
                ok, reason = _economic_entry_gate(app, tid, allocation, cfg)
                if not ok:
                    actions.append({"telegram_id": tid, "action": "REJECT", "reason": reason})
                    continue
                executor = SolanaLiveExecutor(app, tid)
                # Pre-check funding before claiming the signal so a simple funding
                # deficiency does not permanently consume the execution attempt.
                need = int((allocation + reserve) * Decimal(1_000_000_000))
                if executor.native_balance_lamports() < need:
                    actions.append({"telegram_id": tid, "action": "REJECT", "reason": "insufficient SOL for trade plus reserve"})
                    continue
                claimed, attempt_key = _claim_attempt(app, tid, event)
                if not claimed:
                    actions.append({"telegram_id": tid, "action": "SKIP", "reason": "duplicate leader signal already attempted"})
                    continue
                try:
                    trade = executor.buy(event["mint"], allocation, reserve)
                    _update_attempt(app, attempt_key, "EXECUTED", trade)
                    pid, out_raw, entry_cost = _insert_live_position(app, tid, rank, event, trade, allocation, cfg)
                    sig = str(trade.get("signature") or "")
                    _notify(app, tid, f"🚀 <b>Solana LIVE BUY confirmed</b>\nActual wallet spend: <b>{entry_cost:.9f} SOL</b>\nToken: <code>{event['mint']}</code>\nReceived raw: <code>{out_raw}</code>\nTX: <code>{sig}</code>")
                    actions.append({"telegram_id": tid, "action": "BUY", "position_id": pid, "mode": "LIVE", "signature": sig})
                except SolanaLivePostExecutionError as exc:
                    _update_attempt(app, attempt_key, "LANDED_INVALID_OUTPUT", exc.result, str(exc))
                    _record_execution_fault(app, tid, cfg, exc)
                    actions.append({"telegram_id": tid, "action": "REJECT", "reason": str(exc), "signature": exc.signature})
                except Exception as exc:
                    _update_attempt(app, attempt_key, "FAILED_NO_RETRY", None, str(exc))
                    _notify(app, tid, f"🚨 <b>Solana LIVE BUY blocked</b>\n<code>{type(exc).__name__}: {str(exc)[:500]}</code>\nThis exact leader signal will not be retried automatically.")
                    actions.append({"telegram_id": tid, "action": "REJECT", "reason": str(exc)})
            except Exception as exc:
                _notify(app, tid, f"🚨 <b>Solana LIVE BUY preflight blocked</b>\n<code>{type(exc).__name__}: {str(exc)[:500]}</code>")
                actions.append({"telegram_id": tid, "action": "REJECT", "reason": str(exc)})
        elif event["action"] == "SELL":
            with closing(_sol.connect(app)) as conn:
                rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM positions WHERE telegram_id=? AND leader_wallet=? AND mint=? AND status='OPEN' AND mode='LIVE'",
                    (tid, event["leader_wallet"], event["mint"]),
                ).fetchall()]
            for p in rows:
                full = _sol._float(event.get("sell_pct"), 100) >= 99
                fraction = Decimal(1) if full else max(Decimal("0.0001"), min(Decimal(1), _sol._dec(event.get("sell_pct"), 100) / Decimal(100)))
                if not full and not _sol._bool(cfg.get("mirror_partial_sells"), True):
                    continue
                try:
                    ev = _sol.evaluate_position(app, p, fraction)
                    min_profit = _sol._dec(_sol._sibot.user_settings(app, tid, 0).get("min_exit_profit_pct"), ".10")
                    stop = _sol._dec(cfg.get("stop_loss_pct"), 10)
                    if ev["net_pct"] >= min_profit or ev["net_pct"] <= -stop:
                        claimed, attempt_key = _claim_attempt(app, tid, event)
                        if not claimed:
                            actions.append({"telegram_id": tid, "action": "SKIP", "reason": "duplicate leader SELL signal already attempted"})
                            continue
                        try:
                            result = _close_live(app, tid, p, fraction, "SOLANA_LEADER_PARTIAL_SELL" if not full else "SOLANA_LEADER_SELL")
                            _update_attempt(app, attempt_key, "EXECUTED", result.get("trade"))
                            actions.append({"telegram_id": tid, "action": "SELL", "position_id": p["position_id"], "signature": result.get("signature")})
                        except SolanaLivePostExecutionError as exc:
                            _update_attempt(app, attempt_key, "LANDED_INVALID_OUTPUT", exc.result, str(exc))
                            _record_execution_fault(app, tid, cfg, exc)
                            actions.append({"telegram_id": tid, "action": "REJECT", "reason": str(exc), "signature": exc.signature})
                        except Exception as exc:
                            _update_attempt(app, attempt_key, "FAILED_NO_RETRY", None, str(exc))
                            raise
                    else:
                        with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
                            conn.execute("UPDATE positions SET leader_exit_pending=1,updated_at=? WHERE position_id=?", (int(time.time()), p["position_id"]))
                            conn.commit()
                        actions.append({"telegram_id": tid, "action": "EXIT_PENDING", "position_id": p["position_id"]})
                except Exception as exc:
                    _notify(app, tid, f"🚨 <b>Solana LIVE exit warning</b>\n<code>{type(exc).__name__}: {str(exc)[:500]}</code>")
    return actions


def monitor_positions(app):
    cfg = _sol.settings(app)
    now = int(time.time())
    with closing(_sol.connect(app)) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM positions WHERE status='OPEN' AND mode='LIVE' ORDER BY updated_at").fetchall()]
    for p in rows:
        tid = str(p.get("telegram_id") or "")
        if not live_enabled(app, tid):
            continue
        try:
            ev = _sol.evaluate_position(app, p)
        except Exception:
            continue
        current = _sol._dec(ev["net_pct"])
        peak = max(_sol._dec(p.get("peak_unrealised_pct"), 0), current)
        with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
            conn.execute(
                "UPDATE positions SET current_exit_sol=?,unrealised_net_sol=?,unrealised_pct=?,peak_unrealised_pct=?,updated_at=? WHERE position_id=?",
                (str(ev["proceeds_sol"]), str(ev["net_sol"]), float(current), float(peak), now, p["position_id"]),
            )
            conn.commit()
        reason = None
        if int(p.get("leader_exit_pending") or 0) and current <= -_sol._dec(cfg.get("leader_exit_loss_cap_pct"), "2.5"):
            reason = "SOLANA_LEADER_EXIT_LOSS_CAP"
        elif peak >= _sol._dec(cfg.get("break_even_trigger_pct"), 5) and current <= _sol._dec(cfg.get("break_even_floor_pct"), ".10"):
            reason = "SOLANA_BREAK_EVEN_PROTECT"
        elif peak >= _sol._dec(cfg.get("trailing_trigger_pct"), 10):
            floor = max(_sol._dec(cfg.get("break_even_floor_pct"), ".10"), peak - _sol._dec(cfg.get("trailing_gap_pct"), 5))
            if current <= floor:
                reason = "SOLANA_TRAILING_PROFIT_PROTECT"
        if reason is None and current <= -_sol._dec(cfg.get("stop_loss_pct"), 10):
            reason = "SOLANA_STOP_LOSS"
        if reason is None and current >= _sol._dec(cfg.get("take_profit_pct"), 25):
            reason = "SOLANA_TAKE_PROFIT"
        age_h = Decimal(max(0, now - _sol._int(p.get("entry_ts"), now))) / Decimal(3600)
        if reason is None and age_h >= _sol._dec(cfg.get("max_hold_hours"), 24) and current > 0:
            reason = "SOLANA_MAX_HOLD_PROFIT"
        if reason:
            try:
                fresh = dict(p)
                fresh["peak_unrealised_pct"] = float(peak)
                _close_live(app, tid, fresh, Decimal(1), reason)
            except SolanaLivePostExecutionError as exc:
                # Monitor exits have no leader-signature dedupe key, so the global
                # landed-fault circuit breaker is the backstop. Two such faults turn
                # LIVE off and stop further monitor submissions for this user.
                _record_execution_fault(app, tid, cfg, exc)
                _notify(app, tid, f"🚨 <b>Solana LIVE emergency exit landed without valid economic output</b>\n<code>{str(exc)[:450]}</code>")
            except Exception as exc:
                _notify(app, tid, f"🚨 <b>Solana LIVE emergency-exit warning</b>\nReason: <code>{reason}</code>\n<code>{type(exc).__name__}: {str(exc)[:450]}</code>")
    _sol.export_csv(app)


def install():
    _sol.process_leader_event = process_leader_event
    _sol.monitor_positions = monitor_positions


install()
