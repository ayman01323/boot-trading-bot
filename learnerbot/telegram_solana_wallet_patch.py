from __future__ import annotations

import html
from decimal import Decimal

import requests

from . import solana_sibot as _sol
from . import telegram as _tg
from . import telegram_ui as _ui
from .solana_wallet_store import SolanaWalletError, SolanaWalletStore

_PREV_HANDLE_UPDATE = _ui.handle_update
_PREV_WALLET_PAGE = _ui.wallet_page
_PREV_SET_COMMANDS = _ui.set_commands
_PENDING_ADD: set[str] = set()


def _store(app):
    return SolanaWalletStore(app.csv_dir)


def _short(a):
    a = str(a or "")
    return a if len(a) <= 18 else f"{a[:8]}…{a[-6:]}"


def _balance(app, address):
    try:
        cfg = _sol.settings(app)
        r = requests.post(
            cfg.get("rpc_url") or _sol.DEFAULT_RPC,
            json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [address, {"commitment": "confirmed"}]},
            timeout=7,
        )
        r.raise_for_status()
        data = r.json()
        lamports = int(((data.get("result") or {}).get("value")) or 0)
        return Decimal(lamports) / Decimal(1_000_000_000)
    except Exception:
        return None


def wallet_page(app, chat_id):
    text = str(_PREV_WALLET_PAGE(app, chat_id))
    store = _store(app)
    rows = store.list_wallets(chat_id)
    text += "\n\n<b>🟣 SOLANA WALLET</b>"
    if not rows:
        text += "\nNo Solana wallet added yet.\nTap <b>🟣 Solana Wallets</b> below to add a public Solana address."
    else:
        active = store.get_meta(chat_id)
        text += f"\nActive: <b>{html.escape(active.get('label') or active.get('wallet_id') or '')}</b> <code>{html.escape(_short(active.get('address')))}</code>"
        text += f"\nSaved Solana wallets: <b>{len(rows)}</b>"
    return text


def wallet_keyboard():
    return {"inline_keyboard": [
        [{"text": "🟣 Solana Wallets", "callback_data": "solwallet:open"}],
        [{"text": "⬅️ Menu", "callback_data": "menu:home"}],
    ]}


def solwallet_page(app, tid):
    _ui.require_user(app.csv_dir, tid, active=False)
    store = _store(app)
    rows = store.list_wallets(tid)
    L = ["<b>🟣 SOLANA WALLETS</b>", "━━━━━━━━━━━━", "Solana uses a separate public address from your EVM <code>0x...</code> wallet.", ""]
    if not rows:
        L += ["No Solana wallet added yet.", "", "Tap <b>➕ Add Solana Wallet</b> and send the public address only."]
    else:
        for r in rows:
            active = str(r.get("active") or "").lower() == "true"
            mark = "✅ ACTIVE" if active else "▫️"
            L.append(f"{mark} <b>{html.escape(r.get('label') or r.get('wallet_id') or '')}</b> — <code>{html.escape(r.get('wallet_id') or '')}</code>")
            L.append(f"<code>{html.escape(r.get('address') or '')}</code>")
            if active:
                bal = _balance(app, r.get("address"))
                if bal is not None:
                    L.append(f"Balance: <b>{bal:f} SOL</b>")
            L.append("")
    L += ["<b>Commands</b>", "<code>/solwallet</code>", "<code>/solwalletadd ADDRESS</code>", "<code>/solwalletadd LABEL ADDRESS</code>", "<code>/solwalletuse s1234abcd</code>", "<code>/solwalletremove s1234abcd CONFIRM</code>", "", "🔐 <b>Security:</b> this screen accepts a public Solana address only. Do not send a seed phrase or Solana private key. Solana SiBot remains SHADOW-only."]
    return "\n".join(L)


def solwallet_keyboard(app, tid):
    rows = [[{"text": "➕ Add Solana Wallet", "callback_data": "solwallet:add"}]]
    for r in _store(app).list_wallets(tid):
        wid = r.get("wallet_id") or ""
        label = r.get("label") or wid
        active = str(r.get("active") or "").lower() == "true"
        row = []
        if not active:
            row.append({"text": f"✅ Use {label}", "callback_data": f"solwallet:use:{wid}"})
        row.append({"text": f"🗑 {label}", "callback_data": f"solwallet:remove:{wid}"})
        rows.append(row)
        address = r.get("address") or ""
        if address:
            rows.append([{"text": f"🔎 {_short(address)}", "url": f"{(_sol.settings(app).get('explorer_url') or _sol.DEFAULT_EXPLORER).rstrip('/')}/account/{address}"}])
    rows += [[{"text": "🔄 Refresh", "callback_data": "solwallet:open"}], [{"text": "⬅️ My Wallets", "callback_data": "menu:wallet"}]]
    return {"inline_keyboard": rows}


def _show(app, tid):
    _ui._send(app, tid, solwallet_page(app, tid), solwallet_keyboard(app, tid))


def _add(app, tid, address, label="Solana"):
    r = _store(app).add(tid, address, label=label)
    try:
        _ui.audit(app.csv_dir, tid, "SOLANA_WALLET_ADD", r["wallet_id"], "", r["address"], "public address only")
    except Exception:
        pass
    return r


def _handle_message(app, m):
    tid = (m.get("chat") or {}).get("id")
    if tid is None:
        return False
    text = str(m.get("text") or "").strip()
    key = str(tid)
    if key in _PENDING_ADD and not text.startswith("/"):
        if text.lower() in {"cancel", "/cancel"}:
            _PENDING_ADD.discard(key)
            _show(app, tid)
            return True
        try:
            r = _add(app, tid, text)
            _PENDING_ADD.discard(key)
            _ui._send(app, tid, f"✅ Solana wallet added.\nID: <code>{html.escape(r['wallet_id'])}</code>\nAddress: <code>{html.escape(r['address'])}</code>\nActive: <b>{str(r['active']).lower()}</b>", solwallet_keyboard(app, tid))
        except Exception as exc:
            _ui._send(app, tid, f"❌ {html.escape(str(exc))}\nSend a valid Solana public address or <code>cancel</code>.")
        return True
    if not text.startswith("/"):
        return False
    parts = text.split()
    cmd = parts[0].split("@", 1)[0].lower()
    if cmd not in {"/wallet", "/solwallet", "/solwalletadd", "/solwalletuse", "/solwalletremove", "/solwalletforget"}:
        return False
    if not _ui._auth(app, tid):
        return True
    try:
        if cmd == "/wallet":
            _ui._send(app, tid, wallet_page(app, tid), wallet_keyboard())
        elif cmd == "/solwallet":
            _show(app, tid)
        elif cmd == "/solwalletadd":
            _ui.require_user(app.csv_dir, tid, active=False)
            if len(parts) == 2:
                label, address = "Solana", parts[1]
            elif len(parts) == 3:
                label, address = parts[1], parts[2]
            else:
                raise SolanaWalletError("Use /solwalletadd ADDRESS or /solwalletadd LABEL ADDRESS")
            r = _add(app, tid, address, label)
            _ui._send(app, tid, f"✅ Solana wallet added and {'selected' if str(r['active']).lower() == 'true' else 'saved'}.\nID: <code>{html.escape(r['wallet_id'])}</code>\nAddress: <code>{html.escape(r['address'])}</code>", solwallet_keyboard(app, tid))
        elif cmd == "/solwalletuse":
            if len(parts) != 2:
                raise SolanaWalletError("Use /solwalletuse WALLET_ID")
            r = _store(app).set_active(tid, parts[1])
            _ui._send(app, tid, f"✅ Active Solana wallet: <b>{html.escape(r.get('label') or '')}</b>\n<code>{html.escape(r.get('address') or '')}</code>", solwallet_keyboard(app, tid))
        else:
            if len(parts) != 3 or parts[2].upper() != "CONFIRM":
                raise SolanaWalletError("Use /solwalletremove WALLET_ID CONFIRM")
            _store(app).forget(tid, parts[1])
            _ui._send(app, tid, "✅ Solana public wallet removed.", solwallet_keyboard(app, tid))
    except Exception as exc:
        _ui._send(app, tid, f"❌ {html.escape(str(exc))}", solwallet_keyboard(app, tid))
    return True


def _handle_callback(app, cb):
    tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
    data = str(cb.get("data") or "")
    if tid is None or not (data.startswith("solwallet:") or data == "menu:wallet"):
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
            _ui._send(app, tid, wallet_page(app, tid), wallet_keyboard())
        elif data == "solwallet:open":
            _show(app, tid)
        elif data == "solwallet:add":
            _PENDING_ADD.add(str(tid))
            _ui._send(app, tid, "🟣 <b>Add Solana Wallet</b>\nSend the <b>public Solana address only</b>.\n\nDo not send a seed phrase or private key. Send <code>cancel</code> to stop.")
        elif data.startswith("solwallet:use:"):
            wid = data.rsplit(":", 1)[-1]
            _store(app).set_active(tid, wid)
            _show(app, tid)
        elif data.startswith("solwallet:remove-confirm:"):
            wid = data.rsplit(":", 1)[-1]
            _store(app).forget(tid, wid)
            _show(app, tid)
        elif data.startswith("solwallet:remove:"):
            wid = data.rsplit(":", 1)[-1]
            kb = {"inline_keyboard": [[{"text": "🗑 Confirm remove", "callback_data": f"solwallet:remove-confirm:{wid}"}], [{"text": "Cancel", "callback_data": "solwallet:open"}]]}
            _ui._send(app, tid, f"Remove Solana wallet <code>{html.escape(wid)}</code>?\nOnly the saved public address will be removed.", kb)
    except Exception as exc:
        _ui._send(app, tid, f"❌ {html.escape(str(exc))}", solwallet_keyboard(app, tid))
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
            {"command": "solwallet", "description": "Manage my Solana public wallets"},
            {"command": "solwalletadd", "description": "Add a Solana public address"},
            {"command": "solwalletuse", "description": "Select active Solana wallet"},
        ]
        commands.extend(x for x in extras if x["command"] not in existing)
        _tg._json("setMyCommands", token, payload={"commands": commands[:100]}, timeout=15)
    except Exception:
        pass


def install():
    if getattr(_ui, "_solana_wallet_patch_installed", False):
        return
    _ui.wallet_page = wallet_page
    _ui.handle_update = handle_update
    _ui.set_commands = set_commands
    _ui._solana_wallet_patch_installed = True


install()
