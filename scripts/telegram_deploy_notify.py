#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from learnerbot.challenge_alerts import challenge_chat_ids
from learnerbot.config import AppSettings
from learnerbot.telegram import send_to_chats


def main() -> int:
    app=AppSettings.load()
    status=(sys.argv[1] if len(sys.argv)>1 else "UPDATE").strip().upper()
    detail=" ".join(sys.argv[2:]).strip()
    icons={"STARTED":"🛠","DEPLOYED":"✅","ROLLBACK":"↩️","FAILED":"❌"}
    icon=icons.get(status,"ℹ️")
    msg=f"{icon} BOOT CODE UPDATE — {status}"
    if detail:msg+=f"\n{detail[:1200]}"
    recipients=challenge_chat_ids(app)
    if not recipients or not app.telegram_bot_token:
        print("telegram deploy notify: no configured recipients/token")
        return 1
    result=send_to_chats(app.telegram_bot_token,recipients,msg)
    print(f"telegram deploy notify: recipients={len(recipients)} sent={result.get('sent_chats',0)} failed={result.get('failed_chats',0)}")
    return 0 if int(result.get('failed_chats') or 0)==0 else 2

if __name__=="__main__":raise SystemExit(main())
