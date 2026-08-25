from __future__ import annotations

import copy
import html

from . import telegram as _tg
from . import telegram_sibot1_only_menu_patch as _sibot1
from . import telegram_sibot1_watch_wallet_patch as _watch
from . import telegram_ui as _ui
from .multi_wallet_store import MultiWalletStore
from .solana_wallet_store import SolanaWalletStore
from .user_registry import is_master

_PREV_HANDLE_UPDATE = _ui.handle_update
_PREV_SIBOT1_KEYBOARD = _sibot1.sibot1_keyboard
_PENDING_SIGNER: dict[str, str] = {}


def _require_master(app, tid) -> None:
    if not is_master(app.csv_dir, tid):
        raise ValueError("MASTER account required to change SiBot 1 signer configuration")


def _evm_store(app) -> MultiWalletStore:
    return MultiWalletStore(app.data_dir, app.csv_dir)


def _sol_store(app) -> SolanaWalletStore:
    return SolanaWalletStore(app.csv_dir, app.data_dir)


def _active_evm_meta(app, tid) -> dict | None:
    try:
        return dict(_evm_store(app).get_meta(tid))
    except Exception:
        return None


def _active_sol_meta(app, tid) -> dict | None:
    try:
        return dict(_sol_store(app).get_meta(tid))
    except Exception:
        return None


def _evm_signer_ready(app, tid) -> bool:
    meta = _active_evm_meta(app, tid)
    if not meta:
        return False
    try:
        return _evm_store(app)._wallet_file(tid, meta.get("wallet_id")).exists()
    except Exception:
        return False


def _sol_signer_ready(app, tid) -> bool:
    try:
        meta = _sol_store(app).get_meta(tid)
        return bool(_sol_store(app).has_private_key(tid, meta.get("wallet_id")))
    except Exception:
        return False


def _fmt_addr(value: str) -> str:
    value = str(value or "")
    if len(value) <= 20:
        return value
    return value[:10] + "…" + value[-8:]


def wallet_page(app, tid) -> str:
    by_chain = {
        str(row.get("chain") or "").lower(): row
        for row in _watch._rows(app)
        if str(row.get("enabled") or "true").lower() in {"1", "true", "yes", "on"}
    }
    evm_meta = _active_evm_meta(app, tid)
    sol_meta = _active_sol_meta(app, tid)
    evm_ready = _evm_signer_ready(app, tid)
    sol_ready = _sol_signer_ready(app, tid)

    lines = [
        "<b>👛 SiBot 1 — Wallets &amp; Protected Signer</b>",
        _sibot1.DIV,
        "",
        "<b>AI execution boundary</b>",
        "GPT / Gemini / Grok private-key access: <b>OFF</b>",
        "Direct SiBot 1 signing: <b>OFF</b> • Broadcast: <b>OFF</b>",
        "",
        "<b>Watch bindings</b>",
    ]
    for chain, label in (("base", "Base / EVM"), ("solana", "Solana")):
        row = by_chain.get(chain)
        if row:
            lines += [
                f"🟢 <b>{label}</b>",
                f"<code>{html.escape(str(row.get('address') or ''))}</code>",
                f"Source: {html.escape(str(row.get('source') or 'watch-only'))}",
            ]
        else:
            lines.append(f"⚪ <b>{label}</b> — not connected")
        lines.append("")

    lines += ["<b>Protected signing vault</b>"]
    if evm_meta:
        lines.append(
            f"{'🔐' if evm_ready else '⚪'} EVM signer: <b>{'KEY STORED' if evm_ready else 'METADATA ONLY'}</b> "
            f"<code>{html.escape(_fmt_addr(evm_meta.get('address') or ''))}</code>"
        )
    else:
        lines.append("⚪ EVM signer: <b>NOT STORED</b>")
    if sol_meta:
        lines.append(
            f"{'🔐' if sol_ready else '⚪'} Solana signer: <b>{'KEY STORED' if sol_ready else 'PUBLIC ONLY'}</b> "
            f"<code>{html.escape(_fmt_addr(sol_meta.get('address') or ''))}</code>"
        )
    else:
        lines.append("⚪ Solana signer: <b>NOT STORED</b>")

    lines += [
        "",
        "Private keys are encrypted in the existing server-side wallet vaults. SiBot 1 stores only public watch bindings and never copies a private key into its GPT/Gemini/Grok runtime.",
        "",
        "⚠️ Adding a key does <b>not</b> enable LIVE trading. LIVE/sign/broadcast gates remain separate.",
    ]
    return "\n".join(lines)


def wallet_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🔐 Import EVM key", "callback_data": "sibot1:signer:import:evm"},
                {"text": "🔐 Import Solana key", "callback_data": "sibot1:signer:import:solana"},
            ],
            [{"text": "🔗 Use current EVM wallet", "callback_data": "sibot1:signer:use:evm"}],
            [{"text": "🔗 Use current Solana wallet", "callback_data": "sibot1:wallet:use:solana"}],
            [
                {"text": "✍️ Add Base address", "callback_data": "sibot1:wallet:add:base"},
                {"text": "✍️ Add Solana address", "callback_data": "sibot1:wallet:add:solana"},
            ],
            [
                {"text": "🗑 Clear Base", "callback_data": "sibot1:wallet:clear:base"},
                {"text": "🗑 Clear Solana", "callback_data": "sibot1:wallet:clear:solana"},
            ],
            [{"text": "🔄 Refresh", "callback_data": "sibot1:wallets"}],
            [{"text": "⬅️ SiBot 1", "callback_data": "sibot1:status"}],
        ]
    }


def sibot1_keyboard() -> dict:
    kb = _PREV_SIBOT1_KEYBOARD()
    rows = list(kb.get("inline_keyboard") or [])
    for row in rows:
        for button in row:
            if str(button.get("callback_data") or "") == "sibot1:wallets":
                button["text"] = "👛 Wallets & signer"
    return {"inline_keyboard": rows}


def _answer(app, cb, text="") -> None:
    cqid = (cb or {}).get("id")
    if not cqid:
        return
    try:
        _ui.answer_callback_query(app.telegram_bot_token, cqid, text)
    except Exception:
        pass


def _render(app, tid, text: str, kb: dict, cb=None) -> None:
    _sibot1._render(app, tid, text, kb, cb)


def _prompt(chain: str) -> str:
    if chain == "evm":
        detail = "Send the EVM private key in your next message. Do not send a seed phrase."
    else:
        detail = "Send the 64-byte Solana keypair in base58 or JSON-array form. Seed phrases are not accepted."
    return "\n".join([
        f"<b>🔐 SiBot 1 — Import {'EVM' if chain == 'evm' else 'Solana'} signing key</b>",
        _sibot1.DIV,
        "",
        detail,
        "",
        "The incoming Telegram message must be deleted successfully before the key is encrypted and stored.",
        "The AI engines will not receive the key.",
        "",
        "Send <code>cancel</code> to stop.",
    ])


def _bind_evm_from_active(app, tid) -> str:
    meta = _active_evm_meta(app, tid)
    if not meta:
        # Backward-compatible fallback to the older single-wallet public metadata.
        return _watch._current_evm_public_address(app)
    address = _watch._validate("base", str(meta.get("address") or ""))
    _watch._set_wallet(
        app,
        "base",
        address,
        source="active-evm-signer-public-metadata",
        label=str(meta.get("label") or "Active EVM signer"),
    )
    return address


def _handle_pending(app, message) -> bool:
    tid = (message.get("chat") or {}).get("id")
    if tid is None:
        return False
    chain = _PENDING_SIGNER.get(str(tid))
    if not chain:
        return False
    if not _ui._auth(app, tid):
        return True
    text = str(message.get("text") or "").strip()
    try:
        _require_master(app, tid)
        if text.lower() in {"cancel", "/cancel"}:
            _PENDING_SIGNER.pop(str(tid), None)
            _ui._send(app, tid, wallet_page(app, tid), wallet_keyboard())
            return True
        if (message.get("chat") or {}).get("type") != "private":
            raise ValueError("Private-key import is allowed only in a private Telegram chat")
        mid = message.get("message_id")
        if not mid or not _tg.delete_message(app.telegram_bot_token, tid, mid):
            raise ValueError("Telegram did not confirm deletion; the private key was NOT saved")

        if chain == "evm":
            if " " in text:
                raise ValueError("Seed phrases are not accepted; send one EVM private key only")
            row = _evm_store(app).save_private_key(
                tid,
                text,
                label="SiBot 1 EVM signer",
                source="sibot1-telegram-import",
            )
            address = _watch._validate("base", row.get("address") or "")
            _watch._set_wallet(
                app,
                "base",
                address,
                source="sibot1-encrypted-evm-signer",
                label="SiBot 1 EVM signer",
            )
            wallet_id = row.get("wallet_id") or ""
        else:
            row = _sol_store(app).save_private_key(
                tid,
                text,
                label="SiBot 1 Solana signer",
                source="sibot1-telegram-import",
            )
            address = _watch._validate("solana", row.get("address") or "")
            _watch._set_wallet(
                app,
                "solana",
                address,
                source="sibot1-encrypted-solana-signer",
                label="SiBot 1 Solana signer",
            )
            wallet_id = row.get("wallet_id") or ""

        _PENDING_SIGNER.pop(str(tid), None)
        _ui._send(
            app,
            tid,
            "\n".join([
                "✅ <b>SiBot 1 signing key stored securely</b>",
                f"Wallet ID: <code>{html.escape(str(wallet_id))}</code>",
                f"Address: <code>{html.escape(str(address))}</code>",
                "",
                "The secret Telegram message was deleted before encrypted persistence.",
                "SiBot 1 AI private-key access remains <b>OFF</b>.",
                "LIVE/sign/broadcast gates were <b>not changed</b>.",
            ]),
            wallet_keyboard(),
        )
        return True
    except Exception as exc:
        _ui._send(
            app,
            tid,
            "❌ <b>Signer import failed</b>\n<code>" + html.escape(str(exc)[:320]) + "</code>\n\nSend a valid key or <code>cancel</code>.",
        )
        return True


def handle_update(app, update):
    message = update.get("message") or {}
    if _handle_pending(app, message):
        return

    cb = update.get("callback_query")
    if cb:
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = str(cb.get("data") or "")
        if data == "sibot1:wallets" or data.startswith("sibot1:signer:"):
            if not _ui._auth(app, tid):
                _answer(app, cb, "Not authorised")
                return
            try:
                if data == "sibot1:wallets":
                    _answer(app, cb)
                    _render(app, tid, wallet_page(app, tid), wallet_keyboard(), cb)
                    return
                _require_master(app, tid)
                parts = data.split(":")
                action = parts[2] if len(parts) > 2 else ""
                chain = parts[3] if len(parts) > 3 else ""
                if action == "import" and chain in {"evm", "solana"}:
                    if ((cb.get("message") or {}).get("chat") or {}).get("type") != "private":
                        raise ValueError("Private-key import is allowed only in a private Telegram chat")
                    _PENDING_SIGNER[str(tid)] = chain
                    _answer(app, cb)
                    _render(
                        app,
                        tid,
                        _prompt(chain),
                        {"inline_keyboard": [[{"text": "Cancel", "callback_data": "sibot1:wallets"}]]},
                        cb,
                    )
                    return
                if action == "use" and chain == "evm":
                    address = _bind_evm_from_active(app, tid)
                    _answer(app, cb)
                    _render(
                        app,
                        tid,
                        "✅ <b>Active EVM wallet bound to SiBot 1</b>\n<code>" + html.escape(address) + "</code>",
                        wallet_keyboard(),
                        cb,
                    )
                    return
            except Exception as exc:
                _answer(app, cb)
                _render(
                    app,
                    tid,
                    "❌ <b>SiBot 1 signer</b>\n<code>" + html.escape(str(exc)[:320]) + "</code>",
                    wallet_keyboard(),
                    cb,
                )
                return

    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_telegram_sibot1_signer_menu_patch_installed", False):
        return
    _sibot1.sibot1_keyboard = sibot1_keyboard
    _ui.handle_update = handle_update
    _ui._telegram_sibot1_signer_menu_patch_installed = True
    print("[telegram-sibot1-signer] installed encrypted-vault=true ai-private-key-access=false live-unchanged=true")


install()
