#!/usr/bin/env bash
set -Eeuo pipefail

test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323

MODULE_SHA='157369e2f7e7486e8be8f688b43561afd6fca5da'
TMP_MODULE="$(mktemp)"
trap 'rm -f "$TMP_MODULE"' EXIT
curl -fsSL --retry 3 --connect-timeout 10 \
  "https://raw.githubusercontent.com/ayman01323/boot-trading-bot/${MODULE_SHA}/SiRisky/overrides/sirisky/position_telegram.py" \
  -o "$TMP_MODULE"
grep -q 'class PositionTelegramReporter' "$TMP_MODULE"

sudo -n env TMP_MODULE="$TMP_MODULE" MODULE_SHA="$MODULE_SHA" bash -lc '
set -Eeuo pipefail
cd /root/SiRisky

echo "=== DEPLOY SIRISKY TELEGRAM POSITION REPORTER ==="
backup="/root/SiRisky/data/telegram-position-backup-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup"
cp -a run.py "$backup/run.py"
[ -f sirisky/position_telegram.py ] && cp -a sirisky/position_telegram.py "$backup/position_telegram.py" || true
cp "$TMP_MODULE" sirisky/position_telegram.py
chmod 0644 sirisky/position_telegram.py

PYTHONPATH=. .venv/bin/python - <<"PY"
from pathlib import Path
p=Path("run.py")
s=p.read_text(encoding="utf-8")
imp="from sirisky.position_telegram import PositionTelegramReporter\n"
anchor="from sirisky.manual_approval import ManualApprovalGate\n"
if imp not in s:
    if anchor not in s:
        raise SystemExit("run.py import anchor missing")
    s=s.replace(anchor,anchor+imp,1)

old="""def start(settings):\n    engine=SiRiskyEngine(settings); tg=TelegramClient(settings); stop=threading.Event(); tg.run_thread(lambda c,ch:telegram_handler(engine,settings,c,ch),stop)\n    tg.send(\"SiRisky started. Manual per-trade approval is supported; server-side transaction broadcast remains CSV-gated and external/manual signing is required when the approval gate is enabled.\")\n"""
new="""def start(settings):\n    engine=SiRiskyEngine(settings); tg=TelegramClient(settings); stop=threading.Event(); tg.run_thread(lambda c,ch:telegram_handler(engine,settings,c,ch),stop)\n    position_reporter=PositionTelegramReporter(settings)\n    position_notice_lock=threading.Lock()\n    def _dispatch_position_notices(result):\n        snapshot=dict(result or {})\n        def worker():\n            if not position_notice_lock.acquire(blocking=False):\n                return\n            try:\n                for message in position_reporter.messages(snapshot,engine):\n                    if message:\n                        tg.send(message)\n            except Exception as exc:\n                print(f\"SiRisky Telegram position notice error: {type(exc).__name__}\",file=sys.stderr,flush=True)\n            finally:\n                position_notice_lock.release()\n        threading.Thread(target=worker,name=\"sirisky-position-telegram\",daemon=True).start()\n    tg.send(\"SiRisky started. Manual per-trade approval is supported; server-side transaction broadcast remains CSV-gated and external/manual signing is required when the approval gate is enabled.\")\n"""
if "position_reporter=PositionTelegramReporter(settings)" not in s:
    if old not in s:
        raise SystemExit("run.py start anchor missing")
    s=s.replace(old,new,1)

old_cycle="""            result=engine.run_once(); status=str(result.get(\"status\") or \"\")\n            if status==\"WAITING_FOR_MANUAL_APPROVAL\":\n"""
new_cycle="""            result=engine.run_once(); status=str(result.get(\"status\") or \"\")\n            _dispatch_position_notices(result)\n            if status==\"WAITING_FOR_MANUAL_APPROVAL\":\n"""
if "_dispatch_position_notices(result)" not in s:
    if old_cycle not in s:
        raise SystemExit("run.py cycle anchor missing")
    s=s.replace(old_cycle,new_cycle,1)

old_state="""            elif status in {\"OPENED\",\"CLOSED\"}:\n                # Position state changes remain immediate and are never rate-limited.\n                tg.send(_format_result_notice(result,settings))\n"""
new_state="""            elif status in {\"OPENED\",\"CLOSED\",\"HOLD\"}:\n                # Rich BUY/SELL/NewPoll45 messages are emitted asynchronously by\n                # PositionTelegramReporter so reporting can never delay trading.\n                pass\n"""
if old_state in s:
    s=s.replace(old_state,new_state,1)
elif "elif status in {\"OPENED\",\"CLOSED\",\"HOLD\"}:" not in s:
    raise SystemExit("run.py OPENED/CLOSED anchor missing")

p.write_text(s,encoding="utf-8")
PY

PYTHONPATH=. .venv/bin/python - <<"PY"
import csv
from pathlib import Path
p=Path("CSV/runtime.csv")
rows=list(csv.reader(p.open(encoding="utf-8-sig",newline="")))
key="telegram_open_position_notice_seconds"
found=False
for row in rows[1:]:
    if row and row[0].strip()==key:
        while len(row)<3: row.append("")
        row[1]="45"
        row[2]="NewPoll45 open LIVE position Telegram update interval"
        found=True
if not found:
    width=max(3,len(rows[0]) if rows else 3)
    row=[""]*width; row[0]=key; row[1]="45"; row[2]="NewPoll45 open LIVE position Telegram update interval"
    rows.append(row)
with p.open("w",encoding="utf-8",newline="") as f:
    csv.writer(f).writerows(rows)
PY

PYTHONPATH=. .venv/bin/python -m py_compile run.py sirisky/position_telegram.py

PYTHONPATH=. .venv/bin/python - <<"PY"
from pathlib import Path
from sirisky.config import Settings
from sirisky.position_telegram import PositionTelegramReporter
s=Settings.load()
r=PositionTelegramReporter(s)
r.state={}; r.state_path=Path("/tmp/sirisky-telegram-format-test.json")
r._market=lambda pool,mint:{
    "symbol":"TEST","name":"Formatter Test Token","dex":"testdex","pair_name":"TEST / SOL",
    "pool_id":pool,"viewer":"https://dexscreener.com/solana/TESTPOOL",
    "liquidity_usd":1000.0,"quote_depth_sol":5.0,"sol_usd_hint":100.0,
}
r._sol_usd=lambda market=None:100.0
r._network_fee_sol=lambda sig:0.0
pos={
    "position_id":"pos-format-test","pool_id":"TESTPOOL","mint":"TESTMINT","mode":"LIVE",
    "opened_epoch":1,"entry_sol":0.01,"entry_lamports":10000000,"buy_signature":"TESTSIG",
}
class E:
    def open_positions(self): return [pos]
msg=r._position_message("BUY",pos,E(),0.01,0.0,force=True)
assert "Open Position 1 of 1" in msg
for n in range(1,17):
    assert f"{n}." in msg, n
assert "NewPoll45" not in msg
print("telegram_formatter_16_fields=PASS")
PY

PYTHONPATH=. .venv/bin/python run.py selftest
systemctl restart sirisky.service
sleep 3
systemctl is-active --quiet sirisky.service

PYTHONPATH=. .venv/bin/python - <<"PY"
from sirisky.config import Settings
from sirisky.telegram import TelegramClient
s=Settings.load(); tg=TelegramClient(s)
ok=tg.send("✅ SiRisky Telegram upgraded\nBUY: immediate\nSELL: immediate\nOpen LIVE positions: NewPoll45 every 45 seconds\nPosition reports include token, SOL/USD values, P&L, pool liquidity/change, DEX viewer and time open.") if tg.configured() else False
print("telegram_configured="+str(tg.configured()).lower())
print("telegram_upgrade_test_sent="+str(bool(ok)).lower())
print("telegram_open_position_notice_seconds="+str(s.runtime().get("telegram_open_position_notice_seconds") or ""))
PY

echo "service=$(systemctl is-active sirisky.service)"
echo "module_sha=$MODULE_SHA"
echo "backup=$backup"
echo "stage3_sha256=$(sha256sum sirisky/stage3_risk.py | cut -d" " -f1)"
echo "telegram_position_reporting=DEPLOYED"
'
