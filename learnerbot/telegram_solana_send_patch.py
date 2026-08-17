from __future__ import annotations

import html
import secrets
import time
from decimal import Decimal, ROUND_HALF_UP

import requests

from . import solana_sibot as _sol
from . import telegram as _tg
from . import telegram_solana_wallet_patch as _solui
from . import telegram_ui as _ui
from .solana_live_patch import live_enabled
from .solana_manual_transfer import (
    SolanaManualTransferError,
    broadcast_native_transfer,
    prepare_native_transfer,
)
from .solana_wallet_store import SolanaWalletStore, validate_solana_address

_PREV_HANDLE = _ui.handle_update
_PREV_SET_COMMANDS = _ui.set_commands
_PREV_SOL_KEYBOARD = _solui.solwallet_keyboard
_PREV_SOL_PAGE = _solui.solwallet_page

_PENDING_INPUT: dict[str, dict] = {}
_PENDING_CONFIRM: dict[tuple[str, str], dict] = {}
_PRICE_CACHE = {"ts": 0.0, "usd": None}
INPUT_TTL_SECONDS = 300
CONFIRM_TTL_SECONDS = 120


def _store(app):
    return SolanaWalletStore(app.csv_dir, app.data_dir)


def _short(value):
    value = str(value or "")
    return value if len(value) <= 18 else f"{value[:8]}…{value[-6:]}"


def _bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _require_manual_transfer(app, tid, chat_type=None):
    user = _ui.require_user(app.csv_dir, tid, active=True)
    if chat_type is not None and str(chat_type) != "private":
        raise SolanaManualTransferError("Manual Solana transfers are allowed only in a private Telegram chat")
    if not _bool(user.get("can_transfer"), False):
        raise SolanaManualTransferError("This Telegram account is not permitted to transfer funds")
    if live_enabled(app, tid):
        raise SolanaManualTransferError(
            "Disable Solana LIVE before a manual transfer so AUTO trading cannot change the wallet balance during confirmation"
        )
    store = _store(app)
    meta = store.get_meta(tid)
    if not store.has_private_key(tid, meta.get("wallet_id")):
        raise SolanaManualTransferError("Active Solana wallet is not SIGNING READY")
    return user, meta


def _sol_usd_price() -> Decimal:
    now = time.time()
    if now - float(_PRICE_CACHE.get("ts") or 0) < 45 and _PRICE_CACHE.get("usd") is not None:
        return Decimal(str(_PRICE_CACHE["usd"]))
    r = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": "solana", "vs_currencies": "usd"},
        headers={"User-Agent": "BOOT-Solana-Manual-Transfer/1.0"},
        timeout=10,
    )
    r.raise_for_status()
    price = Decimal(str(((r.json().get("solana") or {}).get("usd")) or "0"))
    if price <= 0:
        raise SolanaManualTransferError("Could not obtain a current SOL/USD price")
    _PRICE_CACHE.update({"ts": now, "usd": price})
    return price


def _lamports_for(mode: str, amount: Decimal) -> tuple[int, Decimal | None]:
    amount = Decimal(str(amount))
    if amount <= 0:
        raise SolanaManualTransferError("Amount must be greater than zero")
    if mode == "SOL":
        sol_amount = amount
        price = None
    elif mode == "USD":
        price = _sol_usd_price()
        sol_amount = amount / price
    else:
        raise SolanaManualTransferError("Transfer mode must be SOL or USD")
    lamports = int((sol_amount * Decimal(1_000_000_000)).to_integral_value(rounding=ROUND_HALF_UP))
    if lamports <= 0:
        raise SolanaManualTransferError("Amount is too small to send")
    return lamports, price


def _audit(app, tid, action, ref="", amount="", destination="", note=""):
    try:
        _ui.audit(app.csv_dir, tid, action, ref, amount, destination, note)
    except Exception:
        pass


def solwallet_keyboard(app, tid):
    kb = _PREV_SOL_KEYBOARD(app, tid)
    rows = list(kb.get("inline_keyboard") or [])
    send_row = [
        {"text": "💸 Send SOL", "callback_data": "solsend:start:sol"},
        {"text": "💵 Send USD", "callback_data": "solsend:start:usd"},
    ]
    if not any(any(str(b.get("callback_data") or "").startswith("solsend:") for b in row) for row in rows):
        insert_at = 1 if rows else 0
        rows.insert(insert_at, send_row)
    return {"inline_keyboard": rows}


def solwallet_page(app, tid):
    text = _PREV_SOL_PAGE(app, tid)
    text = text.replace(
        "⚠️ Storing signing authority does <b>not</b> enable Solana LIVE trading. SiBot remains SHADOW-only until the Solana transaction simulation/sign/broadcast controls are separately enabled.",
        "⚠️ <b>Manual transfers:</b> use <b>Send SOL</b> or <b>Send USD</b> below. Every send requires a separate confirmation and is blocked while Solana LIVE AUTO is armed.",
    )
    return text


def _prompt(app, tid, mode):
    mode = str(mode).upper()
    _PENDING_INPUT[str(tid)] = {"mode": mode, "expires": time.time() + INPUT_TTL_SECONDS}
    unit = "SOL" if mode == "SOL" else "USD"
    example = "0.01 ADDRESS" if mode == "SOL" else "2 ADDRESS"
    _ui._send(
        app,
        tid,
        "\n".join([
            f"<b>{'💸' if mode == 'SOL' else '💵'} Send {unit} from Solana wallet</b>",
            "━━━━━━━━━━━━",
            f"Send the <b>amount</b> followed by the <b>destination Solana address</b> in your next message.",
            "",
            f"Example: <code>{example}</code>",
            "",
            "Nothing is sent yet. The bot will show the exact SOL amount, sender, destination, balance and reserve before a separate CONFIRM TRANSFER button appears.",
            "Send <code>cancel</code> to stop.",
        ]),
    )


def _make_preview(app, tid, mode, amount_text, destination, chat_type="private"):
    _, meta = _require_manual_transfer(app, tid, chat_type)
    destination = validate_solana_address(destination)
    try:
        requested = Decimal(str(amount_text))
    except Exception as exc:
        raise SolanaManualTransferError("Amount must be a number") from exc
    lamports, price = _lamports_for(mode, requested)
    prepared = prepare_native_transfer(app, tid, destination, lamports)
    token = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:10]
    entry = {
        "mode": mode,
        "requested": requested,
        "price": price,
        "destination": destination,
        "lamports": lamports,
        "sender": prepared["sender"],
        "amount_sol": prepared["amount_sol"],
        "balance_sol": prepared["balance_sol"],
        "reserve_sol": prepared["reserve_sol"],
        "wallet_id": str(meta.get("wallet_id") or ""),
        "expires": time.time() + CONFIRM_TTL_SECONDS,
    }
    _PENDING_CONFIRM[(str(tid), token)] = entry
    _audit(
        app,
        tid,
        "SOLANA_TRANSFER_PREVIEW",
        token,
        f"{entry['amount_sol']:.9f} SOL",
        destination,
        f"requested {requested} {mode}; no transaction broadcast",
    )
    requested_line = (
        f"Requested: <b>{requested:.9f} SOL</b>"
        if mode == "SOL"
        else f"Requested: <b>${requested:.2f}</b> at <b>${price:,.4f}/SOL</b>"
    )
    after = entry["balance_sol"] - entry["amount_sol"]
    text = "\n".join([
        "<b>⚠️ REVIEW SOLANA TRANSFER</b>",
        "━━━━━━━━━━━━",
        f"From: <code>{html.escape(entry['sender'])}</code>",
        f"To: <code>{html.escape(destination)}</code>",
        requested_line,
        f"Will send exactly: <b>{entry['amount_sol']:.9f} SOL</b>",
        f"Current SOL balance: <b>{entry['balance_sol']:.9f} SOL</b>",
        f"Balance after amount, before network fee: <b>{after:.9f} SOL</b>",
        f"Protected minimum reserve: <b>{entry['reserve_sol']:.9f} SOL</b>",
        "",
        "A Solana network fee is additional. Blockchain transfers are irreversible.",
        f"This confirmation expires in <b>{CONFIRM_TTL_SECONDS} seconds</b>.",
    ])
    kb = {"inline_keyboard": [
        [{"text": "✅ CONFIRM TRANSFER", "callback_data": f"solsend:confirm:{token}"}],
        [{"text": "Cancel", "callback_data": f"solsend:cancel:{token}"}],
    ]}
    _ui._send(app, tid, text, kb)
    return entry


def _handle_pending_input(app, message):
    tid = (message.get("chat") or {}).get("id")
    if tid is None:
        return False
    key = str(tid)
    pending = _PENDING_INPUT.get(key)
    if not pending:
        return False
    text = str(message.get("text") or "").strip()
    if text.startswith("/"):
        return False
    if time.time() > float(pending.get("expires") or 0):
        _PENDING_INPUT.pop(key, None)
        _ui._send(app, tid, "⌛ Send request expired. Open Solana Wallets and choose Send SOL / Send USD again.")
        return True
    if text.lower() in {"cancel", "/cancel"}:
        _PENDING_INPUT.pop(key, None)
        _ui._send(app, tid, "✅ Solana transfer cancelled.", solwallet_keyboard(app, tid))
        return True
    parts = text.split()
    if len(parts) != 2:
        _ui._send(app, tid, "❌ Send exactly: <code>AMOUNT ADDRESS</code>, for example <code>2 Css...</code>, or send <code>cancel</code>.")
        return True
    try:
        _make_preview(app, tid, pending["mode"], parts[0], parts[1], (message.get("chat") or {}).get("type"))
        _PENDING_INPUT.pop(key, None)
    except Exception as exc:
        _ui._send(app, tid, f"❌ {html.escape(str(exc))}\nNothing was sent. Send another <code>AMOUNT ADDRESS</code> or <code>cancel</code>.")
    return True


def _handle_command(app, message):
    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    if tid is None or not text.startswith("/"):
        return False
    parts = text.split()
    cmd = parts[0].split("@", 1)[0].lower()
    if cmd not in {"/solsend", "/solsendsol", "/solsendusd"}:
        return False
    if not _ui._auth(app, tid):
        return True
    try:
        chat_type = (message.get("chat") or {}).get("type")
        if cmd == "/solsend":
            if len(parts) != 4 or parts[1].upper() not in {"SOL", "USD"}:
                raise SolanaManualTransferError("Use /solsend SOL AMOUNT ADDRESS or /solsend USD AMOUNT ADDRESS")
            mode, amount, destination = parts[1].upper(), parts[2], parts[3]
        else:
            if len(parts) != 3:
                raise SolanaManualTransferError(f"Use {cmd} AMOUNT ADDRESS")
            mode = "SOL" if cmd == "/solsendsol" else "USD"
            amount, destination = parts[1], parts[2]
        _make_preview(app, tid, mode, amount, destination, chat_type)
    except Exception as exc:
        _ui._send(app, tid, f"❌ {html.escape(str(exc))}\nNothing was sent.", solwallet_keyboard(app, tid))
    return True


def _handle_callback(app, cb):
    tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
    data = str(cb.get("data") or "")
    if tid is None or not data.startswith("solsend:"):
        return False
    cqid = str(cb.get("id") or "")
    if not _ui._auth(app, tid):
        if cqid:
            _tg.answer_callback_query(app.telegram_bot_token, cqid, "Not authorised")
        return True
    try:
        chat_type = ((cb.get("message") or {}).get("chat") or {}).get("type")
        if data == "solsend:start:sol":
            _require_manual_transfer(app, tid, chat_type)
            if cqid:
                _tg.answer_callback_query(app.telegram_bot_token, cqid)
            _prompt(app, tid, "SOL")
            return True
        if data == "solsend:start:usd":
            _require_manual_transfer(app, tid, chat_type)
            if cqid:
                _tg.answer_callback_query(app.telegram_bot_token, cqid)
            _prompt(app, tid, "USD")
            return True
        if data.startswith("solsend:cancel:"):
            token = data.rsplit(":", 1)[-1]
            _PENDING_CONFIRM.pop((str(tid), token), None)
            if cqid:
                _tg.answer_callback_query(app.telegram_bot_token, cqid, "Transfer cancelled")
            _ui._send(app, tid, "✅ Solana transfer cancelled. Nothing was sent.", solwallet_keyboard(app, tid))
            return True
        if data.startswith("solsend:confirm:"):
            token = data.rsplit(":", 1)[-1]
            entry = _PENDING_CONFIRM.pop((str(tid), token), None)
            if not entry:
                raise SolanaManualTransferError("Transfer confirmation is missing or has already been used")
            if time.time() > float(entry.get("expires") or 0):
                raise SolanaManualTransferError("Transfer confirmation expired; create a new transfer preview")
            _require_manual_transfer(app, tid, chat_type)
            if cqid:
                _tg.answer_callback_query(app.telegram_bot_token, cqid, "Submitting confirmed Solana transfer…")
            result = broadcast_native_transfer(app, tid, entry["destination"], int(entry["lamports"]))
            signature = str(result.get("signature") or "")
            status = str(result.get("status") or "SUBMITTED")
            _audit(
                app,
                tid,
                "SOLANA_TRANSFER_SUBMIT",
                signature,
                f"{entry['amount_sol']:.9f} SOL",
                entry["destination"],
                status,
            )
            explorer = str(_sol.settings(app).get("explorer_url") or _sol.DEFAULT_EXPLORER).rstrip("/")
            _ui._send(
                app,
                tid,
                "\n".join([
                    "✅ <b>Solana transfer submitted</b>",
                    f"Amount: <b>{entry['amount_sol']:.9f} SOL</b>",
                    f"To: <code>{html.escape(entry['destination'])}</code>",
                    f"Status: <b>{html.escape(status)}</b>",
                    f"TX: <code>{html.escape(signature)}</code>",
                ]),
                {"inline_keyboard": [[{"text": "🔎 View transaction", "url": f"{explorer}/tx/{signature}"}], [{"text": "⬅️ Solana Wallets", "callback_data": "solwallet:open"}]]},
            )
            return True
    except Exception as exc:
        if cqid:
            try:
                _tg.answer_callback_query(app.telegram_bot_token, cqid, str(exc)[:160])
            except Exception:
                pass
        _ui._send(app, tid, f"❌ <b>Solana transfer not sent</b>\n<code>{html.escape(str(exc)[:600])}</code>", solwallet_keyboard(app, tid))
        return True
    return False


def handle_update(app, update):
    cb = update.get("callback_query") or {}
    if _handle_callback(app, cb):
        return
    message = update.get("message") or {}
    if _handle_pending_input(app, message):
        return
    if _handle_command(app, message):
        return
    return _PREV_HANDLE(app, update)


def set_commands(token: str):
    _PREV_SET_COMMANDS(token)
    try:
        commands = _tg._json("getMyCommands", token, payload={}, timeout=15) or []
        existing = {str(x.get("command") or "") for x in commands}
        extras = [
            {"command": "solsend", "description": "Review a SOL/USD Solana transfer"},
            {"command": "solsendsol", "description": "Review sending an exact SOL amount"},
            {"command": "solsendusd", "description": "Review sending a USD-equivalent SOL amount"},
        ]
        commands.extend(x for x in extras if x["command"] not in existing)
        _tg._json("setMyCommands", token, payload={"commands": commands[:100]}, timeout=15)
    except Exception:
        pass


def install():
    _sol.DEFAULTS.update({
        "manual_transfer_max_sol": ("1", "Maximum SOL in one user-confirmed manual wallet transfer"),
        "manual_transfer_require_simulation": ("true", "Require successful signed simulation before manual SOL transfer submission"),
    })
    _solui.solwallet_keyboard = solwallet_keyboard
    _solui.solwallet_page = solwallet_page
    _ui.handle_update = handle_update
    _ui.set_commands = set_commands


install()
