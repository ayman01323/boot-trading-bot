#!/usr/bin/env bash
set -Eeuo pipefail

test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323

sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky

echo "=== FINALIZE SIRISKY TELEGRAM POSITION REPORTER ==="
grep -q "from sirisky.position_telegram import PositionTelegramReporter" run.py
grep -q "_dispatch_position_notices(result)" run.py
grep -q "class PositionTelegramReporter" sirisky/position_telegram.py
grep -q "telegram_open_position_notice_seconds,45" CSV/runtime.csv

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
assert msg.startswith("🟢 BUY LIVE")
assert "Open Position 1 of 1" in msg
for n in range(1,17):
    assert f"{n}." in msg, n
assert "8. Since previous NewPoll45:" in msg
assert "12. Pool change since previous NewPoll45:" in msg
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
print("trading_mode="+str(s.runtime().get("trading_mode") or ""))
print("live_enabled="+str(s.runtime().get("live_enabled") or ""))
print("broadcast_enabled="+str(s.runtime().get("broadcast_enabled") or ""))
print("manual_approval_enabled="+str(s.runtime().get("manual_approval_enabled") or ""))
PY

echo "service=$(systemctl is-active sirisky.service)"
echo "stage3_sha256=$(sha256sum sirisky/stage3_risk.py | cut -d" " -f1)"
echo "telegram_position_reporting=ACTIVE"
'
