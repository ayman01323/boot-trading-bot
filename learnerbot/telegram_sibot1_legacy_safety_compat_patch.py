from __future__ import annotations

from . import telegram_sibot1_only_menu_patch as _sibot1_menu
from . import telegram_ui as _ui

_PREV = _ui.handle_update


def handle_update(app, update):
    cb = update.get("callback_query")
    if cb:
        data = str(cb.get("data") or "")
        # The current menu exposes only sibot1:* callbacks. Old Telegram messages
        # can still contain legacy sibot:* buttons; preserve their original
        # behaviour so an existing OFF/stop control is never neutralised by a
        # presentation-only migration.
        if data.startswith("sibot:"):
            return _sibot1_menu._PREV_HANDLE_UPDATE(app, update)
    return _PREV(app, update)


def install() -> None:
    if getattr(_ui, "_telegram_sibot1_legacy_safety_compat_installed", False):
        return
    _ui.handle_update = handle_update
    _ui._telegram_sibot1_legacy_safety_compat_installed = True
    print("[telegram-sibot1-menu] legacy stale callbacks preserved for safety")


install()

# SiBot 1 first binds only public wallet addresses for observation. This layer
# never decrypts wallet keys and leaves signing/broadcast disabled in the AI runtime.
from . import telegram_sibot1_watch_wallet_patch as _sibot1_watch_wallet  # noqa: E402,F401

# Final wallet presentation adds a secure private-key import prompt backed by the
# existing encrypted EVM/Solana wallet vaults. Keys are deleted from Telegram before
# persistence and are never copied into GPT/Gemini/Grok. Importing a key changes no
# LIVE, signing or broadcast gate by itself.
from . import telegram_sibot1_signer_menu_patch as _sibot1_signer_menu  # noqa: E402,F401

# Last-line secret hygiene: if an already-stored SiBot 1 private key is pasted
# again after the import session ended, recognise it by its derived wallet address
# and delete the duplicate Telegram message immediately. Existing encrypted signer
# material is left unchanged.
from . import telegram_sibot1_duplicate_secret_scrub_patch as _sibot1_duplicate_secret_scrub  # noqa: E402,F401

# Owner-requested controlled rollback of Solana *strategy policy* to the known-good
# 17-Aug configuration. This loads only after final_runtime_integrity_patch and all
# late presentation/liveness layers have composed. The patch independently verifies
# that current execution, simulation, reserve, exit-circuit and LIQUIDITY_STUCK
# non-blocking safety hooks remain authoritative before enabling the canary policy.
from . import solana_aug17_canary_restore_patch as _solana_aug17_canary  # noqa: E402,F401
