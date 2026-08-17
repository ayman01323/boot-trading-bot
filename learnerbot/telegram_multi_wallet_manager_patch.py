from __future__ import annotations

import html

from . import telegram as _tg
from . import telegram_ui as _ui
from . import telegram_solana_wallet_patch as _solui
from .multi_wallet_store import MultiWalletError, MultiWalletStore

_PREV_HANDLE_UPDATE = _ui.handle_update
_PREV_SET_COMMANDS = _ui.set_commands
_PENDING_EVM_IMPORT: set[str] = set()


def _evm_store(app):
    return MultiWalletStore(app.data_dir, app.csv_dir)


def _short(value):
    value = str(value or "")
    return value if len(value) <= 18 else f"{value[:8]}…{value[-6:]}"


def wallet_hub_page(app, tid):
    user = _ui.require_user(app.csv_dir, tid, active=False)
    evm = _evm_store(app).list_wallets(tid)
    sol = _solui._store(app).list_wallets(tid)
    L = [
        "<b>🔐 MY WALLETS</b>",
        "━━━━━━━━━━━━",
        f"Account: <b>{html.escape((user.get('status') or '').upper())}</b>",
        "",
        f"🔷 <b>EVM wallets:</b> {len(evm)}",
    ]
    if evm:
        active = _evm_store(app).get_meta(tid)
        L.append(f"Active EVM: <b>{html.escape(active.get('label') or active.get('wallet_id') or '')}</b> <code>{html.escape(_short(active.get('address')))}</code>")
    else:
        L.append("Active EVM: <b>none</b>")
    L += [
        "Used for Ethereum, BSC, Polygon, Base and Arbitrum.",
        "",
        f"🟣 <b>Solana wallets:</b> {len(sol)}",
    ]
    if sol:
        active = _solui._store(app).get_meta(tid)
        signing = _solui._store(app).has_private_key(tid, active.get("wallet_id"))
        L.append(f"Active Solana: <b>{html.escape(active.get('label') or active.get('wallet_id') or '')}</b> <code>{html.escape(_short(active.get('address')))}</code>")
        L.append(f"Solana signing authority: <b>{'🔐 READY' if signing else '👁 PUBLIC ONLY'}</b>")
    else:
        L.append("Active Solana: <b>none</b>")
    L += [
        "Solana has its own separate address and active-wallet selection.",
        "",
        "You may save multiple wallets of both types. Only one EVM wallet and one Solana wallet are active at a time.",
        "",
        "🔐 <b>Private-key import is available for BOTH EVM and Solana.</b> Import is allowed only in a private Telegram chat. The incoming secret message must be deleted successfully before the key is persisted encrypted server-side. Seed phrases are not accepted.",
        "",
        "⚠️ An imported Solana signing key is stored for future LIVE capability, but Solana SiBot remains SHADOW-only until its transaction signing/broadcast engine is separately enabled.",
    ]
    return "\n".join(L)


def wallet_hub_keyboard():
    return {"inline_keyboard": [
        [{"text": "🔷 EVM Wallets", "callback_data": "evmwallet:open"}, {"text": "🟣 Solana Wallets", "callback_data": "solwallet:open"}],
        [{"text": "⬅️ Menu", "callback_data": "menu:home"}],
    ]}


def evmwallet_page(app, tid):
    _ui.require_user(app.csv_dir, tid, active=False)
    store = _evm_store(app)
    rows = store.list_wallets(tid)
    L = [
        "<b>🔷 EVM WALLETS</b>",
        "━━━━━━━━━━━━",
        "One EVM wallet address works across enabled EVM chains. You can save several and choose which one SiBot/LIVE uses.",
        "",
    ]
    if not rows:
        L += ["No EVM wallet configured.", ""]
    else:
        for r in rows:
            active = str(r.get("active") or "").lower() == "true"
            mark = "✅ ACTIVE" if active else "▫️"
            L += [
                f"{mark} <b>{html.escape(r.get('label') or r.get('wallet_id') or '')}</b> — <code>{html.escape(r.get('wallet_id') or '')}</code>",
                f"<code>{html.escape(r.get('address') or '')}</code>",
                "",
            ]
    L += [
        "<b>Add wallets</b>",
        "• <b>Create</b>: bot generates a new dedicated EVM wallet and encrypts its private key server-side.",
        "• <b>Import Private Key</b>: tap the button below, then send the EVM private key in the next message. Telegram must delete that secret message before encrypted persistence.",
        "",
        "Existing commands still work: <code>/walletcreate</code>, <code>/walletimport</code>, <code>/walletuse</code>, <code>/walletremove</code>.",
    ]
    return "\n".join(L)


def evmwallet_keyboard(app, tid):
    rows = [[
        {"text": "➕ Create EVM", "callback_data": "evmwallet:create"},
        {"text": "🔐 Import Private Key", "callback_data": "evmwallet:import"},
    ]]
    for r in _evm_store(app).list_wallets(tid):
        wid = str(r.get("wallet_id") or "")
        label = str(r.get("label") or wid)
        active = str(r.get("active") or "").lower() == "true"
        buttons = []
        if not active:
            buttons.append({"text": f"✅ Use {label}", "callback_data": f"evmwallet:use:{wid}"})
        buttons.append({"text": f"🗑 {label}", "callback_data": f"evmwallet:remove:{wid}"})
        rows.append(buttons)
    rows += [
        [{"text": "🔄 Refresh", "callback_data": "evmwallet:open"}],
        [{"text": "⬅️ My Wallets", "callback_data": "menu:wallet"}],
    ]
    return {"inline_keyboard": rows}


def _show_hub(app, tid):
    _ui._send(app, tid, wallet_hub_page(app, tid), wallet_hub_keyboard())


def _show_evm(app, tid):
    _ui._send(app, tid, evmwallet_page(app, tid), evmwallet_keyboard(app, tid))


def _disable_evm_live(app, tid):
    # Removing a signing wallet invalidates the assumptions behind LIVE/AUTO.
    _ui.set_user_setting(app.csv_dir, tid, "auto_trading_enabled", "false", description="User automatic route execution switch")
    _ui.set_user_setting(app.csv_dir, tid, "live_trading_enabled", "false", description="User live signing switch")


def _handle_pending_import(app, m):
    tid = (m.get("chat") or {}).get("id")
    if tid is None or str(tid) not in _PENDING_EVM_IMPORT:
        return False
    text = str(m.get("text") or "").strip()
    if text.startswith("/"):
        return False
    if text.lower() == "cancel":
        _PENDING_EVM_IMPORT.discard(str(tid))
        _show_evm(app, tid)
        return True
    try:
        if (m.get("chat") or {}).get("type") != "private":
            raise MultiWalletError("EVM private-key import is allowed only in a private Telegram chat")
        mid = m.get("message_id")
        if not mid or not _tg.delete_message(app.telegram_bot_token, tid, mid):
            raise MultiWalletError("Telegram did not confirm deletion; private key was NOT saved")
        r = _evm_store(app).save_private_key(tid, text, label="Imported EVM", source="telegram-button-import")
        _PENDING_EVM_IMPORT.discard(str(tid))
        try:
            _ui.audit(app.csv_dir, tid, "WALLET_IMPORT", r["wallet_id"], "", r["address"], "incoming secret deleted before persistence")
        except Exception:
            pass
        _ui._send(
            app,
            tid,
            f"✅ EVM wallet imported. Secret message deleted before encrypted persistence.\nID: <code>{html.escape(r['wallet_id'])}</code>\nAddress: <code>{html.escape(r['address'])}</code>",
            evmwallet_keyboard(app, tid),
        )
    except Exception as exc:
        _ui._send(app, tid, f"❌ {html.escape(str(exc))}\nSend a valid EVM private key or <code>cancel</code>.")
    return True


def _handle_callback(app, cb):
    tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
    data = str(cb.get("data") or "")
    if tid is None or not (data == "menu:wallet" or data.startswith("evmwallet:")):
        return False
    cqid = cb.get("id")
    if not _ui._auth(app, tid):
        if cqid:
            _tg.answer_callback_query(app.telegram_bot_token, cqid, "Not authorised.")
        return True
    try:
        if cqid:
            _tg.answer_callback_query(app.telegram_bot_token, cqid)
        if data == "menu:wallet":
            _show_hub(app, tid)
        elif data == "evmwallet:open":
            _show_evm(app, tid)
        elif data == "evmwallet:create":
            _ui.require_user(app.csv_dir, tid, active=False)
            r = _evm_store(app).create(tid, "EVM Wallet")
            try:
                _ui.audit(app.csv_dir, tid, "WALLET_CREATE", r["wallet_id"], "", r["address"])
            except Exception:
                pass
            _ui._send(app, tid, f"✅ New EVM wallet created.\nID: <code>{html.escape(r['wallet_id'])}</code>\nAddress: <code>{html.escape(r['address'])}</code>\nActive: <b>{str(r['active']).lower()}</b>", evmwallet_keyboard(app, tid))
        elif data == "evmwallet:import":
            _ui.require_user(app.csv_dir, tid, active=False)
            _PENDING_EVM_IMPORT.add(str(tid))
            _ui._send(app, tid, "🔐 <b>Import EVM Private Key</b>\nSend the EVM private key in this <b>private chat only</b>. Telegram must delete the incoming key message before the bot will save it encrypted.\n\nDo not send a seed phrase. Send <code>cancel</code> to stop.")
        elif data.startswith("evmwallet:use:"):
            wid = data.rsplit(":", 1)[-1]
            _evm_store(app).set_active(tid, wid)
            _show_evm(app, tid)
        elif data.startswith("evmwallet:remove-confirm:"):
            wid = data.rsplit(":", 1)[-1]
            _disable_evm_live(app, tid)
            _evm_store(app).forget(tid, wid)
            _ui._send(app, tid, "✅ EVM wallet removed. LIVE and AUTOTRADE were turned off as a safety precaution.", evmwallet_keyboard(app, tid))
        elif data.startswith("evmwallet:remove:"):
            wid = data.rsplit(":", 1)[-1]
            kb = {"inline_keyboard": [
                [{"text": "🗑 Confirm remove", "callback_data": f"evmwallet:remove-confirm:{wid}"}],
                [{"text": "Cancel", "callback_data": "evmwallet:open"}],
            ]}
            _ui._send(app, tid, f"Remove EVM wallet <code>{html.escape(wid)}</code>?\nRemoving an EVM signing wallet will turn your LIVE and AUTOTRADE switches off.", kb)
    except Exception as exc:
        _ui._send(app, tid, f"❌ {html.escape(str(exc))}", evmwallet_keyboard(app, tid))
    return True


def _handle_message(app, m):
    if _handle_pending_import(app, m):
        return True
    tid = (m.get("chat") or {}).get("id")
    if tid is None:
        return False
    text = str(m.get("text") or "").strip()
    if not text.startswith("/"):
        return False
    cmd = text.split()[0].split("@", 1)[0].lower()
    if cmd not in {"/wallets", "/evmwallet"}:
        return False
    if not _ui._auth(app, tid):
        return True
    if cmd == "/wallets":
        _show_hub(app, tid)
    else:
        _show_evm(app, tid)
    return True


def handle_update(app, update):
    if _handle_callback(app, update.get("callback_query") or {}):
        return
    if _handle_message(app, update.get("message") or {}):
        return
    return _PREV_HANDLE_UPDATE(app, update)


def set_commands(token: str):
    _PREV_SET_COMMANDS(token)
    try:
        commands = _tg._json("getMyCommands", token, payload={}, timeout=15) or []
        existing = {str(x.get("command") or "") for x in commands}
        extras = [
            {"command": "wallets", "description": "Manage all EVM and Solana wallets"},
            {"command": "evmwallet", "description": "Manage multiple EVM wallets"},
        ]
        commands.extend(x for x in extras if x["command"] not in existing)
        _tg._json("setMyCommands", token, payload={"commands": commands[:100]}, timeout=15)
    except Exception:
        pass


def install():
    if getattr(_ui, "_multi_wallet_manager_patch_installed", False):
        return
    _ui.wallet_page = wallet_hub_page
    _ui.handle_update = handle_update
    _ui.set_commands = set_commands
    _ui._multi_wallet_manager_patch_installed = True


install()
