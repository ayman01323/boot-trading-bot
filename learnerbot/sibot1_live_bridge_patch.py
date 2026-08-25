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
from . import evm_pool_rug_gate as _evm_rug  # noqa: F401
from . import telegram as _tg
from . import telegram_sibot1_only_menu_patch as _sibot1
from . import telegram_ui as _ui
from .config import load_chains, load_kv_scoped
from .live_executor import LiveTrader
from .multi_wallet_store import MultiWalletStore
from .user_registry import is_master, require_user, user_bool

_PREV_APP = _cli._app
_PREV_HANDLE_UPDATE = _ui.handle_update
_PREV_SIBOT1_KEYBOARD = _sibot1.sibot1_keyboard
_STARTED = False
_START_LOCK = threading.Lock()
_DB_LOCK = threading.RLock()

CONTROL_HEADERS = [
    "telegram_id", "armed", "live_enabled", "auto_enabled", "max_native_per_trade", "updated_epoch"
]
MAX_ENTRY_NATIVE = Decimal("0.001")
DEFAULT_ENTRY_NATIVE = Decimal("0.0005")
MAX_SIGNAL_AGE_SECONDS = 20
MAX_OPEN_POSITIONS = 1
BASE_CHAIN_ID = 8453


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
    return Path(app.csv_dir) / "sibot1" / "live_control.csv"


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
        "max_native_per_trade": str(DEFAULT_ENTRY_NATIVE),
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


def _base_chain(app):
    return next((c for c in load_chains(app, enabled_only=False) if int(c.chain_id) == BASE_CHAIN_ID), None)


def _signer_ready(app, tid) -> tuple[bool, str]:
    try:
        store = MultiWalletStore(app.data_dir, app.csv_dir)
        meta = store.get_meta(tid)
        if not store._wallet_file(tid, meta.get("wallet_id")).exists():
            return False, "encrypted EVM signer file missing"
        return True, str(meta.get("address") or "")
    except Exception as exc:
        return False, str(exc)


def _platform_gates(app, tid) -> dict:
    chain = _base_chain(app)
    if chain is None:
        return {"ready": False, "reason": "Base chain is not configured"}
    try:
        user = require_user(app.csv_dir, tid, active=True, chain_slug=chain.slug)
    except Exception as exc:
        return {"ready": False, "reason": str(exc)}
    live_cfg = load_kv_scoped(Path(app.csv_dir) / "live_trading_settings.csv", chain.chain_id)
    auto_cfg = load_kv_scoped(Path(app.csv_dir) / "auto_trading_settings.csv", chain.chain_id)
    platform_live = _bool(live_cfg.get("trading_enabled"), False)
    platform_auto = _bool(auto_cfg.get("auto_trading_enabled"), False)
    user_live = user_bool(app.csv_dir, tid, chain.chain_id, "live_trading_enabled", False)
    user_auto = user_bool(app.csv_dir, tid, chain.chain_id, "auto_trading_enabled", False)
    can_auto = _bool(user.get("can_auto_trade"), True)
    return {
        "ready": bool(platform_live and platform_auto and user_live and user_auto and can_auto),
        "platform_live": platform_live,
        "platform_auto": platform_auto,
        "user_live": user_live,
        "user_auto": user_auto,
        "can_auto": can_auto,
        "reason": "ok" if platform_live and platform_auto and user_live and user_auto and can_auto else "one or more existing LIVE/AUTO gates are OFF",
    }


def readiness(app, tid) -> dict:
    ctl = control(app, tid)
    signer_ok, signer_detail = _signer_ready(app, tid)
    gates = _platform_gates(app, tid)
    requested = _bool(ctl.get("armed")) and _bool(ctl.get("live_enabled")) and _bool(ctl.get("auto_enabled"))
    return {
        "control": ctl,
        "signer_ready": signer_ok,
        "signer_detail": signer_detail,
        "platform": gates,
        "bridge_ready": bool(signer_ok and gates.get("ready")),
        "entry_execution_requested": requested,
        "entry_execution_active": bool(requested and signer_ok and gates.get("ready")),
        "chain": "base",
    }


def _db(app) -> sqlite3.Connection:
    path = Path(app.data_dir) / "sibot1_live_bridge.sqlite3"
    conn = sqlite3.connect(path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS attempts(
      attempt_key TEXT PRIMARY KEY,
      telegram_id TEXT NOT NULL,
      candidate_id TEXT NOT NULL,
      kind TEXT NOT NULL,
      chain TEXT NOT NULL,
      shadow_lot_id TEXT,
      status TEXT NOT NULL,
      tx_hash TEXT,
      error TEXT,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS positions(
      telegram_id TEXT NOT NULL,
      shadow_lot_id TEXT NOT NULL,
      chain TEXT NOT NULL,
      token TEXT NOT NULL,
      token_raw TEXT NOT NULL,
      token_decimals INTEGER NOT NULL,
      entry_tx TEXT,
      status TEXT NOT NULL,
      updated_at INTEGER NOT NULL,
      PRIMARY KEY(telegram_id,shadow_lot_id)
    );
    """)
    return conn


def _attempt_key(tid, candidate) -> str:
    raw = f"{tid}|{candidate.get('candidate_id')}|{candidate.get('kind')}|{candidate.get('shadow_lot_id')}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _claim(app, tid, candidate) -> tuple[bool, str]:
    key = _attempt_key(tid, candidate)
    now = int(time.time())
    with _DB_LOCK:
        conn = _db(app)
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO attempts(attempt_key,telegram_id,candidate_id,kind,chain,shadow_lot_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (key, str(tid), str(candidate.get("candidate_id") or ""), str(candidate.get("kind") or ""), str(candidate.get("chain") or ""), str(candidate.get("shadow_lot_id") or ""), "CLAIMED", now, now),
            )
            conn.commit()
            return cur.rowcount == 1, key
        finally:
            conn.close()


def _attempt_update(app, key, status, tx_hash="", error="") -> None:
    with _DB_LOCK:
        conn = _db(app)
        try:
            conn.execute(
                "UPDATE attempts SET status=?,tx_hash=?,error=?,updated_at=? WHERE attempt_key=?",
                (str(status), str(tx_hash or ""), str(error or "")[:1200], int(time.time()), str(key)),
            )
            conn.commit()
        finally:
            conn.close()


def _open_count(app, tid) -> int:
    conn = _db(app)
    try:
        return int(conn.execute("SELECT COUNT(*) n FROM positions WHERE telegram_id=? AND status='OPEN'", (str(tid),)).fetchone()["n"])
    finally:
        conn.close()


def _position(app, tid, lot_id):
    conn = _db(app)
    try:
        row = conn.execute("SELECT * FROM positions WHERE telegram_id=? AND shadow_lot_id=? AND status='OPEN'", (str(tid), str(lot_id))).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _save_position(app, tid, lot_id, token, raw, decimals, tx_hash):
    conn = _db(app)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO positions(telegram_id,shadow_lot_id,chain,token,token_raw,token_decimals,entry_tx,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (str(tid), str(lot_id), "base", str(token), str(int(raw)), int(decimals), str(tx_hash or ""), "OPEN", int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()


def _reduce_position(app, tid, lot_id, sold_raw):
    conn = _db(app)
    try:
        row = conn.execute("SELECT token_raw FROM positions WHERE telegram_id=? AND shadow_lot_id=?", (str(tid), str(lot_id))).fetchone()
        if not row:
            return
        remaining = max(0, int(row["token_raw"] or 0) - int(sold_raw))
        conn.execute(
            "UPDATE positions SET token_raw=?,status=?,updated_at=? WHERE telegram_id=? AND shadow_lot_id=?",
            (str(remaining), "CLOSED" if remaining <= 0 else "OPEN", int(time.time()), str(tid), str(lot_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _candidate_age(candidate) -> float:
    try:
        created = int(candidate.get("intent_created_at_ms") or 0) / 1000.0
        return max(0.0, time.time() - created)
    except Exception:
        return 10**9


def _notify(app, tid, text):
    try:
        _tg.send_message(app.telegram_bot_token, str(tid), text, parse_mode="HTML", protect_content=True)
    except Exception:
        pass


def _fixed_entry_size(ctl) -> Decimal:
    amount = _dec(ctl.get("max_native_per_trade"), DEFAULT_ENTRY_NATIVE)
    return min(MAX_ENTRY_NATIVE, max(Decimal("0.0001"), amount))


def _execute_entry(app, tid, candidate, key):
    if _open_count(app, tid) >= MAX_OPEN_POSITIONS:
        raise RuntimeError("SiBot 1 LIVE canary already has the maximum 1 open Base position")
    token = str(candidate.get("asset_out") or "")
    if not token.startswith("0x"):
        raise RuntimeError("Base LIVE candidate has no EVM token contract address")
    trader = LiveTrader(app, "base", telegram_id=tid)
    amount = _fixed_entry_size(control(app, tid))
    _, _, decimals, symbol, before_raw, _ = trader.token_balance(token)
    result = trader.buy(token, amount, "CONFIRM")
    tx_hash = str(result.get("tx_hash") or "")
    _attempt_update(app, key, "BROADCAST", tx_hash)
    try:
        receipt = trader.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=150, poll_latency=2)
    except Exception as exc:
        set_control(app, tid, auto_enabled="false")
        raise RuntimeError(f"entry broadcast {tx_hash} but confirmation timed out; AUTO paused") from exc
    if int(receipt.status) != 1:
        set_control(app, tid, auto_enabled="false")
        raise RuntimeError(f"entry transaction failed on-chain: {tx_hash}; AUTO paused")
    _, _, decimals2, symbol2, after_raw, _ = trader.token_balance(token)
    acquired = max(0, int(after_raw) - int(before_raw))
    if acquired <= 0:
        set_control(app, tid, auto_enabled="false")
        raise RuntimeError(f"entry landed but no positive token balance delta was proven: {tx_hash}; AUTO paused")
    _save_position(app, tid, candidate.get("shadow_lot_id"), token, acquired, decimals2 or decimals, tx_hash)
    _attempt_update(app, key, "EXECUTED", tx_hash)
    _notify(app, tid, f"🚀 <b>SiBot 1 Base CANARY BUY confirmed</b>\nEngine: <b>{html.escape(str(candidate.get('engine_id') or ''))}</b>\nToken: <code>{html.escape(token)}</code>\nSize: <b>{amount} ETH</b>\nReceived: <b>{acquired}</b> raw {html.escape(symbol2 or symbol)}\nTX: <code>{html.escape(tx_hash)}</code>")


def _execute_exit(app, tid, candidate, key):
    pos = _position(app, tid, candidate.get("shadow_lot_id"))
    if not pos:
        _attempt_update(app, key, "NO_LIVE_POSITION")
        return
    trader = LiveTrader(app, "base", telegram_id=tid)
    fraction = min(Decimal(1), max(Decimal("0.0001"), _dec(candidate.get("exit_fraction"), "1")))
    raw = int(pos.get("token_raw") or 0)
    sell_raw = max(1, int(Decimal(raw) * fraction))
    decimals = int(pos.get("token_decimals") or 18)
    human = Decimal(sell_raw) / (Decimal(10) ** decimals)
    result = trader.sell(str(pos.get("token")), f"{human:f}", "CONFIRM")
    tx_hash = str(result.get("tx_hash") or "")
    _attempt_update(app, key, "BROADCAST", tx_hash)
    try:
        receipt = trader.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=150, poll_latency=2)
    except Exception as exc:
        raise RuntimeError(f"exit broadcast {tx_hash} but confirmation timed out; inspect before any retry") from exc
    if int(receipt.status) != 1:
        raise RuntimeError(f"exit transaction failed on-chain: {tx_hash}")
    _reduce_position(app, tid, candidate.get("shadow_lot_id"), sell_raw)
    _attempt_update(app, key, "EXECUTED", tx_hash)
    _notify(app, tid, f"✅ <b>SiBot 1 Base CANARY SELL confirmed</b>\nReason: <code>{html.escape(str(candidate.get('reason') or 'strategy_exit'))}</code>\nFraction: <b>{fraction * 100:.2f}%</b>\nTX: <code>{html.escape(tx_hash)}</code>")


def _process_candidate(app, tid, candidate):
    chain = str(candidate.get("chain") or "").lower()
    if chain != "base":
        return
    if _candidate_age(candidate) > MAX_SIGNAL_AGE_SECONDS:
        return
    ctl = control(app, tid)
    kind = str(candidate.get("kind") or "").upper()
    # New entries require all three explicit SiBot 1 controls. Exits are allowed
    # while LIVE remains on even if AUTO is subsequently paused, so an existing
    # position is not stranded by a new-entry stop.
    if kind == "ENTRY":
        if not (_bool(ctl.get("armed")) and _bool(ctl.get("live_enabled")) and _bool(ctl.get("auto_enabled"))):
            return
    elif kind == "EXIT":
        if not _bool(ctl.get("live_enabled")):
            return
    else:
        return
    ready = readiness(app, tid)
    if not ready.get("signer_ready") or not ready.get("platform", {}).get("ready"):
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
        _attempt_update(app, key, "REJECTED_OR_FAILED", error=f"{type(exc).__name__}: {exc}")
        _notify(app, tid, f"🚨 <b>SiBot 1 LIVE candidate blocked</b>\n<code>{html.escape(type(exc).__name__ + ': ' + str(exc)[:500])}</code>")


def _candidate_rows(app):
    path = Path(app.data_dir) / "sibot1" / "live_candidates.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-2000:]
    except Exception:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                out.append(row)
        except Exception:
            continue
    return out


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
            print("[sibot1-live-bridge] worker", type(exc).__name__, str(exc)[:240])
        time.sleep(2)


def _start(app):
    global _STARTED
    with _START_LOCK:
        if _STARTED:
            return
        threading.Thread(target=_worker, args=(app,), daemon=True, name="sibot1-protected-live-bridge").start()
        _STARTED = True
        print("[sibot1-live-bridge] enabled chain=base default=OFF max_open=1 hard_max_native=0.001")


def _app_with_bridge():
    app = _PREV_APP()
    _start(app)
    return app


def _icon(value):
    return "🟢" if value else "🔴"


def status_text(app, tid):
    r = readiness(app, tid)
    ctl = r["control"]
    p = r["platform"]
    return "\n".join([
        "<b>🚦 SiBot 1 — LIVE Readiness</b>",
        _sibot1.DIV,
        "",
        "<b>Protected execution bridge:</b> Base/EVM canary",
        "AI private-key access: <b>OFF</b>",
        "Solana SiBot 1 LIVE: <b>NOT ENABLED IN THIS CANARY</b>",
        "",
        f"{_icon(_bool(ctl.get('armed')))} SiBot 1 ARMED: <b>{'YES' if _bool(ctl.get('armed')) else 'NO'}</b>",
        f"{_icon(_bool(ctl.get('live_enabled')))} SiBot 1 LIVE: <b>{'YES' if _bool(ctl.get('live_enabled')) else 'NO'}</b>",
        f"{_icon(_bool(ctl.get('auto_enabled')))} SiBot 1 AUTO entries: <b>{'YES' if _bool(ctl.get('auto_enabled')) else 'NO'}</b>",
        f"{_icon(r.get('signer_ready'))} EVM signer vault: <b>{'READY' if r.get('signer_ready') else 'NOT READY'}</b>",
        "",
        "<b>Existing platform gates</b>",
        f"{_icon(p.get('platform_live'))} Platform LIVE",
        f"{_icon(p.get('platform_auto'))} Platform AUTO",
        f"{_icon(p.get('user_live'))} User LIVE",
        f"{_icon(p.get('user_auto'))} User AUTO",
        f"{_icon(p.get('can_auto'))} Account AUTO permission",
        "",
        f"Fixed Base canary size: <b>{_fixed_entry_size(ctl)} ETH</b> (hard maximum {MAX_ENTRY_NATIVE} ETH)",
        f"Maximum SiBot 1 LIVE positions: <b>{MAX_OPEN_POSITIONS}</b>",
        f"Maximum signal age: <b>{MAX_SIGNAL_AGE_SECONDS}s</b>",
        "",
        f"{'🟢' if r.get('entry_execution_active') else '🔴'} <b>Real-money SiBot 1 entry execution: {'READY/ACTIVE' if r.get('entry_execution_active') else 'OFF/BLOCKED'}</b>",
        "",
        "<b>Manual commands</b>",
        "<code>/sibot1arm on CONFIRM</code>",
        "<code>/sibot1live on CONFIRM</code>",
        "<code>/sibot1auto on CONFIRM</code>",
        "<code>/sibot1stop</code> — stop new entries; LIVE exits remain available",
    ])


def live_keyboard():
    return {"inline_keyboard": [
        [{"text": "🔄 Refresh", "callback_data": "sibot1:live"}],
        [{"text": "👛 Wallets & signer", "callback_data": "sibot1:wallets"}],
        [{"text": "⬅️ SiBot 1", "callback_data": "sibot1:status"}],
    ]}


def sibot1_keyboard():
    kb = _PREV_SIBOT1_KEYBOARD()
    rows = list(kb.get("inline_keyboard") or [])
    if not any(any(str(b.get("callback_data") or "") == "sibot1:live" for b in row) for row in rows):
        insert_at = max(0, len(rows) - 2)
        rows.insert(insert_at, [{"text": "🚦 LIVE readiness", "callback_data": "sibot1:live"}])
    return {"inline_keyboard": rows}


def _command(app, tid, text: str) -> bool:
    parts = text.strip().split()
    if not parts:
        return False
    cmd = parts[0].lower().split("@", 1)[0]
    if cmd not in {"/sibot1arm", "/sibot1live", "/sibot1auto", "/sibot1stop"}:
        return False
    if not is_master(app.csv_dir, tid):
        _ui._send(app, tid, "❌ MASTER account required.")
        return True
    if cmd == "/sibot1stop":
        set_control(app, tid, auto_enabled="false")
        _ui._send(app, tid, "🛑 <b>SiBot 1 new LIVE entries stopped</b>\nExisting LIVE remains available for exits.", live_keyboard())
        return True
    if len(parts) < 2 or parts[1].lower() not in {"on", "off"}:
        _ui._send(app, tid, f"Usage: <code>{cmd} on CONFIRM</code> or <code>{cmd} off</code>")
        return True
    enable = parts[1].lower() == "on"
    if enable and (len(parts) < 3 or parts[2].upper() != "CONFIRM"):
        _ui._send(app, tid, "❌ Add <code>CONFIRM</code> to enable a SiBot 1 LIVE control.")
        return True
    if cmd == "/sibot1arm":
        if not enable:
            set_control(app, tid, armed="false", auto_enabled="false")
        else:
            signer_ok, detail = _signer_ready(app, tid)
            if not signer_ok:
                _ui._send(app, tid, "❌ Cannot arm: EVM signing vault is not ready.\n<code>" + html.escape(detail[:300]) + "</code>")
                return True
            set_control(app, tid, armed="true")
    elif cmd == "/sibot1live":
        ctl = control(app, tid)
        if enable and not _bool(ctl.get("armed")):
            _ui._send(app, tid, "❌ Arm SiBot 1 first with <code>/sibot1arm on CONFIRM</code>.")
            return True
        if not enable:
            set_control(app, tid, live_enabled="false", auto_enabled="false")
        else:
            gates = _platform_gates(app, tid)
            if not gates.get("platform_live") or not gates.get("user_live"):
                _ui._send(app, tid, "❌ Existing Platform LIVE and User LIVE gates must already be ON before SiBot 1 LIVE can be requested.")
                return True
            set_control(app, tid, live_enabled="true")
    elif cmd == "/sibot1auto":
        ctl = control(app, tid)
        if enable and not (_bool(ctl.get("armed")) and _bool(ctl.get("live_enabled"))):
            _ui._send(app, tid, "❌ SiBot 1 must be ARMED and LIVE before AUTO can be enabled.")
            return True
        if enable:
            gates = _platform_gates(app, tid)
            if not gates.get("ready"):
                _ui._send(app, tid, "❌ Existing Platform/User LIVE and AUTO gates are not all ready.")
                return True
        set_control(app, tid, auto_enabled="true" if enable else "false")
    _ui._send(app, tid, status_text(app, tid), live_keyboard())
    return True


def handle_update(app, update):
    message = update.get("message") or {}
    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    if tid is not None and text and _command(app, tid, text):
        return
    cb = update.get("callback_query")
    if cb and str(cb.get("data") or "") == "sibot1:live":
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
        if not _ui._auth(app, tid):
            return
        try:
            _ui.answer_callback_query(app.telegram_bot_token, cb.get("id"), "")
        except Exception:
            pass
        _sibot1._render(app, tid, status_text(app, tid), live_keyboard(), cb)
        return
    return _PREV_HANDLE_UPDATE(app, update)


def install():
    _cli._app = _app_with_bridge
    _sibot1.sibot1_keyboard = sibot1_keyboard
    _ui.handle_update = handle_update
    print("[sibot1-live-bridge] controls-installed default=OFF user-confirmation-required=true")


install()
