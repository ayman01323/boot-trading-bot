#!/usr/bin/env python3
"""Outbound-only Telegram relay for isolated learner Cold Zone decisions."""

import html
import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

DB = Path("/home/ayman01323/BOOT/testingbots/learn/data/solana_sibot.sqlite3")
STATE = Path("/home/ayman01323/.local/state/google-learner-cold-zone-relay.json")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip().strip('"').strip("'")
POLL_SECONDS = 2
DEXVIEW_SOLANA = "https://www.dexview.com/solana"


def load_state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"last_id": 0}
    except Exception:
        return {"last_id": 0}


def save_state(value: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    os.replace(tmp, STATE)


def _with_dexview(text: str, mint: str) -> str:
    """Append one clickable DexView link to every Cold Zone Telegram message."""
    text = str(text or "")
    if "dexview.com/" in text.lower():
        return text
    clean_mint = str(mint or "").strip()
    if clean_mint:
        url = f"{DEXVIEW_SOLANA}/{urllib.parse.quote(clean_mint, safe='')}"
        label = "Open token on DexView"
    else:
        url = DEXVIEW_SOLANA
        label = "Open DexView Solana"
    return text + f'\n\n🔎 <a href="{html.escape(url, quote=True)}">{label}</a>'


def send(tid: str, text: str) -> bool:
    if not TOKEN or not tid or not text:
        return False
    body = urllib.parse.urlencode(
        {
            "chat_id": str(tid),
            "text": str(text),
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data=body,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return 200 <= int(response.status) < 300
    except Exception:
        return False


def pending(after_id: int) -> list[dict]:
    if not DB.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cold_zone_notifications'"
        ).fetchone()
        if not exists:
            con.close()
            return []
        rows = [
            dict(row)
            for row in con.execute(
                """SELECT notification_id,telegram_id,kind,position_id,mint,message_html,created_at
                   FROM cold_zone_notifications WHERE notification_id>? ORDER BY notification_id LIMIT 100""",
                (int(after_id),),
            ).fetchall()
        ]
        con.close()
        return rows
    except Exception:
        return []


def main() -> None:
    state = load_state()
    last_id = int(state.get("last_id") or 0)
    while True:
        rows = pending(last_id)
        for row in rows:
            nid = int(row.get("notification_id") or 0)
            tid = str(row.get("telegram_id") or "")
            text = _with_dexview(
                str(row.get("message_html") or ""),
                str(row.get("mint") or ""),
            )
            if not send(tid, text):
                # Retry the same notification next loop; never skip an unsent result.
                break
            last_id = max(last_id, nid)
            state["last_id"] = last_id
            state["last_sent_at"] = int(time.time())
            save_state(state)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing")
    main()
