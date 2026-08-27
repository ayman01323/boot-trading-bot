from __future__ import annotations

"""MASTER-menu access to the isolated Google learner wallet store.

The Telegram MASTER bot may receive a learner key transiently in memory, but the
secret is never persisted in the production wallet store.  Telegram must confirm
message deletion before the key is validated and encrypted under the isolated
Google learner paths.
"""

import html
import os
import pwd
from pathlib import Path

from . import telegram as _tg
from . import telegram_ui as _ui
from .solana_wallet_store import SolanaWalletError, SolanaWalletStore

_PREV_MENU = _ui.menu_keyboard
_PREV_HANDLE_UPDATE = _ui.handle_update
_CALLBACKS = {"learnergoogle:home", "learnergoogle:refresh", "learnergoogle:import"}
_PENDING_IMPORT: set[str] = set()
_LEARN_ROOT = Path("/home/ayman01323/BOOT/testingbots/learn")
_LEARN_CSV = _LEARN_ROOT / "CSVbot"
_LEARN_DATA = _LEARN_ROOT / "data"


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


def _learner_wallet_summary(tid) -> list[str]:
    try:
        wallets = _store().list_wallets(tid)
    except Exception:
        wallets = []
    if not wallets:
        return ["Learner wallet: <b>not added yet</b>"]
    try:
        active = _store().get_meta(tid)
    except Exception:
        active = wallets[0]
    address = str(active.get("address") or "")
    signing = _store().has_private_key(tid, active.get("wallet_id"))
    return [
        f"Learner wallet: <code>{html.escape(address)}</code>",
        f"Signing: <b>{'🔐 READY' if signing else '👁 PUBLIC ONLY'}</b>",
        f"Saved learner wallets: <b>{len(wallets)}</b>",
    ]


def learner_page(tid=None) -> str:
    lines = [
        "<b>🧠 LEARNER BOT — GOOGLE TEST</b>",
        "🔒 <b>INSTANCE:</b> LEARNER ONLY • <b>SERVER:</b> botgoogle",
        "⚠️ <b>NOT THE PRODUCTION WALLET</b>",
        "━━━━━━━━━━━━",
        "",
        "Google learner path:",
        "<code>/home/ayman01323/BOOT/testingbots/learn</code>",
        "",
    ]
    if tid is not None:
        lines.extend(_learner_wallet_summary(tid))
        lines.append("")
    lines.extend([
        "<b>🔐 Learner-only private key</b>",
        "Tap <b>🔐 Add Private Key — LEARNER ONLY</b> below.",
        "The next secret message must be deleted successfully by Telegram before it is accepted.",
        "The encrypted key is written only to the learner wallet store under the path above; it is not added to the production wallet registry.",
        "Adding the key does not automatically enable LIVE trading.",
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
    """Keep learner runtime files usable by the normal Google learner account."""
    try:
        acct = pwd.getpwnam("ayman01323")
        paths = [
            _LEARN_CSV / "auto" / "solana_user_wallets.csv",
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
    _ui._send(
        app,
        tid,
        "🔐 <b>LEARNER ONLY — Import Solana Private Key</b>\n"
        "Send the private key in your <b>next message</b>.\n\n"
        "Accepted: base58 64-byte Solana keypair or JSON array of 64 bytes.\n"
        "Seed phrases are NOT accepted.\n\n"
        "Telegram must confirm deletion of your secret message before the learner store accepts it. "
        "The key will be encrypted only under <code>/home/ayman01323/BOOT/testingbots/learn</code>.\n\n"
        "Send <code>cancel</code> to stop.",
    )


def _handle_pending_import(app, message) -> bool:
    tid = (message.get("chat") or {}).get("id")
    if tid is None or str(tid) not in _PENDING_IMPORT:
        return False
    text = str(message.get("text") or "").strip()
    if text.lower() in {"cancel", "/cancel"}:
        _PENDING_IMPORT.discard(str(tid))
        _ui._send(app, tid, learner_page(tid), learner_keyboard())
        return True
    try:
        if not _master(app, tid):
            raise SolanaWalletError("MASTER only")
        if (message.get("chat") or {}).get("type") != "private":
            raise SolanaWalletError("Learner private-key import is allowed only in a private Telegram chat")
        mid = message.get("message_id")
        if not mid or not _tg.delete_message(app.telegram_bot_token, tid, mid):
            raise SolanaWalletError("Telegram did not confirm deletion; learner private key was NOT saved")
        result = _store().save_private_key(
            tid,
            text,
            label="Learner Solana",
            source="telegram-master-learner-only",
        )
        _restore_learner_owner(tid, result.get("wallet_id"))
        _PENDING_IMPORT.discard(str(tid))
        _ui._send(
            app,
            tid,
            "✅ <b>LEARNER PRIVATE KEY SAVED</b>\n"
            "Secret Telegram message deleted before encrypted persistence.\n"
            f"Wallet ID: <code>{html.escape(str(result.get('wallet_id') or ''))}</code>\n"
            f"Public address: <code>{html.escape(str(result.get('address') or ''))}</code>\n"
            "Storage: <b>LEARNER ONLY</b>\n"
            "Production wallet registry: <b>UNCHANGED</b>\n"
            "LIVE trading: <b>NOT automatically enabled</b>",
            learner_keyboard(),
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
        if data == "learnergoogle:import":
            try:
                _answer(app, cb)
                _begin_import(app, tid, ((cb.get("message") or {}).get("chat") or {}).get("type"))
            except Exception as exc:
                _answer(app, cb, "Import unavailable")
                _ui._send(app, tid, f"❌ {html.escape(str(exc))}", learner_keyboard())
            return
        _answer(app, cb, "Refreshed" if data.endswith("refresh") else "")
        _ui._send(app, tid, learner_page(tid), learner_keyboard())
        return

    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    if tid is not None and text.startswith("/"):
        cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if cmd in {"/learnergoogle", "/learnerbot"}:
            if not _master(app, tid):
                _ui._send(app, tid, "MASTER only.", _ui.back_keyboard())
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
