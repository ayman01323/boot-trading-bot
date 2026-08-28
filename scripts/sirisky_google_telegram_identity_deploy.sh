#!/usr/bin/env bash
set -Eeuo pipefail

test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323

sudo -n bash <<'ROOT'
set -Eeuo pipefail
cd /root/SiRisky

target="sirisky/position_telegram.py"
test -f "$target"
cp -a "$target" "$target.bak-$(date +%Y%m%d%H%M%S)"

PYTHONPATH=. .venv/bin/python - <<'PY'
from pathlib import Path
p=Path("sirisky/position_telegram.py")
s=p.read_text(encoding="utf-8")

s=s.replace("Telegram BUY/SELL and NewPoll45 position reporting only.",
            "Telegram BUY/SELL and SiRisky position reporting only.")
s=s.replace("🔄 NewPoll45", "🔄 SiRisky Position Update")
s=s.replace("Since previous NewPoll45:", "Since previous SiRisky update:")
s=s.replace("Pool change since previous NewPoll45:", "Pool change since previous SiRisky update:")

old='''        quote_depth=market.get("quote_depth_sol")
        idx,total=self._live_index(engine,pid)
'''
new='''        quote_depth=market.get("quote_depth_sol")
        depth_sol=_num(quote_depth,pool_sol) if quote_depth is not None else pool_sol
        depth_usd=depth_sol*sol_usd if sol_usd>0 else pool_usd
        dexview=f"https://www.dexview.com/solana/{mint}" if mint else "n/a"
        dexscreener=market.get("viewer") or ctx.get("viewer") or (f"https://dexscreener.com/solana/{pool_id}" if pool_id else "n/a")
        idx,total=self._live_index(engine,pid)
'''
if old not in s:
    raise SystemExit("open depth anchor not found")
s=s.replace(old,new,1)

old='''            f"13. SOL-quoted liquidity/depth: {_fmt_sol(quote_depth) if quote_depth is not None else _fmt_sol(pool_sol)+' total eq'}",
            f"14. DEX/pool: {market.get('dex') or ctx.get('dex') or 'unknown'} · {market.get('pair_name') or ctx.get('pair_name') or '? / ?'} · {pool_id}",
            f"15. DEX Viewer: {market.get('viewer') or ctx.get('viewer') or ('https://dexscreener.com/solana/'+pool_id)}",
            f"16. Time open: {_fmt_duration(age)}",
'''
new='''            f"13. SOL-quoted liquidity/depth: {_fmt_sol(depth_sol)} / {_fmt_usd(depth_usd)}",
            f"14. DEX/pool: {market.get('dex') or ctx.get('dex') or 'unknown'} · {market.get('pair_name') or ctx.get('pair_name') or '? / ?'} · {pool_id}",
            f"15. DexView: {dexview}",
            f"16. Time open: {_fmt_duration(age)}",
            f"DexScreener: {dexscreener}",
'''
if old not in s:
    raise SystemExit("open lines anchor not found")
s=s.replace(old,new,1)

old='''        pool_delta=pool_usd-open_pool_usd
        pool_pct=((pool_usd/open_pool_usd)-1)*100.0 if open_pool_usd>0 else 0.0
        live_now=len([r for r in engine.open_positions() if str(r.get("mode") or "").upper()=="LIVE"])
'''
new='''        pool_delta=pool_usd-open_pool_usd
        pool_pct=((pool_usd/open_pool_usd)-1)*100.0 if open_pool_usd>0 else 0.0
        quote_depth=market.get("quote_depth_sol")
        depth_sol=_num(quote_depth,pool_sol) if quote_depth is not None else pool_sol
        depth_usd=depth_sol*sol_usd if sol_usd>0 else pool_usd
        dexview=f"https://www.dexview.com/solana/{mint}" if mint else "n/a"
        dexscreener=market.get("viewer") or ctx.get("viewer") or (f"https://dexscreener.com/solana/{pool_id}" if pool_id else "n/a")
        live_now=len([r for r in engine.open_positions() if str(r.get("mode") or "").upper()=="LIVE"])
'''
if old not in s:
    raise SystemExit("sell depth anchor not found")
s=s.replace(old,new,1)

old='''            "🔴 SELL LIVE",
            f"Position closed · Open LIVE positions now: {live_now}",
'''
new='''            "🔴 SELL LIVE",
            f"Open Position 0 of {live_now} — this position is closed; {live_now} LIVE position(s) remain",
'''
if old not in s:
    raise SystemExit("sell counter anchor not found")
s=s.replace(old,new,1)

old='''            f"Pool change: {_fmt_pct(pool_pct)} / {_fmt_usd(pool_delta)}",
            f"DEX/pool: {market.get('dex') or ctx.get('dex') or 'unknown'} · {market.get('pair_name') or ctx.get('pair_name') or '? / ?'} · {pool_id or 'n/a'}",
            f"DEX Viewer: {market.get('viewer') or ctx.get('viewer') or ('https://dexscreener.com/solana/'+pool_id if pool_id else 'n/a')}",
            f"Time held: {_fmt_duration(age)}",
'''
new='''            f"Pool change: {_fmt_pct(pool_pct)} / {_fmt_usd(pool_delta)}",
            f"SOL-quoted liquidity/depth: {_fmt_sol(depth_sol)} / {_fmt_usd(depth_usd)}",
            f"DEX/pool: {market.get('dex') or ctx.get('dex') or 'unknown'} · {market.get('pair_name') or ctx.get('pair_name') or '? / ?'} · {pool_id or 'n/a'}",
            f"DexView: {dexview}",
            f"DexScreener: {dexscreener}",
            f"Time held: {_fmt_duration(age)}",
'''
if old not in s:
    raise SystemExit("sell lines anchor not found")
s=s.replace(old,new,1)

if "NewPoll45" in s:
    raise SystemExit("foreign bot name still present")
if "www.dexview.com/solana/" not in s:
    raise SystemExit("DexView direct URL missing")
p.write_text(s,encoding="utf-8")
PY

.venv/bin/python -m py_compile "$target"
PYTHONPATH=. .venv/bin/python run.py selftest

! grep -q "NewPoll45" "$target"
grep -q "SiRisky Position Update" "$target"
grep -q "Since previous SiRisky update" "$target"
grep -q "www.dexview.com/solana/" "$target"
grep -q "SOL-quoted liquidity/depth:.*_fmt_usd" "$target"
grep -q "Open Position 0 of" "$target"

systemctl restart sirisky.service
sleep 4
test "$(systemctl is-active sirisky.service)" = active

PYTHONPATH=. .venv/bin/python - <<'PY'
from sirisky.config import Settings
from sirisky.telegram import TelegramClient
s=Settings.load(); rt=s.runtime()
assert str(rt.get("trading_mode") or "").upper()=="LIVE"
assert str(rt.get("live_enabled"))=="1"
assert str(rt.get("broadcast_enabled"))=="1"
assert str(rt.get("manual_approval_enabled"))=="0"
assert abs(float(rt.get("auto_entry_min_sol"))-0.009172629)<1e-12
assert abs(float(rt.get("auto_entry_max_sol"))-0.009172629)<1e-12
msg=("SiRisky Telegram reporting upgrade deployed: BUY / SiRisky Position Update / SELL now use "
     "DexView direct links, SOL+USD P&L, SOL+USD pool depth, and consistent LIVE-position counts. "
     "The other bot name was removed from SiRisky reporting.")
tg=TelegramClient(s)
print("telegram_upgrade_notice_sent="+str(tg.send(msg) if tg.configured() else False).lower())
print("service=active")
print("trading_mode=LIVE")
print("live_enabled=1")
print("broadcast_enabled=1")
print("manual_approval_enabled=0")
print("fixed_entry_sol=0.009172629")
print("foreign_bot_name_present=false")
print("dexview_direct_link=true")
print("pool_depth_sol_usd=true")
print("DEPLOYMENT=PASS")
PY
ROOT
