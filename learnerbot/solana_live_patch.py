from __future__ import annotations

import hashlib
import time
from contextlib import closing
from decimal import Decimal

from . import solana_sibot as _sol
from .solana_live_executor import SolanaLiveError, SolanaLiveExecutor
from .telegram import send_message
from .user_registry import all_users, user_bool

# LIVE canary defaults. New Solana entries are no longer created in SHADOW mode.
_sol.DEFAULTS.update({
    "live_trade_sol": ("0.005", "Real SOL amount per guarded Solana LIVE copied BUY"),
    "live_min_sol_reserve": ("0.02", "SOL that must remain untouched for fees and emergency exits"),
    "live_max_positions": ("1", "Maximum simultaneous Solana LIVE positions per Telegram user"),
    "live_require_simulation": ("true", "Require successful Solana simulation before Jupiter execute"),
})


def live_enabled(app, telegram_id) -> bool:
    return user_bool(app.csv_dir, telegram_id, _sol.SOLANA_CHAIN_ID, "solana_live_enabled", False)


def _notify(app, tid, text):
    try:
        if getattr(app, "telegram_bot_token", ""):
            send_message(app.telegram_bot_token, str(tid), text, parse_mode="HTML", protect_content=True)
    except Exception:
        pass


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
    proceeds = Decimal(out_lamports) / Decimal(1_000_000_000)
    cfg = _sol.settings(app)
    proceeds -= _sol._dec(cfg.get("estimated_exit_fee_sol"), ".00002")
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
    _notify(app, tid, f"✅ <b>Solana LIVE SELL confirmed</b>\nReason: <code>{reason}</code>\nReceived: <b>{proceeds:.9f} SOL</b>\nNet on sold portion: <b>{net:+.9f} SOL</b>\nTX: <code>{sig}</code>")
    return {"closed": closed, "net_sol": net, "signature": sig, "reason": reason}


def process_leader_event(app, event: dict):
    """LIVE-only Solana entry path. No new SHADOW positions are created."""
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
            allocation = min(Decimal("0.005"), max(Decimal("0.0005"), _sol._dec(cfg.get("live_trade_sol"), ".005")))
            reserve = max(Decimal("0.01"), _sol._dec(cfg.get("live_min_sol_reserve"), ".02"))
            try:
                ok, reason, _ = _sol._validate_shadow_entry(app, event, allocation, cfg)
                if not ok:
                    actions.append({"telegram_id": tid, "action": "REJECT", "reason": reason})
                    continue
                executor = SolanaLiveExecutor(app, tid)
                trade = executor.buy(event["mint"], allocation, reserve)
                pid, out_raw, entry_cost = _insert_live_position(app, tid, rank, event, trade, allocation, cfg)
                sig = str(trade.get("signature") or "")
                _notify(app, tid, f"🚀 <b>Solana LIVE BUY confirmed</b>\nSpent: <b>{entry_cost:.9f} SOL</b>\nToken: <code>{event['mint']}</code>\nReceived raw: <code>{out_raw}</code>\nTX: <code>{sig}</code>")
                actions.append({"telegram_id": tid, "action": "BUY", "position_id": pid, "mode": "LIVE", "signature": sig})
            except Exception as exc:
                _notify(app, tid, f"🚨 <b>Solana LIVE BUY blocked</b>\n<code>{type(exc).__name__}: {str(exc)[:500]}</code>\nNo LIVE position was recorded.")
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
                        result = _close_live(app, tid, p, fraction, "SOLANA_LEADER_PARTIAL_SELL" if not full else "SOLANA_LEADER_SELL")
                        actions.append({"telegram_id": tid, "action": "SELL", "position_id": p["position_id"], "signature": result.get("signature")})
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
            except Exception as exc:
                _notify(app, tid, f"🚨 <b>Solana LIVE emergency-exit warning</b>\nReason: <code>{reason}</code>\n<code>{type(exc).__name__}: {str(exc)[:450]}</code>")
    _sol.export_csv(app)


def install():
    _sol.process_leader_event = process_leader_event
    _sol.monitor_positions = monitor_positions


install()
