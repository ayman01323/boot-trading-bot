from __future__ import annotations

"""Single-poller Telegram bridge for isolated Google learner wallets.

Uses the existing Telegram bot process, but persists every learner key only under
/home/ayman01323/BOOT/testingbots/learn.  Each Telegram ID gets an isolated wallet
store.  Secret messages must be deleted successfully before validation/persistence.
"""

import html
from pathlib import Path

from . import telegram as _tg
from . import telegram_ui as _ui
from .solana_wallet_store import SolanaWalletError, SolanaWalletStore
from .user_registry import get_user

_PREV_MENU = _ui.menu_keyboard
_PREV_HANDLE_UPDATE = _ui.handle_update
_PENDING_IMPORT: set[str] = set()
_CALLBACKS = {"learnergoogle:home", "learnergoogle:import", "learnergoogle:refresh"}
_ROOT = Path("/home/ayman01323/BOOT/testingbots/learn")
_CSV = _ROOT / "CSVbot"
_DATA = _ROOT / "data"


def _store() -> SolanaWalletStore:
    return SolanaWalletStore(_CSV, _DATA)


def _authorised(app, tid) -> bool:
    try:
        return bool(_ui._auth(app, tid))
    except Exception:
        return False


def _learner_registered(tid) -> bool:
    try:
        return get_user(_CSV, tid) is not None
    except Exception:
        return False


def _button_exists(rows) -> bool:
    return any(
        str(button.get("callback_data") or "") == "learnergoogle:home"
        for row in rows for button in row
    )


def menu_keyboard(app=None, chat_id=None):
    keyboard = _PREV_MENU(app, chat_id)
    rows = list(keyboard.get("inline_keyboard") or [])
    if app is None or chat_id is None or not _authorised(app, chat_id) or _button_exists(rows):
        return {"inline_keyboard": rows}
    rows.insert(1, [{"text": "🧠 Learner Bot — Google Test", "callback_data": "learnergoogle:home"}])
    return {"inline_keyboard": rows}


def _wallet_lines(tid) -> list[str]:
    try:
        wallets = _store().list_wallets(tid)
    except Exception:
        wallets = []
    if not wallets:
        return ["Learner wallet: <b>not added</b>", "Signing: <b>❌ NOT READY</b>"]
    try:
        meta = _store().get_meta(tid)
    except Exception:
        meta = wallets[0]
    address = str(meta.get("address") or "")
    signing = _store().has_private_key(tid, meta.get("wallet_id"))
    return [
        f"Learner wallet: <code>{html.escape(address)}</code>",
        f"Signing: <b>{'🔐 READY' if signing else '👁 PUBLIC ONLY'}</b>",
        f"Saved learner wallets: <b>{len(wallets)}</b>",
    ]


def learner_page(tid) -> str:
    lines = [
        "<b>🧠 LEARNER BOT — GOOGLE TEST</b>",
        "🔒 Wallet store: <b>LEARNER ONLY</b>",
        "⚠️ Production wallet store is not used.",
        "━━━━━━━━━━━━",
        "",
    ]
    lines.extend(_wallet_lines(tid))
    lines.extend([
        "",
        "Each Telegram user has a separate learner wallet namespace.",
        "Tap <b>🔐 Add Private Key</b>; send the key only in the next private-chat message.",
        "The message must be deleted successfully before the key is validated and encrypted.",
        "",
        "Trading is <b>not enabled</b> merely by importing a key.",
    ])
    return "\n".join(lines)


def learner_keyboard() -> dict:
    return {"inline_keyboard": [
        [{"text": "🔐 Add Private Key — LEARNER ONLY", "callback_data": "learnergoogle:import"}],
        [{"text": "🔄 Refresh", "callback_data": "learnergoogle:refresh"}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
    ]}


def _answer(app, cb, text="") -> None:
    qid = str((cb or {}).get("id") or "")
    if qid:
        try:
            _tg.answer_callback_query(app.telegram_bot_token, qid, text)
        except Exception:
            pass


def _begin_import(app, tid, chat_type) -> None:
    if str(chat_type or "") != "private":
        raise SolanaWalletError("Private-key import is allowed only in a private Telegram chat")
    if not _learner_registered(tid):
        raise SolanaWalletError("This Telegram ID is not registered in the learner yet. Use /join in the learner account first.")
    _PENDING_IMPORT.add(str(tid))
    _tg.send_message(
        app.telegram_bot_token,
        str(tid),
        "🔐 <b>LEARNER ONLY — Import Solana Private Key</b>\n"
        "Send the private key in your <b>next message</b>.\n\n"
        "Accepted: base58 64-byte Solana keypair or JSON array of 64 bytes.\n"
        "Seed phrases are NOT accepted.\n\n"
        "The message must be deleted before encrypted persistence.\n"
        "Send <code>cancel</code> to stop.",
        parse_mode="HTML",
        protect_content=True,
    )


def _pending_import(app, message) -> bool:
    tid = (message.get("chat") or {}).get("id")
    if tid is None or str(tid) not in _PENDING_IMPORT:
        return False
    text = str(message.get("text") or "").strip()
    if text.lower() in {"cancel", "/cancel"}:
        _PENDING_IMPORT.discard(str(tid))
        _ui._send(app, tid, learner_page(tid), learner_keyboard())
        return True
    try:
        if not _authorised(app, tid):
            raise SolanaWalletError("Not authorised")
        if str((message.get("chat") or {}).get("type") or "") != "private":
            raise SolanaWalletError("Private-key import is allowed only in a private Telegram chat")
        mid = message.get("message_id")
        if not mid or not _tg.delete_message(app.telegram_bot_token, tid, mid):
            raise SolanaWalletError("Telegram did not confirm message deletion; private key was NOT saved")
        result = _store().save_private_key(
            tid,
            text,
            label="Learner Solana",
            source="existing-telegram-single-poller",
        )
        _PENDING_IMPORT.discard(str(tid))
        _tg.send_message(
            app.telegram_bot_token,
            str(tid),
            "✅ <b>LEARNER PRIVATE KEY SAVED</b>\n"
            "Secret message deleted before encrypted persistence.\n"
            f"Public address: <code>{html.escape(str(result.get('address') or ''))}</code>\n"
            "Storage: <b>GOOGLE LEARNER ONLY</b>\n"
            "Production wallet registry: <b>UNCHANGED</b>\n"
            "Trading: <b>OFF until separately armed</b>.",
            parse_mode="HTML",
            protect_content=True,
            reply_markup=learner_keyboard(),
        )
    except Exception as exc:
        _ui._send(app, tid, f"❌ {html.escape(str(exc)[:600])}\nThe learner key was not saved.")
    return True


def handle_update(app, update):
    message = update.get("message") or {}
    if message and _pending_import(app, message):
        return

    cb = update.get("callback_query") or {}
    data = str(cb.get("data") or "")
    tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
    if tid is not None and data in _CALLBACKS:
        if not _authorised(app, tid):
            _answer(app, cb, "Not authorised")
            return
        if data == "learnergoogle:import":
            try:
                _answer(app, cb)
                _begin_import(app, tid, ((cb.get("message") or {}).get("chat") or {}).get("type"))
            except Exception as exc:
                _answer(app, cb, "Import unavailable")
                _ui._send(app, tid, f"❌ {html.escape(str(exc)[:500])}", learner_keyboard())
            return
        _answer(app, cb, "Refreshed" if data.endswith("refresh") else "")
        _ui._send(app, tid, learner_page(tid), learner_keyboard())
        return

    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    if tid is not None and text.startswith("/"):
        cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if cmd in {"/learnergoogle", "/learnerbot"}:
            if not _authorised(app, tid):
                _ui._send(app, tid, "Not authorised.")
                return
            _ui._send(app, tid, learner_page(tid), learner_keyboard())
            return
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_google_learner_launcher_installed", False):
        return
    _ui.menu_keyboard = menu_keyboard
    _ui.handle_update = handle_update
    _ui._google_learner_launcher_installed = True


install()
