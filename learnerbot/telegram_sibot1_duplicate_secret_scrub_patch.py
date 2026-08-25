from __future__ import annotations

from eth_account import Account

from . import telegram as _tg
from . import telegram_sibot1_signer_menu_patch as _signer
from . import telegram_ui as _ui
from .solana_wallet_store import parse_solana_private_key

_PREV_HANDLE_UPDATE = _ui.handle_update


def _duplicate_evm(app, tid, text: str) -> bool:
    try:
        address = Account.from_key(str(text or "").strip()).address.lower()
    except Exception:
        return False
    try:
        store = _signer._evm_store(app)
        for row in store.list_wallets(tid):
            if str(row.get("address") or "").lower() != address:
                continue
            try:
                if store._wallet_file(tid, row.get("wallet_id")).exists():
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _duplicate_solana(app, tid, text: str) -> bool:
    try:
        _raw, address = parse_solana_private_key(str(text or "").strip())
    except Exception:
        return False
    try:
        store = _signer._sol_store(app)
        for row in store.list_wallets(tid):
            if str(row.get("address") or "") != address:
                continue
            try:
                if store.has_private_key(tid, row.get("wallet_id")):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _scrub_duplicate(app, message, *, kind: str) -> bool:
    tid = (message.get("chat") or {}).get("id")
    mid = message.get("message_id")
    if tid is None or not mid:
        return False

    splash_id = _signer._show_delete_splash(app, tid)
    try:
        deleted = bool(_tg.delete_message(app.telegram_bot_token, tid, mid))
    except Exception:
        deleted = False

    if deleted:
        _signer._edit_delete_splash(
            app,
            tid,
            splash_id,
            "✅ <b>DUPLICATE SECRET DELETED</b>\n"
            f"Existing encrypted {kind} signer kept. Nothing changed.",
        )
        _signer._clear_delete_splash(app, tid, splash_id, hold_seconds=1.4)
        return True

    _signer._edit_delete_splash(
        app,
        tid,
        splash_id,
        "❌ <b>SECURE DELETE FAILED</b>\n"
        "The repeated private-key message is still visible. Delete it manually now.",
    )
    # Intentionally keep the warning visible when Telegram did not delete the
    # secret; disappearing the warning would falsely suggest the key was removed.
    return True


def handle_update(app, update):
    message = update.get("message") or {}
    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()

    # An active SiBot 1 import is already handled by the signer module, which
    # deletes the incoming secret before validation/persistence. This layer is
    # only for accidental re-entry after the import session has ended.
    if tid is not None and str(tid) not in _signer._PENDING_SIGNER and text:
        chat_type = str((message.get("chat") or {}).get("type") or "")
        if chat_type == "private" and _ui._auth(app, tid):
            try:
                _signer._require_master(app, tid)
                if _duplicate_evm(app, tid, text):
                    _scrub_duplicate(app, message, kind="EVM")
                    return
                if _duplicate_solana(app, tid, text):
                    _scrub_duplicate(app, message, kind="Solana")
                    return
            except Exception:
                pass

    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_telegram_sibot1_duplicate_secret_scrub_installed", False):
        return
    _ui.handle_update = handle_update
    _ui._telegram_sibot1_duplicate_secret_scrub_installed = True
    print("[telegram-sibot1-signer] duplicate-secret-scrub=true")


install()
