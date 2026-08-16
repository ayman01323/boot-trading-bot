from __future__ import annotations
from typing import Optional

import requests

API_BASE = "https://api.telegram.org"

def _api(token: str, method: str) -> str:
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not configured")
    return f"{API_BASE}/bot{token}/{method}"

def _json(method: str, token: str, *, payload=None, params=None, timeout=20):
    if method in {"getMe", "getUpdates", "getWebhookInfo"}:
        r = requests.get(_api(token, method), params=params, timeout=timeout)
    else:
        r = requests.post(_api(token, method), json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    return data.get("result")

def get_me(token: str) -> dict:
    return _json("getMe", token, timeout=15)

def get_webhook_info(token: str) -> dict:
    return _json("getWebhookInfo", token, timeout=15)

def get_updates(token: str, limit: int = 50, offset: Optional[int] = None, timeout: int = 0) -> list[dict]:
    params = {"limit": limit, "timeout": timeout, "allowed_updates": '["message","callback_query"]'}
    if offset is not None:
        params["offset"] = offset
    return _json("getUpdates", token, params=params, timeout=max(20, timeout + 5)) or []

def recent_chats(token: str, limit: int = 50) -> list[dict]:
    chats = {}
    for u in get_updates(token, limit):
        m = u.get("message") or u.get("channel_post") or u.get("edited_message") or u.get("edited_channel_post")
        if not m:
            continue
        c = m.get("chat") or {}
        cid = c.get("id")
        if cid is None:
            continue
        chats[str(cid)] = {
            "id": cid,
            "type": c.get("type", ""),
            "title": c.get("title") or c.get("first_name") or c.get("username", ""),
        }
    return list(chats.values())

def _split_text(text: str, limit: int = 3900):
    text = (text or "").strip()
    out = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        out.append(text)
    return out

def send_message(
    token: str,
    chat_id: str,
    text: str,
    *,
    protect_content: bool = False,
    disable_notification: bool = False,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[dict] = None,
) -> int:
    if not chat_id:
        raise ValueError("Telegram chat ID is not configured")
    chunks = _split_text(text)
    n = 0
    for idx, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "protect_content": protect_content,
            "disable_notification": disable_notification,
            "link_preview_options": {"is_disabled": True},
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None and idx == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        _json("sendMessage", token, payload=payload, timeout=20)
        n += 1
    return n


def delete_message(token: str, chat_id, message_id: int) -> bool:
    return bool(_json("deleteMessage", token, payload={"chat_id": chat_id, "message_id": int(message_id)}, timeout=15))

def answer_callback_query(token: str, callback_query_id: str, text: str = "") -> None:
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text[:180]
    _json("answerCallbackQuery", token, payload=payload, timeout=15)

def set_commands(token: str) -> None:
    commands = [
        {"command": "menu", "description": "Open main menu"},
        {"command": "join", "description": "Register Telegram ID under default fee plan"},
        {"command": "activate", "description": "Activate account with code"},
        {"command": "fees", "description": "Show my fee plan/status"},
        {"command": "payactivation", "description": "Pay fixed activation fee: bsc CONFIRM"},
        {"command": "control", "description": "MASTER platform controls"},
        {"command": "platformlive", "description": "MASTER global live-signing gate"},
        {"command": "platformauto", "description": "MASTER global automatic-execution gate"},
        {"command": "adminusers", "description": "MASTER list platform users"},
        {"command": "admincode", "description": "MASTER create activation code"},
        {"command": "engine", "description": "Pause/resume engine: /engine on|off"},
        {"command": "mode", "description": "Set mode: /mode shadow|armed"},
        {"command": "queue", "description": "Show local execution queue"},
        {"command": "wallet", "description": "My isolated multi-wallet status"},
        {"command": "walletcreate", "description": "Create another server wallet: [LABEL] CONFIRM"},
        {"command": "walletimport", "description": "Import my private key; message deleted"},
        {"command": "walletuse", "description": "Select my active wallet by wallet id"},
        {"command": "walletremove", "description": "Remove one of my wallets: ID CONFIRM"},
        {"command": "assets", "description": "Show assets for my active wallet"},
        {"command": "transfer", "description": "Transfer native/token from my active wallet"},
        {"command": "auto", "description": "Automatic route-trading status"},
        {"command": "autotrade", "description": "Automatic trading on/off"},
        {"command": "autoprep", "description": "Prepare wrapped capital + bounded allowance"},
        {"command": "opportunities", "description": "Fresh learned + direct-market routes"},
        {"command": "power", "description": "Full-power V2/V3 scanner status"},
        {"command": "products", "description": "Dynamic AUTO product universe and risk levels"},
        {"command": "setautosize", "description": "Set automatic route input"},
        {"command": "setautoprofit", "description": "Set minimum automatic net profit"},
        {"command": "trading", "description": "Live trading wallet/status"},
        {"command": "live", "description": "Live switch: /live on CONFIRM | /live off"},
        {"command": "balance", "description": "Wallet balance: /balance bsc [token]"},
        {"command": "quote", "description": "Quote BUY: /quote bsc TOKEN 0.01"},
        {"command": "buy", "description": "Live BUY: /buy bsc TOKEN 0.01 CONFIRM"},
        {"command": "sell", "description": "Live SELL: /sell bsc TOKEN 50% CONFIRM"},
        {"command": "settrademax", "description": "Set maximum native BUY amount"},
        {"command": "setslippage", "description": "Set live slippage in basis points"},
        {"command": "tx", "description": "Transaction status: /tx bsc TXHASH"},
        {"command": "setmax", "description": "Set maximum wrapped-base input"},
        {"command": "setprofit", "description": "Set minimum conservative profit"},
        {"command": "setcopy", "description": "Set copy sizing percentage"},
        {"command": "setedge", "description": "Set follower edge-capture percentage"},
        {"command": "setage", "description": "Set maximum live-signal age"},
        {"command": "setcanary", "description": "Set canary input size"},
        {"command": "setscore", "description": "Set minimum copy score"},
        {"command": "alerts", "description": "Control automatic Telegram updates"},
        {"command": "chains", "description": "Enabled blockchain networks"},
        {"command": "wallets", "description": "Top detected bot wallets"},
        {"command": "profit", "description": "Proven profit evidence"},
        {"command": "strategies", "description": "Learned strategies"},
        {"command": "behaviours", "description": "Trade behaviour research"},
        {"command": "rankings", "description": "Highest and fastest profit"},
        {"command": "copy20", "description": "Top 20 approved copy wallets"},
        {"command": "signals", "description": "Advisory IN / OUT copy signals"},
        {"command": "help", "description": "Explain strategy fields"},
        {"command": "status", "description": "Scanner status"},
        {"command": "report", "description": "Full intelligence report"},
    ]
    _json("setMyCommands", token, payload={"commands": commands}, timeout=15)

def send_to_chats(
    token: str,
    chat_ids: list[str],
    text: str,
    *,
    protect_content: bool = False,
    disable_notification: bool = False,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[dict] = None,
) -> dict:
    clean = []
    seen = set()
    for chat_id in chat_ids:
        cid = str(chat_id).strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        clean.append(cid)
        if len(clean) >= 5:
            break

    if not clean:
        raise ValueError("No Telegram chat IDs configured")

    results = {"sent_chats": 0, "failed_chats": 0, "messages": 0, "details": []}
    for cid in clean:
        try:
            n = send_message(
                token,
                cid,
                text,
                protect_content=protect_content,
                disable_notification=disable_notification,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            results["sent_chats"] += 1
            results["messages"] += n
            results["details"].append({"chat_id": cid, "ok": True, "messages": n})
        except Exception as exc:
            results["failed_chats"] += 1
            results["details"].append({
                "chat_id": cid,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return results
