from __future__ import annotations

"""MASTER-menu access to the isolated Google learner wallet/runtime.

Private keys are persisted only in the isolated learner store.  Telegram must
confirm deletion of the user's secret message before a key is validated and
encrypted.  LIVE controls operate only on the isolated learner CSV/data paths.
"""

import csv
import html
import os
import pwd
import sqlite3
import subprocess
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from . import solana_sibot as _sol
from . import telegram as _tg
from . import telegram_ui as _ui
from .solana_wallet_store import SolanaWalletError, SolanaWalletStore
from .user_registry import (
    activate_user,
    get_user,
    join_user,
    set_user_setting,
    update_user,
    user_bool,
)

_PREV_MENU = _ui.menu_keyboard
_PREV_HANDLE_UPDATE = _ui.handle_update
_CALLBACKS = {
    "learnergoogle:home",
    "learnergoogle:refresh",
    "learnergoogle:import",
    "learnergoogle:live:start",
    "learnergoogle:live:confirm",
    "learnergoogle:live:stop",
}
_PENDING_IMPORT: set[str] = set()
_LEARN_ROOT = Path("/home/ayman01323/BOOT/testingbots/learn")
_LEARN_CSV = _LEARN_ROOT / "CSVbot"
_LEARN_DATA = _LEARN_ROOT / "data"
_RUNTIME_SERVICE = "learnerbot-learn.service"


def _store() -> SolanaWalletStore:
    return SolanaWalletStore(_LEARN_CSV, _LEARN_DATA)


def _button_exists(rows) -> bool:
    for row in rows:
        for button in row:
            if str(button.get("callback_data") or "") == "learnergoogle:home":
                return True
            if str(button.get("text") or "").startswith("🧠 Learner Bot"):
                return True
    return False


def menu_keyboard(app=None, chat_id=None):
    keyboard = _PREV_MENU(app, chat_id)
    rows = list(keyboard.get("inline_keyboard") or [])
    if app is None or chat_id is None:
        return {"inline_keyboard": rows}
    try:
        if not _ui._master(app, chat_id):
            return {"inline_keyboard": rows}
    except Exception:
        return {"inline_keyboard": rows}

    if _button_exists(rows):
        return {"inline_keyboard": rows}

    row = [{"text": "🧠 Learner Bot — Google Test", "callback_data": "learnergoogle:home"}]
    insert_at = 1
    for i, existing in enumerate(rows):
        if any(str(b.get("callback_data") or "") == "sr:home" for b in existing):
            insert_at = i + 1
            break
    rows.insert(min(insert_at, len(rows)), row)
    return {"inline_keyboard": rows}


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except Exception:
        return []


def _learner_settings() -> dict:
    out = {}
    for row in _rows(_LEARN_CSV / "solana_settings.csv"):
        key = str(row.get("setting") or "").strip()
        if key:
            out[key] = str(row.get("value") or "").strip()
    return out


def _dec(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default))


def _trade_limits() -> tuple[Decimal, Decimal]:
    cfg = _learner_settings()
    trade = max(Decimal("0.0005"), min(Decimal("0.005"), _dec(cfg.get("live_trade_sol"), "0.005")))
    reserve = max(Decimal("0.005"), _dec(cfg.get("live_min_sol_reserve"), "0.02"))
    return trade, reserve


def _balance(address: str) -> Decimal | None:
    if not address:
        return None
    cfg = _learner_settings()
    rpc = str(cfg.get("rpc_url") or _sol.DEFAULT_RPC).strip()
    try:
        response = requests.post(
            rpc,
            json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [address, {"commitment": "confirmed"}]},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        return Decimal(int(((payload.get("result") or {}).get("value")) or 0)) / Decimal(1_000_000_000)
    except Exception:
        return None


def _runtime_active() -> bool:
    try:
        p = subprocess.run(
            ["/usr/bin/systemctl", "is-active", "--quiet", _RUNTIME_SERVICE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
        return p.returncode == 0
    except Exception:
        return False


def _start_runtime() -> None:
    try:
        p = subprocess.run(
            ["/usr/bin/systemctl", "start", _RUNTIME_SERVICE],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception as exc:
        raise SolanaWalletError(f"Cannot start isolated learner runtime: {type(exc).__name__}") from exc
    if p.returncode != 0 or not _runtime_active():
        detail = (p.stderr or p.stdout or "service did not become active").strip().replace("\n", " ")[:300]
        raise SolanaWalletError(f"Isolated learner runtime failed to start: {detail}")


def _entry_enabled(tid) -> bool:
    return user_bool(_LEARN_CSV, tid, _sol.SOLANA_CHAIN_ID, "learner_new_entries_enabled", False)


def _live_gate(tid) -> bool:
    return user_bool(_LEARN_CSV, tid, _sol.SOLANA_CHAIN_ID, "solana_live_enabled", False)


def _ensure_learner_user(tid) -> dict:
    user = get_user(_LEARN_CSV, tid)
    if user is None:
        join_user(_LEARN_CSV, tid, "STANDARD")
        user = activate_user(_LEARN_CSV, tid, "STANDARD", "Activated from Google learner MASTER menu")
    elif str(user.get("status") or "").upper() != "ACTIVE":
        user = activate_user(_LEARN_CSV, tid, user.get("fee_plan_id") or "STANDARD", "Activated from Google learner MASTER menu")
    user = update_user(
        _LEARN_CSV,
        tid,
        allowed_chains="*",
        can_auto_trade="true",
        can_manual_trade="true",
    )
    return user


def _prepare_live_user(tid) -> None:
    _ensure_learner_user(tid)
    set_user_setting(
        _LEARN_CSV,
        tid,
        "sibot_enabled",
        "true",
        chain_id="*",
        description="Isolated learner monitoring enabled from MASTER Telegram",
    )
    set_user_setting(
        _LEARN_CSV,
        tid,
        "solana_live_enabled",
        "true",
        chain_id=str(_sol.SOLANA_CHAIN_ID),
        description="Isolated learner LIVE execution gate; exits remain available when new entries are stopped",
    )


def _set_entries(tid, enabled: bool) -> None:
    _prepare_live_user(tid)
    set_user_setting(
        _LEARN_CSV,
        tid,
        "learner_new_entries_enabled",
        "true" if enabled else "false",
        chain_id=str(_sol.SOLANA_CHAIN_ID),
        description="Isolated learner new BUY entry gate; OFF preserves LIVE exit monitoring",
    )


def _open_live_positions(tid) -> int:
    path = _LEARN_DATA / "solana_sibot.sqlite3"
    if not path.exists():
        return 0
    try:
        conn = sqlite3.connect(path, timeout=2)
        row = conn.execute(
            "SELECT COUNT(*) FROM positions WHERE telegram_id=? AND status='OPEN' AND mode='LIVE'",
            (str(tid),),
        ).fetchone()
        conn.close()
        return int((row or [0])[0] or 0)
    except Exception:
        return 0


def _learner_wallet_summary(tid) -> list[str]:
    try:
        wallets = _store().list_wallets(tid)
    except Exception:
        wallets = []
    if not wallets:
        return ["Learner wallet: <b>not added yet</b>", "Signing: <b>❌ NOT READY</b>"]
    try:
        active = _store().get_meta(tid)
    except Exception:
        active = wallets[0]
    address = str(active.get("address") or "")
    signing = _store().has_private_key(tid, active.get("wallet_id"))
    balance = _balance(address)
    balance_line = f"Balance: <b>{balance:.9f} SOL</b>" if balance is not None else "Balance: <b>unavailable</b>"
    return [
        f"Learner wallet: <code>{html.escape(address)}</code>",
        f"Signing: <b>{'🔐 READY' if signing else '👁 PUBLIC ONLY'}</b>",
        balance_line,
        f"Saved learner wallets: <b>{len(wallets)}</b>",
    ]


def learner_page(tid=None) -> str:
    runtime = _runtime_active()
    entries = bool(tid is not None and _entry_enabled(tid))
    live_gate = bool(tid is not None and _live_gate(tid))
    open_positions = _open_live_positions(tid) if tid is not None else 0
    trade, reserve = _trade_limits()
    if entries and runtime:
        trading = "🟢 <b>LIVE — NEW ENTRIES ON</b>"
    elif entries:
        trading = "🟡 <b>ENTRY ARMED — RUNTIME OFF</b>"
    else:
        trading = "🔴 <b>NEW ENTRIES OFF</b>"
    exits = "🟢 ACTIVE" if runtime and live_gate else "⚪ STANDBY"
    lines = [
        "<b>🧠 LEARNER BOT — GOOGLE TEST</b>",
        "🔒 <b>INSTANCE:</b> LEARNER ONLY • <b>SERVER:</b> botgoogle",
        "⚠️ <b>NOT THE PRODUCTION WALLET</b>",
        "━━━━━━━━━━━━",
        "",
        f"Trading: {trading}",
        f"Exit protection: <b>{exits}</b>",
        f"Open LIVE positions: <b>{open_positions}</b>",
        f"Trade size: <b>{trade} SOL</b>",
        f"Untouched reserve: <b>{reserve} SOL</b>",
        f"Runtime: <b>{'🟢 ACTIVE' if runtime else '🔴 STOPPED'}</b>",
        "",
    ]
    if tid is not None:
        lines.extend(_learner_wallet_summary(tid))
        lines.append("")
    lines.extend([
        "<b>🔐 Learner-only private key</b>",
        "The secret Telegram message is deleted before validation and encrypted persistence.",
        "The encrypted key is stored only under <code>/home/ayman01323/BOOT/testingbots/learn</code>.",
        "",
        "<b>Trading controls</b>",
        "START requires a second CONFIRM LIVE step and checks signing + wallet funding.",
        "STOP blocks new BUY entries immediately while keeping the learner runtime available to monitor/exit existing LIVE positions.",
    ])
    return "\n".join(lines)


def learner_keyboard(tid=None) -> dict:
    rows = [[{"text": "🔐 Add Private Key — LEARNER ONLY", "callback_data": "learnergoogle:import"}]]
    if tid is not None and _entry_enabled(tid):
        rows.append([{"text": "⏹ STOP LEARNER TRADING", "callback_data": "learnergoogle:live:stop"}])
    else:
        rows.append([{"text": "▶️ START LEARNER TRADING", "callback_data": "learnergoogle:live:start"}])
    rows.extend([
        [{"text": "🔄 Refresh", "callback_data": "learnergoogle:refresh"}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
    ])
    return {"inline_keyboard": rows}


def _answer(app, cb, text="") -> None:
    qid = str((cb or {}).get("id") or "")
    if not qid:
        return
    try:
        _tg.answer_callback_query(app.telegram_bot_token, qid, text)
    except Exception:
        pass


def _master(app, tid) -> bool:
    try:
        return bool(_ui._master(app, tid))
    except Exception:
        return False


def _restore_learner_owner(tid, wallet_id) -> None:
    try:
        acct = pwd.getpwnam("ayman01323")
        paths = [
            _LEARN_CSV / "auto" / "solana_user_wallets.csv",
            _LEARN_CSV / "users.csv",
            _LEARN_CSV / "user_trading_settings.csv",
            _LEARN_DATA / ".solana_wallet_store.key",
            _LEARN_DATA / "user_solana_wallets" / str(tid),
            _LEARN_DATA / "user_solana_wallets" / str(tid) / f"{wallet_id}.enc.json",
        ]
        for path in paths:
            if not path.exists():
                continue
            os.chown(path, acct.pw_uid, acct.pw_gid)
            if path.is_dir():
                for child in path.iterdir():
                    try:
                        os.chown(child, acct.pw_uid, acct.pw_gid)
                    except Exception:
                        pass
    except Exception:
        pass


def _begin_import(app, tid, chat_type) -> None:
    if str(chat_type or "") != "private":
        raise SolanaWalletError("Learner private-key import is allowed only in a private Telegram chat")
    _PENDING_IMPORT.add(str(tid))
    _tg.send_message(
        app.telegram_bot_token,
        str(tid),
        "🔐 <b>LEARNER ONLY — Import Solana Private Key</b>\n"
        "Send the private key in your <b>next message</b>.\n\n"
        "Accepted: base58 64-byte Solana keypair or JSON array of 64 bytes.\n"
        "Seed phrases are NOT accepted.\n\n"
        "Your secret message will disappear as soon as Telegram confirms deletion. "
        "Only then will the learner validate and encrypt it.\n\n"
        "Send <code>cancel</code> to stop.",
        parse_mode="HTML",
        protect_content=True,
    )


def _handle_pending_import(app, message) -> bool:
    tid = (message.get("chat") or {}).get("id")
    if tid is None or str(tid) not in _PENDING_IMPORT:
        return False
    text = str(message.get("text") or "").strip()
    if text.lower() in {"cancel", "/cancel"}:
        _PENDING_IMPORT.discard(str(tid))
        _ui._send(app, tid, learner_page(tid), learner_keyboard(tid))
        return True
    try:
        if not _master(app, tid):
            raise SolanaWalletError("MASTER only")
        if (message.get("chat") or {}).get("type") != "private":
            raise SolanaWalletError("Learner private-key import is allowed only in a private Telegram chat")
        mid = message.get("message_id")
        if not mid or not _tg.delete_message(app.telegram_bot_token, tid, mid):
            raise SolanaWalletError("Telegram did not confirm deletion; learner private key was NOT saved")
        _ensure_learner_user(tid)
        result = _store().save_private_key(
            tid,
            text,
            label="Learner Solana",
            source="telegram-master-learner-only",
        )
        _restore_learner_owner(tid, result.get("wallet_id"))
        _PENDING_IMPORT.discard(str(tid))
        _tg.send_message(
            app.telegram_bot_token,
            str(tid),
            "✅ <b>LEARNER PRIVATE KEY SAVED</b>\n"
            "Secret Telegram message deleted before encrypted persistence.\n"
            f"Public address: <code>{html.escape(str(result.get('address') or ''))}</code>\n"
            "Signing: <b>🔐 READY</b>\n"
            "Storage: <b>LEARNER ONLY</b>\n"
            "Production wallet registry: <b>UNCHANGED</b>\n"
            "Trading remains <b>OFF</b> until START + CONFIRM LIVE.",
            parse_mode="HTML",
            protect_content=True,
            reply_markup=learner_keyboard(tid),
        )
    except Exception as exc:
        _ui._send(
            app,
            tid,
            f"❌ {html.escape(str(exc))}\n"
            "The key was not added to the production wallet store. "
            "Send a valid learner key again or <code>cancel</code>.",
        )
    return True


def _preflight_live(tid) -> tuple[dict, Decimal, Decimal, Decimal]:
    _ensure_learner_user(tid)
    meta = _store().get_meta(tid)
    if not _store().has_private_key(tid, meta.get("wallet_id")):
        raise SolanaWalletError("Learner wallet is not 🔐 READY. Add its private key first.")
    trade, reserve = _trade_limits()
    balance = _balance(str(meta.get("address") or ""))
    if balance is None:
        raise SolanaWalletError("Cannot confirm learner wallet SOL balance right now")
    required = trade + reserve
    if balance < required:
        raise SolanaWalletError(f"Need at least {required:.6f} SOL for trade + reserve; wallet has {balance:.6f} SOL")
    return meta, balance, trade, reserve


def _show_live_confirmation(app, tid) -> None:
    meta, balance, trade, reserve = _preflight_live(tid)
    text = "\n".join([
        "<b>⚠️ CONFIRM LEARNER LIVE TRADING</b>",
        "━━━━━━━━━━━━",
        "This applies only to the isolated Google learner.",
        f"Wallet: <code>{html.escape(str(meta.get('address') or ''))}</code>",
        f"Balance: <b>{balance:.9f} SOL</b>",
        f"Trade size: <b>{trade} SOL</b>",
        f"Untouched reserve: <b>{reserve} SOL</b>",
        "Max positions: <b>1</b> unless the learner settings explicitly say otherwise.",
        "Signed simulation and the existing Solana safety/risk gates remain required.",
        "",
        "Press <b>🚀 CONFIRM LIVE — LEARNER ONLY</b> to allow new learner BUY entries.",
    ])
    kb = {"inline_keyboard": [
        [{"text": "🚀 CONFIRM LIVE — LEARNER ONLY", "callback_data": "learnergoogle:live:confirm"}],
        [{"text": "Cancel", "callback_data": "learnergoogle:home"}],
    ]}
    _tg.send_message(app.telegram_bot_token, str(tid), text, parse_mode="HTML", protect_content=True, reply_markup=kb)


def handle_update(app, update):
    message = update.get("message") or {}
    if message and _handle_pending_import(app, message):
        return

    cb = update.get("callback_query") or {}
    data = str(cb.get("data") or "")
    tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
    if tid is not None and data in _CALLBACKS:
        if not _master(app, tid):
            _answer(app, cb, "MASTER only")
            return
        chat_type = ((cb.get("message") or {}).get("chat") or {}).get("type")
        if data == "learnergoogle:import":
            try:
                _answer(app, cb)
                _begin_import(app, tid, chat_type)
            except Exception as exc:
                _answer(app, cb, "Import unavailable")
                _ui._send(app, tid, f"❌ {html.escape(str(exc))}", learner_keyboard(tid))
            return
        if data == "learnergoogle:live:start":
            try:
                if str(chat_type or "") != "private":
                    raise SolanaWalletError("Learner LIVE can only be changed in a private Telegram chat")
                _answer(app, cb, "Review learner LIVE")
                _show_live_confirmation(app, tid)
            except Exception as exc:
                _answer(app, cb, "LIVE unavailable")
                _tg.send_message(app.telegram_bot_token, str(tid), f"🚨 <b>Learner LIVE not enabled</b>\n<code>{html.escape(str(exc))}</code>", parse_mode="HTML", protect_content=True)
            return
        if data == "learnergoogle:live:confirm":
            try:
                if str(chat_type or "") != "private":
                    raise SolanaWalletError("Learner LIVE can only be changed in a private Telegram chat")
                _preflight_live(tid)
                # Fail closed: prepare LIVE/exit gates with new entries OFF, start the
                # isolated runtime, then open the learner-only entry gate last.
                _set_entries(tid, False)
                _start_runtime()
                _set_entries(tid, True)
                _answer(app, cb, "Learner LIVE started")
                _tg.send_message(
                    app.telegram_bot_token,
                    str(tid),
                    "🚀 <b>LEARNER LIVE IS ON</b>\n"
                    "New qualifying learner BUY entries are enabled.\n"
                    "Instance: <b>LEARNER ONLY</b>\n"
                    "Production wallet/trading settings: <b>UNCHANGED</b>\n"
                    "Use <b>⏹ STOP LEARNER TRADING</b> to block new entries while keeping exit monitoring available.",
                    parse_mode="HTML",
                    protect_content=True,
                    reply_markup=learner_keyboard(tid),
                )
            except Exception as exc:
                try:
                    _set_entries(tid, False)
                except Exception:
                    pass
                _answer(app, cb, "LIVE failed")
                _tg.send_message(app.telegram_bot_token, str(tid), f"🚨 <b>Learner LIVE not enabled</b>\n<code>{html.escape(str(exc)[:700])}</code>", parse_mode="HTML", protect_content=True)
            return
        if data == "learnergoogle:live:stop":
            try:
                _set_entries(tid, False)
                _answer(app, cb, "New learner entries stopped")
                _tg.send_message(
                    app.telegram_bot_token,
                    str(tid),
                    "⏹ <b>LEARNER NEW ENTRIES OFF</b>\n"
                    "No new learner BUY entries are allowed.\n"
                    "The isolated runtime is left available so existing LIVE positions can continue to be monitored and exited by the safety/leader rules.",
                    parse_mode="HTML",
                    protect_content=True,
                    reply_markup=learner_keyboard(tid),
                )
            except Exception as exc:
                _answer(app, cb, "Stop failed")
                _ui._send(app, tid, f"❌ {html.escape(str(exc))}", learner_keyboard(tid))
            return
        _answer(app, cb, "Refreshed" if data.endswith("refresh") else "")
        _ui._send(app, tid, learner_page(tid), learner_keyboard(tid))
        return

    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    if tid is not None and text.startswith("/"):
        cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if cmd in {"/learnergoogle", "/learnerbot"}:
            if not _master(app, tid):
                _ui._send(app, tid, "MASTER only.", _ui.back_keyboard())
                return
            _ui._send(app, tid, learner_page(tid), learner_keyboard(tid))
            return
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_google_learner_launcher_installed", False):
        return
    _ui.menu_keyboard = menu_keyboard
    _ui.handle_update = handle_update
    _ui._google_learner_launcher_installed = True


install()
