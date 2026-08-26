from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import sqlite3
import threading
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import cli as _cli
from . import solana_sibot as _sol
from . import telegram as _tg
from . import telegram_ui as _ui
from .solana_entry_exit_liquidity_preflight_patch import _quote_price_impact_bps
from .solana_live_executor import SolanaLiveError, SolanaLiveExecutor, SolanaLivePostExecutionError
from .solana_pool_risk_gate import external_pool_check
from .solana_wallet_store import SolanaWalletStore
from .user_registry import is_master, require_user

_PREV_APP = _cli._app
_PREV_HANDLE_UPDATE = _ui.handle_update
_STARTED = False
_START_LOCK = threading.Lock()
_DB_LOCK = threading.RLock()

CONTROL_HEADERS = [
    "telegram_id", "armed", "live_enabled", "auto_enabled", "max_sol_per_trade", "updated_epoch"
]
DEFAULT_ENTRY_SOL = Decimal("0.0005")
HARD_MAX_ENTRY_SOL = Decimal("0.001")
MIN_RESERVE_SOL = Decimal("0.005")
MAX_SIGNAL_AGE_SECONDS = 20
MAX_OPEN_POSITIONS = 1
MAX_REVERSE_IMPACT_BPS = Decimal("200")
MAX_STRESS_IMPACT_BPS = Decimal("500")
EXIT_IMPACT_CAP_BPS = Decimal("500")
STRESS_MULTIPLIER = 3
EXIT_RETRY_SECONDS = 30


def _bool(value, default=False):
    if value in (None, ""):
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _dec(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default))


def _control_path(app) -> Path:
    return Path(app.csv_dir) / "sibot1" / "solana_live_control.csv"


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except Exception:
        return []


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CONTROL_HEADERS)
        writer.writeheader()
        writer.writerows([{h: row.get(h, "") for h in CONTROL_HEADERS} for row in rows])
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def control(app, tid) -> dict[str, str]:
    wanted = str(tid)
    for row in _rows(_control_path(app)):
        if str(row.get("telegram_id") or "") == wanted:
            return dict(row)
    return {
        "telegram_id": wanted,
        "armed": "false",
        "live_enabled": "false",
        "auto_enabled": "false",
        "max_sol_per_trade": str(DEFAULT_ENTRY_SOL),
        "updated_epoch": "0",
    }


def set_control(app, tid, **updates) -> dict[str, str]:
    wanted = str(tid)
    path = _control_path(app)
    rows = _rows(path)
    row = next((r for r in rows if str(r.get("telegram_id") or "") == wanted), None)
    if row is None:
        row = control(app, wanted)
        rows.append(row)
    for key, value in updates.items():
        if key in CONTROL_HEADERS and key != "telegram_id":
            row[key] = str(value)
    row["updated_epoch"] = str(int(time.time()))
    _write_rows(path, rows)
    return dict(row)


def _entry_size(ctl) -> Decimal:
    requested = _dec(ctl.get("max_sol_per_trade"), DEFAULT_ENTRY_SOL)
    return min(HARD_MAX_ENTRY_SOL, max(DEFAULT_ENTRY_SOL, requested))


def _candidate_age(candidate) -> float:
    try:
        created = int(candidate.get("intent_created_at_ms") or 0) / 1000.0
        if created <= 0:
            return 10**9
        return max(0.0, time.time() - created)
    except Exception:
        return 10**9


def _signer_and_balance(app, tid) -> tuple[bool, str, Decimal]:
    try:
        store = SolanaWalletStore(app.csv_dir, app.data_dir)
        meta = store.get_meta(tid)
        wallet_id = meta.get("wallet_id")
        if not store.has_private_key(tid, wallet_id):
            return False, "encrypted Solana signer is not available", Decimal(0)
        address = str(meta.get("address") or "")
        if not address:
            return False, "active Solana wallet address is missing", Decimal(0)
        result = _sol._rpc(app, "getBalance", [address, {"commitment": "confirmed"}]) or {}
        balance = Decimal(int(result.get("value") or 0)) / Decimal(1_000_000_000)
        return True, "ready", balance
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", Decimal(0)


def _account_gate(app, tid) -> tuple[bool, str]:
    try:
        user = require_user(app.csv_dir, tid, active=True, chain_slug="solana")
    except Exception as exc:
        return False, str(exc)
    if not _bool(user.get("can_auto_trade"), False):
        return False, "account automatic-trading permission is OFF"
    return True, "ok"


def readiness(app, tid) -> dict:
    ctl = control(app, tid)
    amount = _entry_size(ctl)
    signer_ok, signer_detail, balance = _signer_and_balance(app, tid)
    account_ok, account_reason = _account_gate(app, tid)
    funded = balance >= amount + MIN_RESERVE_SOL
    armed = _bool(ctl.get("armed"))
    live = _bool(ctl.get("live_enabled"))
    auto = _bool(ctl.get("auto_enabled"))
    return {
        "control": ctl,
        "signer_ready": signer_ok,
        "signer_detail": signer_detail,
        "balance_sol": balance,
        "funded": funded,
        "account_ready": account_ok,
        "account_reason": account_reason,
        "entry_size_sol": amount,
        "reserve_sol": MIN_RESERVE_SOL,
        "entry_execution_active": bool(armed and live and auto and signer_ok and funded and account_ok),
        "exit_execution_active": bool(armed and live and signer_ok and account_ok),
        "chain": "solana",
    }


def _db(app) -> sqlite3.Connection:
    path = Path(app.data_dir) / "sibot1_solana_live_bridge.sqlite3"
    conn = sqlite3.connect(path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS attempts(
      attempt_key TEXT PRIMARY KEY,
      telegram_id TEXT NOT NULL,
      candidate_id TEXT NOT NULL,
      kind TEXT NOT NULL,
      engine_id TEXT,
      chain TEXT NOT NULL,
      shadow_lot_id TEXT,
      mint TEXT,
      status TEXT NOT NULL,
      tx_signature TEXT,
      error TEXT,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS positions(
      telegram_id TEXT NOT NULL,
      shadow_lot_id TEXT NOT NULL,
      engine_id TEXT,
      mint TEXT NOT NULL,
      token_raw TEXT NOT NULL,
      entry_tx TEXT,
      exit_tx TEXT,
      status TEXT NOT NULL,
      updated_at INTEGER NOT NULL,
      PRIMARY KEY(telegram_id,shadow_lot_id)
    );
    """)
    return conn


def _attempt_key(tid, candidate) -> str:
    raw = "|".join([
        str(tid),
        str(candidate.get("candidate_id") or ""),
        str(candidate.get("kind") or ""),
        str(candidate.get("shadow_lot_id") or ""),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def _claim(app, tid, candidate) -> tuple[bool, str]:
    key = _attempt_key(tid, candidate)
    now = int(time.time())
    with _DB_LOCK:
        conn = _db(app)
        try:
            row = conn.execute("SELECT status,updated_at FROM attempts WHERE attempt_key=?", (key,)).fetchone()
            if row:
                retryable = str(row["status"] or "") == "EXIT_DEFERRED"
                old_enough = now - int(row["updated_at"] or 0) >= EXIT_RETRY_SECONDS
                if retryable and old_enough:
                    conn.execute(
                        "UPDATE attempts SET status='CLAIMED',error='',updated_at=? WHERE attempt_key=?",
                        (now, key),
                    )
                    conn.commit()
                    return True, key
                return False, key
            conn.execute(
                """INSERT INTO attempts(
                     attempt_key,telegram_id,candidate_id,kind,engine_id,chain,shadow_lot_id,mint,status,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    key, str(tid), str(candidate.get("candidate_id") or ""),
                    str(candidate.get("kind") or ""), str(candidate.get("engine_id") or ""),
                    str(candidate.get("chain") or ""), str(candidate.get("shadow_lot_id") or ""),
                    str(candidate.get("asset_out") or candidate.get("asset") or ""),
                    "CLAIMED", now, now,
                ),
            )
            conn.commit()
            return True, key
        finally:
            conn.close()


def _attempt_update(app, key, status, tx_signature="", error="") -> None:
    with _DB_LOCK:
        conn = _db(app)
        try:
            conn.execute(
                "UPDATE attempts SET status=?,tx_signature=?,error=?,updated_at=? WHERE attempt_key=?",
                (str(status), str(tx_signature or ""), str(error or "")[:1200], int(time.time()), str(key)),
            )
            conn.commit()
        finally:
            conn.close()


def _open_count(app, tid) -> int:
    conn = _db(app)
    try:
        row = conn.execute(
            "SELECT COUNT(*) n FROM positions WHERE telegram_id=? AND status='OPEN'", (str(tid),)
        ).fetchone()
        return int(row["n"] or 0)
    finally:
        conn.close()


def _position(app, tid, lot_id):
    conn = _db(app)
    try:
        row = conn.execute(
            "SELECT * FROM positions WHERE telegram_id=? AND shadow_lot_id=? AND status='OPEN'",
            (str(tid), str(lot_id)),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _save_position(app, tid, candidate, mint: str, token_raw: int, tx_signature: str) -> None:
    conn = _db(app)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO positions(
                 telegram_id,shadow_lot_id,engine_id,mint,token_raw,entry_tx,exit_tx,status,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                str(tid), str(candidate.get("shadow_lot_id") or ""),
                str(candidate.get("engine_id") or ""), str(mint), str(int(token_raw)),
                str(tx_signature or ""), "", "OPEN", int(time.time()),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _reduce_position(app, tid, lot_id, sold_raw: int, tx_signature: str) -> None:
    conn = _db(app)
    try:
        row = conn.execute(
            "SELECT token_raw FROM positions WHERE telegram_id=? AND shadow_lot_id=?",
            (str(tid), str(lot_id)),
        ).fetchone()
        if not row:
            return
        remaining = max(0, int(row["token_raw"] or 0) - int(sold_raw))
        conn.execute(
            """UPDATE positions
               SET token_raw=?,exit_tx=?,status=?,updated_at=?
               WHERE telegram_id=? AND shadow_lot_id=?""",
            (
                str(remaining), str(tx_signature or ""), "CLOSED" if remaining <= 0 else "OPEN",
                int(time.time()), str(tid), str(lot_id),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _quote_out_raw(quote) -> int:
    try:
        return int(str((quote or {}).get("outAmount") or (quote or {}).get("outputAmount") or 0))
    except Exception:
        return 0


def _impact_bps(quote):
    try:
        value = _quote_price_impact_bps(quote or {})
    except Exception:
        return None
    return None if value is None else Decimal(str(value))


def _live_entry_revalidation(app, mint: str, amount_sol: Decimal) -> tuple[bool, str, dict]:
    cfg = _sol.settings(app)
    external = external_pool_check(str(mint), cfg)
    decision = str((external or {}).get("decision") or "HARD_BLOCK").upper()
    if decision != "PASS":
        return False, f"{(external or {}).get('reason_code') or decision}: {(external or {}).get('reason') or 'PoolCheck did not PASS'}", {
            "external_decision": decision,
        }

    lamports = int(Decimal(amount_sol) * Decimal(1_000_000_000))
    try:
        forward = _sol.jupiter_quote(app, _sol.WSOL_MINT, str(mint), lamports)
    except Exception as exc:
        return False, f"forward Jupiter quote failed: {type(exc).__name__}", {}
    token_raw = _quote_out_raw(forward)
    if token_raw <= 0:
        return False, "forward Jupiter quote returned no token output", {}

    try:
        reverse = _sol.jupiter_quote(app, str(mint), _sol.WSOL_MINT, token_raw)
    except Exception as exc:
        return False, f"full reverse Jupiter quote failed: {type(exc).__name__}", {}
    reverse_out = _quote_out_raw(reverse)
    reverse_impact = _impact_bps(reverse)
    if reverse_out <= 0 or reverse_impact is None:
        return False, "full reverse sellability was not proven", {}
    if reverse_impact > MAX_REVERSE_IMPACT_BPS:
        return False, f"full reverse price impact {reverse_impact:.2f} bps exceeds {MAX_REVERSE_IMPACT_BPS:.0f} bps", {}

    recovered_sol = Decimal(reverse_out) / Decimal(1_000_000_000)
    loss_pct = max(Decimal(0), (Decimal(1) - recovered_sol / Decimal(amount_sol)) * Decimal(100))
    configured_loss = max(Decimal(0), _dec(cfg.get("max_roundtrip_loss_pct"), "3"))
    loss_limit = min(Decimal("3"), configured_loss if configured_loss > 0 else Decimal("3"))
    if loss_pct > loss_limit:
        return False, f"full reverse value loss {loss_pct:.3f}% exceeds {loss_limit:.3f}% limit", {}

    stress_raw = max(1, token_raw * STRESS_MULTIPLIER)
    try:
        stress = _sol.jupiter_quote(app, str(mint), _sol.WSOL_MINT, stress_raw)
    except Exception as exc:
        return False, f"3x reverse stress quote failed: {type(exc).__name__}", {}
    stress_out = _quote_out_raw(stress)
    stress_impact = _impact_bps(stress)
    if stress_out <= 0 or stress_impact is None:
        return False, "3x reverse exit stress was not proven", {}
    if stress_impact > MAX_STRESS_IMPACT_BPS:
        return False, f"3x reverse price impact {stress_impact:.2f} bps exceeds {MAX_STRESS_IMPACT_BPS:.0f} bps", {}

    return True, "PASS", {
        "forward_out_raw": token_raw,
        "reverse_out_lamports": reverse_out,
        "reverse_impact_bps": str(reverse_impact),
        "roundtrip_loss_pct": str(loss_pct),
        "stress_out_lamports": stress_out,
        "stress_impact_bps": str(stress_impact),
    }


def _exit_route_ok(app, mint: str, amount_raw: int) -> tuple[bool, str]:
    try:
        quote = _sol.jupiter_quote(app, str(mint), _sol.WSOL_MINT, int(amount_raw))
    except Exception as exc:
        return False, f"exit quote unavailable: {type(exc).__name__}"
    out_raw = _quote_out_raw(quote)
    impact = _impact_bps(quote)
    if out_raw <= 0:
        return False, "exit quote returned no SOL output"
    if impact is None:
        return False, "exit quote did not report price impact"
    if impact > EXIT_IMPACT_CAP_BPS:
        return False, f"exit price impact {impact:.2f} bps exceeds {EXIT_IMPACT_CAP_BPS:.0f} bps"
    return True, "ok"


def _notify(app, tid, text):
    try:
        if getattr(app, "telegram_bot_token", ""):
            _tg.send_message(
                app.telegram_bot_token, str(tid), text,
                parse_mode="HTML", protect_content=True,
            )
    except Exception:
        pass


def _execute_entry(app, tid, candidate, key) -> None:
    if _open_count(app, tid) >= MAX_OPEN_POSITIONS:
        _attempt_update(app, key, "BLOCKED_MAX_POSITION")
        return
    mint = str(candidate.get("asset_out") or "").strip()
    if not mint or mint == _sol.WSOL_MINT or len(mint) < 32 or len(mint) > 64 or any(ch.isspace() for ch in mint):
        _attempt_update(app, key, "BLOCKED_INVALID_MINT", error="Solana candidate has no valid output mint")
        return

    ctl = control(app, tid)
    amount = _entry_size(ctl)
    ok, reason, _evidence = _live_entry_revalidation(app, mint, amount)
    if not ok:
        _attempt_update(app, key, "BLOCKED_POOLCHECK", error=reason)
        _notify(
            app, tid,
            "🛡 <b>SiBot 1 Solana candidate blocked by LIVE PoolCheck</b>\n"
            f"Engine: <b>{html.escape(str(candidate.get('engine_id') or ''))}</b>\n"
            f"Reason: <code>{html.escape(reason[:500])}</code>",
        )
        return

    executor = SolanaLiveExecutor(app, tid)
    before_raw = executor.token_balance_raw(mint)
    try:
        result = executor.buy(mint, amount, MIN_RESERVE_SOL)
    except SolanaLivePostExecutionError as exc:
        set_control(app, tid, auto_enabled="false")
        _attempt_update(app, key, "LANDED_INVALID_OUTPUT", exc.signature, str(exc))
        _notify(
            app, tid,
            "🛑 <b>SiBot 1 Solana AUTO paused</b>\n"
            "A transaction landed/reported success but economic output validation failed.\n"
            f"<code>{html.escape(str(exc)[:500])}</code>",
        )
        return
    except SolanaLiveError as exc:
        _attempt_update(app, key, "REJECTED_OR_FAILED", error=str(exc))
        return

    signature = str(result.get("signature") or "")
    after_raw = executor.token_balance_raw(mint)
    acquired = max(0, int(after_raw) - int(before_raw))
    if not signature or acquired <= 0:
        set_control(app, tid, auto_enabled="false")
        _attempt_update(app, key, "LANDED_UNPROVEN_OUTPUT", signature, "positive token balance delta not proven")
        _notify(
            app, tid,
            "🛑 <b>SiBot 1 Solana AUTO paused</b>\n"
            "Entry result could not prove a positive token balance delta.",
        )
        return

    _save_position(app, tid, candidate, mint, acquired, signature)
    _attempt_update(app, key, "EXECUTED", signature)
    _notify(
        app, tid,
        "🚀 <b>SiBot 1 Solana CANARY BUY confirmed</b>\n"
        f"Engine: <b>{html.escape(str(candidate.get('engine_id') or ''))}</b>\n"
        f"Size: <b>{amount} SOL</b>\n"
        f"TX: <code>{html.escape(signature)}</code>",
    )


def _execute_exit(app, tid, candidate, key) -> None:
    pos = _position(app, tid, candidate.get("shadow_lot_id"))
    if not pos:
        _attempt_update(app, key, "NO_LIVE_POSITION")
        return
    raw = max(0, int(pos.get("token_raw") or 0))
    if raw <= 0:
        _attempt_update(app, key, "NO_LIVE_POSITION")
        return
    fraction = min(Decimal(1), max(Decimal("0.0001"), _dec(candidate.get("exit_fraction"), "1")))
    sell_raw = max(1, int(Decimal(raw) * fraction))
    ok, reason = _exit_route_ok(app, str(pos.get("mint") or ""), sell_raw)
    if not ok:
        _attempt_update(app, key, "EXIT_DEFERRED", error=reason)
        return

    executor = SolanaLiveExecutor(app, tid)
    before_raw = executor.token_balance_raw(str(pos.get("mint") or ""))
    actual_sell = min(sell_raw, before_raw)
    if actual_sell <= 0:
        _attempt_update(app, key, "NO_WALLET_TOKEN")
        return
    try:
        result = executor.sell(str(pos.get("mint") or ""), actual_sell)
    except SolanaLivePostExecutionError as exc:
        _attempt_update(app, key, "EXIT_LANDED_INVALID", exc.signature, str(exc))
        _notify(
            app, tid,
            "⚠️ <b>SiBot 1 Solana exit needs inspection</b>\n"
            f"<code>{html.escape(str(exc)[:500])}</code>",
        )
        return
    except SolanaLiveError as exc:
        _attempt_update(app, key, "EXIT_DEFERRED", error=str(exc))
        return

    signature = str(result.get("signature") or "")
    after_raw = executor.token_balance_raw(str(pos.get("mint") or ""))
    sold_raw = max(0, int(before_raw) - int(after_raw))
    if sold_raw <= 0 or not signature:
        _attempt_update(app, key, "EXIT_DEFERRED", signature, "wallet token decrease not proven")
        return
    _reduce_position(app, tid, candidate.get("shadow_lot_id"), sold_raw, signature)
    _attempt_update(app, key, "EXECUTED", signature)
    _notify(
        app, tid,
        "✅ <b>SiBot 1 Solana CANARY SELL confirmed</b>\n"
        f"Reason: <code>{html.escape(str(candidate.get('reason') or 'strategy_exit'))}</code>\n"
        f"Fraction: <b>{fraction * 100:.2f}%</b>\n"
        f"TX: <code>{html.escape(signature)}</code>",
    )


def _candidate_rows(app):
    path = Path(app.data_dir) / "sibot1" / "live_candidates.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-1000:]
    except Exception:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict) and str(row.get("chain") or "").lower() == "solana":
            out.append(row)
    return out


def _process_candidate(app, tid, candidate) -> None:
    if _candidate_age(candidate) > MAX_SIGNAL_AGE_SECONDS:
        return
    kind = str(candidate.get("kind") or "").upper()
    if kind not in {"ENTRY", "EXIT"}:
        return
    r = readiness(app, tid)
    if kind == "ENTRY":
        if not r.get("entry_execution_active"):
            return
        if str(candidate.get("poolcheck_verdict") or "HARD_BLOCK").upper() == "HARD_BLOCK":
            return
    else:
        if not r.get("exit_execution_active"):
            return
    claimed, key = _claim(app, tid, candidate)
    if not claimed:
        return
    try:
        if kind == "ENTRY":
            _execute_entry(app, tid, candidate, key)
        else:
            _execute_exit(app, tid, candidate, key)
    except Exception as exc:
        if kind == "EXIT":
            _attempt_update(app, key, "EXIT_DEFERRED", error=f"{type(exc).__name__}: {exc}")
        else:
            _attempt_update(app, key, "REJECTED_OR_FAILED", error=f"{type(exc).__name__}: {exc}")
        _notify(
            app, tid,
            "🚨 <b>SiBot 1 Solana bridge error</b>\n"
            f"<code>{html.escape(type(exc).__name__ + ': ' + str(exc)[:500])}</code>",
        )


def _worker(app):
    time.sleep(20)
    while True:
        try:
            controls = [r for r in _rows(_control_path(app)) if _bool(r.get("live_enabled"))]
            if controls:
                candidates = _candidate_rows(app)
                for ctl in controls:
                    tid = str(ctl.get("telegram_id") or "")
                    if not tid:
                        continue
                    for candidate in candidates:
                        _process_candidate(app, tid, candidate)
        except Exception as exc:
            print("[sibot1-solana-live-bridge] worker", type(exc).__name__, str(exc)[:240])
        time.sleep(2)


def _start(app):
    global _STARTED
    with _START_LOCK:
        if _STARTED:
            return
        threading.Thread(
            target=_worker, args=(app,), daemon=True, name="sibot1-protected-solana-live-bridge"
        ).start()
        _STARTED = True
        print(
            "[sibot1-solana-live-bridge] installed=true default=OFF max_open=1 "
            "canary_sol=0.0005 hard_max_sol=0.001 reserve_sol=0.005 poolcheck=fail-closed "
            "reverse=full stress=3x signed_simulation=required"
        )


def _app_with_bridge():
    app = _PREV_APP()
    _start(app)
    return app


def status_text(app, tid) -> str:
    r = readiness(app, tid)
    ctl = r["control"]
    icon = lambda v: "🟢" if v else "🔴"
    return "\n".join([
        "<b>🟣 SiBot 1 — Solana LIVE Bridge</b>",
        "",
        "Protected execution bridge: <b>Solana/Jupiter canary</b>",
        "AI private-key access: <b>OFF</b>",
        "Broadcast: <b>DEFAULT OFF — manual confirmation required</b>",
        "",
        f"{icon(_bool(ctl.get('armed')))} Solana ARMED: <b>{'YES' if _bool(ctl.get('armed')) else 'NO'}</b>",
        f"{icon(_bool(ctl.get('live_enabled')))} Solana LIVE: <b>{'YES' if _bool(ctl.get('live_enabled')) else 'NO'}</b>",
        f"{icon(_bool(ctl.get('auto_enabled')))} Solana AUTO entries: <b>{'YES' if _bool(ctl.get('auto_enabled')) else 'NO'}</b>",
        f"{icon(r.get('signer_ready'))} Solana signer vault: <b>{'READY' if r.get('signer_ready') else 'NOT READY'}</b>",
        f"{icon(r.get('account_ready'))} Account AUTO permission",
        f"{icon(r.get('funded'))} Funding check: <b>{r.get('balance_sol', Decimal(0)):.9f} SOL</b>",
        "",
        f"Canary size: <b>{r.get('entry_size_sol')} SOL</b> (hard maximum {HARD_MAX_ENTRY_SOL} SOL)",
        f"Untouched reserve: <b>{MIN_RESERVE_SOL} SOL</b>",
        f"Maximum LIVE positions: <b>{MAX_OPEN_POSITIONS}</b>",
        f"Maximum signal age: <b>{MAX_SIGNAL_AGE_SECONDS}s</b>",
        "PoolCheck: <b>RugCheck + DexScreener + full reverse + 3x reverse stress</b>",
        "Signed Jupiter transaction simulation: <b>REQUIRED before execute</b>",
        "",
        f"{icon(r.get('entry_execution_active'))} <b>Real-money Solana entry execution: {'READY/ACTIVE' if r.get('entry_execution_active') else 'OFF/BLOCKED'}</b>",
        "",
        "<b>Manual commands</b>",
        "<code>/sibot1solarm on CONFIRM</code>",
        "<code>/sibot1sollive on CONFIRM</code>",
        "<code>/sibot1solauto on CONFIRM</code>",
        "<code>/sibot1solstop</code>",
        "<code>/sibot1solstatus</code>",
    ])


def _send_status(app, tid):
    try:
        _tg.send_message(
            app.telegram_bot_token, str(tid), status_text(app, tid),
            parse_mode="HTML", protect_content=True,
        )
    except Exception:
        pass


def _command(app, tid, text: str) -> bool:
    parts = str(text or "").strip().split()
    if not parts:
        return False
    cmd = parts[0].lower().split("@", 1)[0]
    allowed = {
        "/sibot1solarm", "/sibot1sollive", "/sibot1solauto",
        "/sibot1solstop", "/sibot1solstatus",
    }
    if cmd not in allowed:
        return False
    if not is_master(app.csv_dir, tid):
        _notify(app, tid, "❌ MASTER account required.")
        return True
    if cmd == "/sibot1solstatus":
        _send_status(app, tid)
        return True
    if cmd == "/sibot1solstop":
        set_control(app, tid, armed="false", live_enabled="false", auto_enabled="false")
        _notify(app, tid, "🛑 <b>SiBot 1 Solana bridge stopped.</b>\nNo new Solana entries or exits will be broadcast.")
        _send_status(app, tid)
        return True

    if len(parts) < 2 or parts[1].lower() not in {"on", "off"}:
        _notify(app, tid, f"Usage: <code>{cmd} on CONFIRM</code> or <code>{cmd} off</code>")
        return True
    enable = parts[1].lower() == "on"
    if enable and (len(parts) < 3 or parts[2].upper() != "CONFIRM"):
        _notify(app, tid, "❌ Add <code>CONFIRM</code> to enable a Solana LIVE control.")
        return True

    if cmd == "/sibot1solarm":
        if enable:
            signer_ok, detail, _balance = _signer_and_balance(app, tid)
            account_ok, reason = _account_gate(app, tid)
            if not signer_ok:
                _notify(app, tid, "❌ Cannot arm: Solana signer vault is not ready.\n<code>" + html.escape(detail[:300]) + "</code>")
                return True
            if not account_ok:
                _notify(app, tid, "❌ Cannot arm: <code>" + html.escape(reason[:300]) + "</code>")
                return True
            set_control(app, tid, armed="true")
        else:
            set_control(app, tid, armed="false", live_enabled="false", auto_enabled="false")
    elif cmd == "/sibot1sollive":
        ctl = control(app, tid)
        if enable and not _bool(ctl.get("armed")):
            _notify(app, tid, "❌ Arm Solana first with <code>/sibot1solarm on CONFIRM</code>.")
            return True
        if enable:
            r = readiness(app, tid)
            if not (r.get("signer_ready") and r.get("funded") and r.get("account_ready")):
                _notify(app, tid, "❌ Solana signer, funding and account permission must all be ready before LIVE can be enabled.")
                return True
            set_control(app, tid, live_enabled="true")
        else:
            set_control(app, tid, live_enabled="false", auto_enabled="false")
    elif cmd == "/sibot1solauto":
        ctl = control(app, tid)
        if enable and not (_bool(ctl.get("armed")) and _bool(ctl.get("live_enabled"))):
            _notify(app, tid, "❌ Solana must be ARMED and LIVE before AUTO can be enabled.")
            return True
        if enable:
            r = readiness(app, tid)
            if not (r.get("signer_ready") and r.get("funded") and r.get("account_ready")):
                _notify(app, tid, "❌ Solana signer, funding and account permission are not ready.")
                return True
        set_control(app, tid, auto_enabled="true" if enable else "false")
    _send_status(app, tid)
    return True


def handle_update(app, update):
    message = update.get("message") or {}
    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    if tid is not None and text and _command(app, tid, text):
        return
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_sibot1_solana_live_bridge_installed", False):
        return
    _cli._app = _app_with_bridge
    _ui.handle_update = handle_update
    _ui._sibot1_solana_live_bridge_installed = True
    print("[sibot1-solana-live-bridge] controls-installed default=OFF manual-confirmation-required=true")


install()
