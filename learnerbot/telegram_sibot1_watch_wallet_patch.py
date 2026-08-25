from __future__ import annotations

import csv
import html
import json
import os
import re
import time
from pathlib import Path

from . import telegram_sibot1_only_menu_patch as _sibot1
from . import telegram_ui as _ui
from .solana_wallet_store import SolanaWalletStore, validate_solana_address
from .user_registry import is_master

_PREV_HANDLE_UPDATE = _ui.handle_update
_PREV_SIBOT1_KEYBOARD = _sibot1.sibot1_keyboard
_PENDING: dict[str, str] = {}
_HEADERS = ["chain", "address", "label", "source", "enabled", "updated_epoch"]


def _path(app) -> Path:
    return Path(app.csv_dir) / "sibot1" / "watch_wallets.csv"


def _rows(app) -> list[dict[str, str]]:
    path = _path(app)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except Exception:
        return []


def _write(app, rows: list[dict[str, str]]) -> None:
    path = _path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_HEADERS)
        writer.writeheader()
        writer.writerows([{h: row.get(h, "") for h in _HEADERS} for row in rows])
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _validate(chain: str, address: str) -> str:
    chain = str(chain or "").strip().lower()
    value = str(address or "").strip()
    if chain == "base":
        if not re.fullmatch(r"0x[a-fA-F0-9]{40}", value):
            raise ValueError("Base/EVM address must be 0x followed by 40 hexadecimal characters")
        return value
    if chain == "solana":
        return validate_solana_address(value)
    raise ValueError("Unsupported SiBot 1 watch-wallet chain")


def _set_wallet(app, chain: str, address: str, *, source: str, label: str) -> str:
    chain = str(chain).strip().lower()
    address = _validate(chain, address)
    rows = [row for row in _rows(app) if str(row.get("chain") or "").lower() != chain]
    rows.append({
        "chain": chain,
        "address": address,
        "label": str(label or chain)[:60],
        "source": str(source or "telegram")[:60],
        "enabled": "true",
        "updated_epoch": str(int(time.time())),
    })
    _write(app, rows)
    return address


def _clear_wallet(app, chain: str) -> None:
    chain = str(chain).strip().lower()
    rows = [row for row in _rows(app) if str(row.get("chain") or "").lower() != chain]
    _write(app, rows)


def _current_evm_public_address(app) -> str:
    path = Path(app.data_dir) / "live_wallet.enc.json"
    if not path.exists():
        raise ValueError("No existing EVM live-wallet metadata was found on this server")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _validate("base", str(payload.get("address") or ""))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Existing EVM wallet metadata is unreadable") from exc


def _current_solana_public_address(app, tid) -> str:
    # data_dir is deliberately omitted: this reads only public CSV metadata and
    # cannot decrypt or access a Solana signing key.
    try:
        meta = SolanaWalletStore(Path(app.csv_dir)).get_meta(tid)
    except Exception as exc:
        raise ValueError("No active Solana public wallet metadata was found for this Telegram account") from exc
    return _validate("solana", str(meta.get("address") or ""))


def _mask(address: str) -> str:
    value = str(address or "")
    if len(value) <= 16:
        return value
    return value[:8] + "…" + value[-8:]


def wallet_page(app) -> str:
    by_chain = {
        str(row.get("chain") or "").lower(): row
        for row in _rows(app)
        if str(row.get("enabled") or "true").lower() in {"1", "true", "yes", "on"}
    }
    lines = [
        "<b>👛 SiBot 1 — Watch Wallets</b>",
        _sibot1.DIV,
        "",
        "<b>Mode:</b> READ-ONLY / WATCH-ONLY",
        "Signing: <b>OFF</b> • Broadcast: <b>OFF</b> • Private-key access: <b>OFF</b>",
        "",
    ]
    for chain, label in (("base", "Base / EVM"), ("solana", "Solana")):
        row = by_chain.get(chain)
        if row:
            address = str(row.get("address") or "")
            lines += [
                f"🟢 <b>{label}</b>",
                f"<code>{html.escape(address)}</code>",
                f"Source: {html.escape(str(row.get('source') or 'watch-only'))}",
                "",
            ]
        else:
            lines += [f"⚪ <b>{label}</b> — not connected", ""]
    lines += [
        "SiBot 1 stores only the public address in <code>CSVbot/sibot1/watch_wallets.csv</code>.",
        "It does not copy, decrypt or import any private key.",
    ]
    return "\n".join(lines)


def wallet_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🔗 Use current EVM wallet", "callback_data": "sibot1:wallet:use:base"}],
            [{"text": "🔗 Use current Solana wallet", "callback_data": "sibot1:wallet:use:solana"}],
            [
                {"text": "✍️ Add Base address", "callback_data": "sibot1:wallet:add:base"},
                {"text": "✍️ Add Solana address", "callback_data": "sibot1:wallet:add:solana"},
            ],
            [
                {"text": "🗑 Clear Base", "callback_data": "sibot1:wallet:clear:base"},
                {"text": "🗑 Clear Solana", "callback_data": "sibot1:wallet:clear:solana"},
            ],
            [{"text": "⬅️ SiBot 1", "callback_data": "sibot1:status"}],
        ]
    }


def sibot1_keyboard() -> dict:
    kb = _PREV_SIBOT1_KEYBOARD()
    rows = list(kb.get("inline_keyboard") or [])
    if not any(any(str(b.get("callback_data") or "") == "sibot1:wallets" for b in row) for row in rows):
        insert_at = max(0, len(rows) - 2)
        rows.insert(insert_at, [{"text": "👛 Wallets (watch-only)", "callback_data": "sibot1:wallets"}])
    return {"inline_keyboard": rows}


def _answer(app, cb, text="") -> None:
    cqid = (cb or {}).get("id")
    if not cqid:
        return
    try:
        _ui.answer_callback_query(app.telegram_bot_token, cqid, text)
    except Exception:
        pass


def _render(app, tid, text, kb, cb=None) -> None:
    _sibot1._render(app, tid, text, kb, cb)


def _require_master(app, tid) -> None:
    if not is_master(app.csv_dir, tid):
        raise ValueError("MASTER account required to change SiBot 1 wallet bindings")


def _prompt(chain: str) -> str:
    label = "Base/EVM" if chain == "base" else "Solana"
    return "\n".join([
        f"<b>✍️ Add {label} watch wallet</b>",
        _sibot1.DIV,
        "",
        "Send the PUBLIC wallet address only.",
        "Do not send a private key, seed phrase or recovery phrase.",
        "",
        "Send <code>/cancel</code> to cancel.",
    ])


def handle_update(app, update):
    cb = update.get("callback_query")
    if cb:
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = str(cb.get("data") or "")
        if data == "sibot1:wallets" or data.startswith("sibot1:wallet:"):
            if not _ui._auth(app, tid):
                _answer(app, cb, "Not authorised")
                return
            _answer(app, cb)
            try:
                if data == "sibot1:wallets":
                    _render(app, tid, wallet_page(app), wallet_keyboard(), cb)
                    return
                _require_master(app, tid)
                parts = data.split(":")
                action = parts[2] if len(parts) > 2 else ""
                chain = parts[3] if len(parts) > 3 else ""
                if action == "use" and chain == "base":
                    address = _current_evm_public_address(app)
                    _set_wallet(app, "base", address, source="existing-evm-public-metadata", label="Current EVM wallet")
                    _render(app, tid, "✅ <b>Base watch wallet connected</b>\n<code>" + html.escape(address) + "</code>\n\nNo private key was read or copied.", wallet_keyboard(), cb)
                    return
                if action == "use" and chain == "solana":
                    address = _current_solana_public_address(app, tid)
                    _set_wallet(app, "solana", address, source="existing-solana-public-metadata", label="Current Solana wallet")
                    _render(app, tid, "✅ <b>Solana watch wallet connected</b>\n<code>" + html.escape(address) + "</code>\n\nNo private key was read or copied.", wallet_keyboard(), cb)
                    return
                if action == "add" and chain in {"base", "solana"}:
                    _PENDING[str(tid)] = chain
                    _render(app, tid, _prompt(chain), {"inline_keyboard": [[{"text": "Cancel", "callback_data": "sibot1:wallets"}]]}, cb)
                    return
                if action == "clear" and chain in {"base", "solana"}:
                    _clear_wallet(app, chain)
                    _render(app, tid, wallet_page(app), wallet_keyboard(), cb)
                    return
            except Exception as exc:
                _render(app, tid, "❌ <b>SiBot 1 wallet</b>\n<code>" + html.escape(str(exc)[:320]) + "</code>", wallet_keyboard(), cb)
            return

    message = update.get("message") or {}
    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    pending = _PENDING.get(str(tid)) if tid is not None else None
    if pending and _ui._auth(app, tid):
        try:
            _require_master(app, tid)
            if text.lower() in {"/cancel", "cancel"}:
                _PENDING.pop(str(tid), None)
                _ui._send(app, tid, wallet_page(app), wallet_keyboard())
                return
            if any(marker in text.lower() for marker in ("private", "seed phrase", "recovery phrase")) or " " in text.strip():
                raise ValueError("Send one public wallet address only; private keys and recovery phrases are not accepted")
            address = _set_wallet(app, pending, text, source="telegram-public-address", label=f"SiBot 1 {pending} watch wallet")
            _PENDING.pop(str(tid), None)
            _ui._send(app, tid, "✅ <b>Watch wallet connected</b>\n<code>" + html.escape(address) + "</code>\n\nSigning remains OFF.", wallet_keyboard())
            return
        except Exception as exc:
            _ui._send(app, tid, "❌ " + html.escape(str(exc)) + "\nSend another public address or <code>/cancel</code>.")
            return
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_telegram_sibot1_watch_wallet_patch_installed", False):
        return
    _sibot1.sibot1_keyboard = sibot1_keyboard
    _ui.handle_update = handle_update
    _ui._telegram_sibot1_watch_wallet_patch_installed = True
    print("[telegram-sibot1-wallet] installed mode=watch-only private-key-access=off")


install()
