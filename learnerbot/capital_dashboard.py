from __future__ import annotations

import csv
import html
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from .config import load_chains, load_kv_scoped
from .multi_wallet_store import MultiWalletStore
from .user_registry import all_users, require_user, user_bool, user_setting

ERC20_ABI = [
    {"type":"function","name":"balanceOf","stateMutability":"view","inputs":[{"name":"account","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
    {"type":"function","name":"decimals","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint8"}]},
    {"type":"function","name":"symbol","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"string"}]},
]
SUCCESS_STATUSES={"SUCCESS","SUCCESS_FEE_PENDING"}
STABLE_SYMBOLS={"USDC","USDC.E","USDT","USDT.E","DAI","FDUSD","TUSD","USDP","BUSD"}
CG_NATIVE_IDS={"ethereum":"ethereum","base":"ethereum","arbitrum":"ethereum","bsc":"binancecoin","polygon":"polygon-ecosystem-token"}
CG_PLATFORM_IDS={"ethereum":"ethereum","base":"base","arbitrum":"arbitrum-one","bsc":"binance-smart-chain","polygon":"polygon-pos"}
_price_cache={"ts":0.0,"native":{},"tokens":{}}

def _rows(path:Path)->list[dict]:
    if not path.exists():return []
    with path.open("r",encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))

def _bool(v,default=False)->bool:
    if v is None:return default
    return str(v).strip().lower() in {"1","true","yes","on","y"}

def _dec(v,default="0")->Decimal:
    try:return Decimal(str(v))
    except (InvalidOperation,ValueError,TypeError):return Decimal(default)

def _allowed_chain(user:dict,slug:str)->bool:
    raw=str(user.get("allowed_chains") or "*").strip().lower()
    if raw in {"","*","all"}:return True
    allowed={x.strip() for x in raw.replace("|",",").split(",") if x.strip()}
    return slug.lower() in allowed

def _short(address:str)->str:
    a=str(address or "")
    return a if len(a)<=18 else f"{a[:8]}…{a[-6:]}"

def _fmt_amount(v:Decimal)->str:
    a=abs(v)
    if a==0:return "0"
    if a>=1000:return f"{v:,.2f}"
    if a>=1:return f"{v:,.6f}".rstrip("0").rstrip(".")
    if a>=Decimal("0.0001"):return f"{v:.8f}".rstrip("0").rstrip(".")
    return f"{v:.12f}".rstrip("0").rstrip(".")

def _native_prices(chains)->dict[str,Decimal]:
    now=time.time()
    if now-float(_price_cache.get("ts") or 0)<60 and _price_cache.get("native"):return dict(_price_cache["native"])
    ids=sorted({CG_NATIVE_IDS.get(c.slug) for c in chains if CG_NATIVE_IDS.get(c.slug)});out={}
    if ids:
        try:
            r=requests.get("https://api.coingecko.com/api/v3/simple/price",params={"ids":",".join(ids),"vs_currencies":"usd"},timeout=8,headers={"User-Agent":"BOOT-capital-dashboard/2.3.4"});r.raise_for_status();data=r.json()
            for c in chains:
                cid=CG_NATIVE_IDS.get(c.slug);usd=((data.get(cid) or {}).get("usd") if cid else None)
                if usd is not None:out[c.slug]=_dec(usd)
        except Exception:pass
    _price_cache["ts"]=now;_price_cache["native"]=out;return dict(out)

def _token_prices(chain,addresses:list[str])->dict[str,Decimal]:
    if not addresses:return {}
    platform=CG_PLATFORM_IDS.get(chain.slug)
    if not platform:return {}
    key=(chain.slug,tuple(sorted(a.lower() for a in addresses)));cached=_price_cache.setdefault("tokens",{}).get(key)
    if cached and time.time()-cached[0]<60:return dict(cached[1])
    out={}
    try:
        r=requests.get(f"https://api.coingecko.com/api/v3/simple/token_price/{platform}",params={"contract_addresses":",".join(addresses[:50]),"vs_currencies":"usd"},timeout=8,headers={"User-Agent":"BOOT-capital-dashboard/2.3.4"});r.raise_for_status();data=r.json()
        for addr,row in data.items():
            usd=(row or {}).get("usd")
            if usd is not None:out[addr.lower()]=_dec(usd)
    except Exception:pass
    _price_cache["tokens"][key]=(time.time(),out);return dict(out)

def _rpc(chain):
    last=None
    for url in chain.rpc_urls:
        try:
            w3=Web3(Web3.HTTPProvider(url,request_kwargs={"timeout":8}))
            if chain.chain_id in {56,137}:w3.middleware_onion.inject(ExtraDataToPOAMiddleware,layer=0)
            if w3.is_connected() and int(w3.eth.chain_id)==int(chain.chain_id):return w3
        except Exception as exc:last=exc
    raise RuntimeError(f"no working RPC for {chain.slug}: {last or 'not configured'}")

def _catalog(app,chain)->list[dict]:
    seen={};wrapped=str(chain.wrapped_base_address or "").lower()
    if Web3.is_address(wrapped):seen[wrapped]={"address":Web3.to_checksum_address(wrapped),"symbol":chain.wrapped_base_symbol or f"W{chain.native_symbol}","decimals":18,"source":"wrapped-base"}
    for row in _rows(Path(app.csv_dir)/"tokens.csv"):
        if str(row.get("chain_id") or "").strip()!=str(chain.chain_id) or not _bool(row.get("enabled"),True):continue
        a=str(row.get("address") or "").strip()
        if not Web3.is_address(a):continue
        try:dec=int(row.get("decimals") or 18)
        except Exception:dec=18
        seen[a.lower()]={"address":Web3.to_checksum_address(a),"symbol":str(row.get("symbol") or _short(a))[:24],"decimals":max(0,min(36,dec)),"source":"tokens.csv"}
    for row in _rows(Path(app.csv_dir)/"auto"/"product_universe.csv"):
        if str(row.get("chain_id") or "").strip()!=str(chain.chain_id):continue
        if not (_bool(row.get("auto_trade"),False) or _bool(row.get("auto_scan"),False)):continue
        a=str(row.get("address") or "").strip()
        if not Web3.is_address(a) or a.lower() in seen:continue
        seen[a.lower()]={"address":Web3.to_checksum_address(a),"symbol":str(row.get("symbol") or _short(a))[:24],"decimals":None,"source":"product-universe"}
        if len(seen)>=60:break
    return list(seen.values())[:60]

def wallet_chain_snapshot(app,address:str,chain,native_prices:dict[str,Decimal])->dict:
    addr=Web3.to_checksum_address(address);result={"chain_id":chain.chain_id,"chain_slug":chain.slug,"chain_name":chain.name,"native_symbol":chain.native_symbol,"address":addr,"native_balance":Decimal(0),"assets":[],"capital_usd":Decimal(0),"unpriced_assets":0,"rpc_ok":False,"error":""}
    try:
        w3=_rpc(chain);result["rpc_ok"]=True;native=Decimal(int(w3.eth.get_balance(addr)))/Decimal(10**18);result["native_balance"]=native;np=native_prices.get(chain.slug);native_usd=native*np if np is not None else None
        result["assets"].append({"symbol":chain.native_symbol,"address":"NATIVE","balance":native,"usd_price":np,"usd_value":native_usd})
        nonzero=[]
        for item in _catalog(app,chain):
            try:
                c=w3.eth.contract(address=item["address"],abi=ERC20_ABI);raw=int(c.functions.balanceOf(addr).call())
                if raw<=0:continue
                dec=item.get("decimals")
                if dec is None:
                    try:dec=int(c.functions.decimals().call())
                    except Exception:dec=18
                sym=str(item.get("symbol") or "").strip()
                if not sym or sym.startswith("0x"):
                    try:sym=str(c.functions.symbol().call())[:24]
                    except Exception:sym=_short(item["address"])
                bal=Decimal(raw)/(Decimal(10)**int(dec));nonzero.append({"symbol":sym,"address":item["address"],"balance":bal,"usd_price":None,"usd_value":None})
            except Exception:continue
        prices=_token_prices(chain,[x["address"] for x in nonzero]);wrapped=str(chain.wrapped_base_address or "").lower()
        for item in nonzero:
            sym=str(item["symbol"] or "").upper();price=prices.get(str(item["address"]).lower())
            if price is None and str(item["address"]).lower()==wrapped:price=native_prices.get(chain.slug)
            if price is None and sym in STABLE_SYMBOLS:price=Decimal(1)
            item["usd_price"]=price;item["usd_value"]=item["balance"]*price if price is not None else None;result["assets"].append(item)
        for item in result["assets"]:
            if item["usd_value"] is not None:result["capital_usd"]+=item["usd_value"]
            elif item["balance"]>0:result["unpriced_assets"]+=1
        result["assets"].sort(key=lambda x:(Decimal("-1")*(x["usd_value"] if x["usd_value"] is not None else Decimal(0)),str(x["symbol"])))
    except Exception as exc:result["error"]=f"{type(exc).__name__}: {str(exc)[:160]}"
    return result

def _trading_state(app,user:dict,wallet:dict,chain)->str:
    if not _bool(wallet.get("active"),False):return "STANDBY"
    if (user.get("status") or "").upper()!="ACTIVE":return "ACCOUNT-INACTIVE"
    if not _allowed_chain(user,chain.slug):return "CHAIN-BLOCKED"
    live=user_bool(app.csv_dir,user.get("telegram_id"),chain.chain_id,"live_trading_enabled",False);auto=user_bool(app.csv_dir,user.get("telegram_id"),chain.chain_id,"auto_trading_enabled",False)
    platform_live=_bool(load_kv_scoped(Path(app.csv_dir)/"live_trading_settings.csv",chain.chain_id).get("trading_enabled"),False);platform_auto=_bool(load_kv_scoped(Path(app.csv_dir)/"auto_trading_settings.csv",chain.chain_id).get("auto_trading_enabled"),False);engine_on=_bool(app.operator_settings().get("engine_enabled"),True)
    mode=str(user_setting(app.csv_dir,user.get("telegram_id"),chain.chain_id,"recommendation_mode","SHADOW") or "SHADOW").upper()
    if auto and live and mode=="ARMED" and _bool(user.get("can_auto_trade"),True):return "AUTO" if (platform_live and platform_auto and engine_on) else "AUTO-PAUSED"
    if live and _bool(user.get("can_manual_trade"),True):return "LIVE" if platform_live else "LIVE-PAUSED"
    if auto:return "AUTO-NOT-ARMED"
    return "OFF"

def _performance(app,telegram_id,wallet_ids:set[str],native_prices:dict[str,Decimal])->dict:
    tid=str(telegram_id);by_chain={};trades=0
    for row in _rows(Path(app.csv_dir)/"auto"/"auto_trade_execution.csv"):
        if str(row.get("telegram_id") or "").strip()!=tid or str(row.get("status") or "").upper() not in SUCCESS_STATUSES:continue
        slug=str(row.get("chain_slug") or "").strip().lower();net=_dec(row.get("realised_net_base"))-_dec(row.get("profit_fee_base"));fee=_dec(row.get("profit_fee_base"));r=by_chain.setdefault(slug,{"net":Decimal(0),"profit_fee":Decimal(0),"activation_fee":Decimal(0),"trades":0});r["net"]+=net;r["profit_fee"]+=fee;r["trades"]+=1;trades+=1
    chain_by_id={str(c.chain_id):c for c in load_chains(app,enabled_only=False)}
    for row in _rows(Path(app.csv_dir)/"auto"/"fee_ledger.csv"):
        if str(row.get("telegram_id") or "").strip()!=tid or str(row.get("fee_type") or "").upper()!="ACTIVATION" or str(row.get("status") or "").upper() not in {"BROADCAST","SUCCESS","CONFIRMED"}:continue
        c=chain_by_id.get(str(row.get("chain_id") or "").strip());slug=c.slug if c else str(row.get("chain_id") or "unknown");r=by_chain.setdefault(slug,{"net":Decimal(0),"profit_fee":Decimal(0),"activation_fee":Decimal(0),"trades":0});r["activation_fee"]+=_dec(row.get("fee_amount_base"))
    net_usd=Decimal(0);fee_usd=Decimal(0);usd_complete=True
    for slug,r in by_chain.items():
        p=native_prices.get(slug)
        if p is None and any(x!=0 for x in (r["net"],r["profit_fee"],r["activation_fee"])):usd_complete=False;continue
        if p is not None:net_usd+=r["net"]*p;fee_usd+=(r["profit_fee"]+r["activation_fee"])*p
    return {"by_chain":by_chain,"trades":trades,"net_usd":net_usd,"fees_usd":fee_usd,"usd_complete":usd_complete}

def user_dashboard_data(app,telegram_id)->dict:
    user=require_user(app.csv_dir,telegram_id,active=False);store=MultiWalletStore(app.data_dir,app.csv_dir);wallets=store.list_wallets(telegram_id);chains=[c for c in load_chains(app,enabled_only=True) if _allowed_chain(user,c.slug)];native_prices=_native_prices(chains);wallet_rows=[]
    for wallet in wallets:
        snapshots=[]
        with ThreadPoolExecutor(max_workers=max(1,min(5,len(chains)))) as ex:
            futs={ex.submit(wallet_chain_snapshot,app,wallet.get("address"),c,native_prices):c for c in chains}
            for fut in as_completed(futs):
                c=futs[fut]
                try:snap=fut.result()
                except Exception as exc:snap={"chain_id":c.chain_id,"chain_slug":c.slug,"chain_name":c.name,"native_symbol":c.native_symbol,"address":wallet.get("address"),"native_balance":Decimal(0),"assets":[],"capital_usd":Decimal(0),"unpriced_assets":0,"rpc_ok":False,"error":str(exc)[:160]}
                snap["trading_state"]=_trading_state(app,user,wallet,c);snapshots.append(snap)
        snapshots.sort(key=lambda x:x["chain_id"]);wallet_rows.append({**wallet,"chains":snapshots,"capital_usd":sum((s["capital_usd"] for s in snapshots),Decimal(0))})
    wallet_ids={str(w.get("wallet_id") or "") for w in wallets};perf=_performance(app,telegram_id,wallet_ids,native_prices)
    return {"user":user,"wallets":wallet_rows,"capital_usd":sum((w["capital_usd"] for w in wallet_rows),Decimal(0)),"performance":perf,"native_prices":native_prices}

def _performance_lines(perf:dict)->list[str]:
    lines=[]
    for slug,row in sorted(perf["by_chain"].items()):
        fees=row["profit_fee"]+row["activation_fee"]
        if row["trades"] or row["net"] or fees:lines.append(f"• <b>{html.escape(slug.upper())}</b>: trades {row['trades']} | net <b>{html.escape(_fmt_amount(row['net']))}</b> native | fees <b>{html.escape(_fmt_amount(fees))}</b> native")
    if not lines:lines.append("• No successful AUTO executions or platform fees recorded yet.")
    return lines

def user_dashboard_text(app,telegram_id)->str:
    d=user_dashboard_data(app,telegram_id);u=d["user"];L=["<b>📊 MY CAPITAL &amp; P&amp;L</b>","",f"Telegram ID: <code>{html.escape(str(telegram_id))}</code>",f"Account: <b>{html.escape((u.get('status') or '').upper())}</b> | plan <b>{html.escape(u.get('fee_plan_id') or '-')}</b>",""]
    if not d["wallets"]:return "\n".join(L+["No wallet configured.","","Create or import a wallet from <b>My Wallets &amp; Assets</b>."])
    any_unpriced=False
    for wallet in d["wallets"]:
        active=_bool(wallet.get("active"),False);L += [f"{'✅' if active else '▫️'} <b>{html.escape(wallet.get('label') or wallet.get('wallet_id') or 'Wallet')}</b> — <code>{html.escape(wallet.get('wallet_id') or '')}</code>",f"<code>{html.escape(wallet.get('address') or '')}</code>"]
        for c in wallet["chains"]:
            state=c.get("trading_state") or "OFF";assets=[x for x in c["assets"] if x["balance"]>0]
            if not assets and state in {"OFF","STANDBY"} and not c.get("error"):continue
            if c.get("error"):L.append(f"  ⚠️ <b>{html.escape(c['chain_slug'].upper())}</b> — RPC unavailable");continue
            parts=[f"{html.escape(x['symbol'])} {_fmt_amount(x['balance'])}" for x in assets[:8]];bal=" | ".join(parts) if parts else "0 balance";L.append(f"  {'🟢' if state=='AUTO' else '🔵' if state=='LIVE' else '⚪'} <b>{html.escape(c['chain_slug'].upper())}</b> [{html.escape(state)}] — {bal} | ≈ <b>${c['capital_usd']:,.2f}</b>")
            if c["unpriced_assets"]:any_unpriced=True
        L += [f"  Wallet priced capital ≈ <b>${wallet['capital_usd']:,.2f}</b>",""]
    L += [f"<b>Total priced capital ≈ ${d['capital_usd']:,.2f}</b>",f"Successful AUTO trades: <b>{d['performance']['trades']}</b>",f"Trading net after profit-share ≈ <b>${d['performance']['net_usd']:,.2f}</b>",f"Total platform fees ≈ <b>${d['performance']['fees_usd']:,.2f}</b>",""]+_performance_lines(d["performance"])
    if any_unpriced:L += ["","<i>Total capital excludes any non-zero asset for which a live USD price was unavailable.</i>"]
    L += ["","<i>Capital is current wallet value, not deposited cost basis. Net profit uses successful AUTO execution records after cycle gas and profit-share; manual trade P&amp;L is not inferred.</i>"]
    return "\n".join(L)

def master_dashboard_text(app,master_id)->str:
    require_user(app.csv_dir,master_id,active=True);users=[u for u in all_users(app.csv_dir) if (u.get("status") or "").upper() in {"ACTIVE","PENDING"}];L=["<b>🏦 MASTER — TRADING WALLETS &amp; CAPITAL</b>",""];total_capital=Decimal(0);total_net=Decimal(0);total_fees=Decimal(0);trading_wallets=0;wallets_seen=0
    for u in users:
        tid=str(u.get("telegram_id") or "")
        try:d=user_dashboard_data(app,tid)
        except Exception as exc:L += [f"⚠️ <code>{html.escape(tid)}</code> — {html.escape(str(exc)[:120])}",""];continue
        if not d["wallets"]:continue
        L += [f"<b>{html.escape(u.get('label') or 'User')}</b> — ID <code>{html.escape(tid)}</code>",f"Role {html.escape((u.get('role') or 'USER').upper())} | status {html.escape((u.get('status') or '').upper())} | plan {html.escape(u.get('fee_plan_id') or '-')}"]
        total_capital+=d["capital_usd"];total_net+=d["performance"]["net_usd"];total_fees+=d["performance"]["fees_usd"]
        for wallet in d["wallets"]:
            wallets_seen+=1;states=[c["trading_state"] for c in wallet["chains"]];is_trading=any(s in {"AUTO","LIVE"} for s in states)
            if is_trading:trading_wallets+=1
            L.append(f"{'🟢 TRADING' if is_trading else '⚪ STANDBY'} <b>{html.escape(wallet.get('label') or wallet.get('wallet_id') or 'Wallet')}</b> <code>{html.escape(_short(wallet.get('address') or ''))}</code>")
            for c in wallet["chains"]:
                assets=[x for x in c["assets"] if x["balance"]>0];state=c["trading_state"]
                if not assets and state in {"OFF","STANDBY"}:continue
                if c.get("error"):L.append(f"  ⚠️ {html.escape(c['chain_slug'].upper())} — RPC unavailable");continue
                part=" | ".join(f"{html.escape(x['symbol'])} {_fmt_amount(x['balance'])}" for x in assets[:6]) or "0 balance";L.append(f"  • <b>{html.escape(c['chain_slug'].upper())}</b> [{html.escape(state)}] {part} | ≈ <b>${c['capital_usd']:,.2f}</b>")
            L.append(f"  Wallet capital ≈ <b>${wallet['capital_usd']:,.2f}</b>")
        L += [f"User total capital ≈ <b>${d['capital_usd']:,.2f}</b> | net ≈ <b>${d['performance']['net_usd']:,.2f}</b> | fees ≈ <b>${d['performance']['fees_usd']:,.2f}</b>",""]
    L += ["<b>PLATFORM TOTALS</b>",f"Wallets registered: <b>{wallets_seen}</b>",f"Wallets currently LIVE/AUTO: <b>{trading_wallets}</b>",f"Total priced capital ≈ <b>${total_capital:,.2f}</b>",f"Total trading net after profit-share ≈ <b>${total_net:,.2f}</b>",f"Total platform fees ≈ <b>${total_fees:,.2f}</b>","","<i>Master view exposes only public wallet addresses/balances and accounting totals; private keys are never read for this dashboard.</i>"]
    return "\n".join(L)
