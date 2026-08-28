from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from .csvio import read_rows


DEX_PAIR_URL = "https://api.dexscreener.com/latest/dex/pairs/solana/{pair}"
DEX_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
COINGECKO_SOL = "https://api.coingecko.com/api/v3/simple/price"
SOL_SYMBOLS = {"SOL", "WSOL"}


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _fmt_sol(value) -> str:
    try:
        return f"{float(value):.9f} SOL"
    except Exception:
        return "n/a"


def _fmt_usd(value) -> str:
    try:
        return f"${float(value):,.4f}"
    except Exception:
        return "n/a"


def _fmt_pct(value) -> str:
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return "n/a"


def _fmt_duration(seconds) -> str:
    total=max(0,int(_num(seconds,0)))
    if total < 60:
        return f"{total}s"
    minutes, sec=divmod(total,60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes=divmod(minutes,60)
    return f"{hours}h {minutes:02d}m {sec:02d}s"


class PositionTelegramReporter:
    """Telegram BUY/SELL and NewPoll45 position reporting only.

    This module is observational. It never creates orders, changes risk state,
    or calls Jupiter. HOLD values come from Stage 6's already-computed executable
    reverse-quote percentage, so Telegram does not add trade-routing pressure.
    """

    def __init__(self, settings):
        self.settings=settings
        self.state_path=Path(settings.data_dir)/"telegram_position_state.json"
        self.state=self._load_state()
        self._sol_usd_cache=(0.0,0.0)

    def _load_state(self):
        try:
            raw=json.loads(self.state_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw,dict) else {}
        except Exception:
            return {}

    def _save_state(self):
        try:
            self.state_path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state,sort_keys=True),encoding="utf-8")
            tmp.replace(self.state_path)
        except Exception:
            pass

    def interval_seconds(self) -> float:
        try:
            return max(10.0,float(self.settings.runtime().get("telegram_open_position_notice_seconds") or 45))
        except Exception:
            return 45.0

    @staticmethod
    def _pick_pair(payload, pool_id: str, mint: str):
        pairs=(payload or {}).get("pairs") or []
        exact=[p for p in pairs if str(p.get("pairAddress") or "")==pool_id]
        if exact:
            return exact[0]
        mint_pairs=[]
        for p in pairs:
            base=str((p.get("baseToken") or {}).get("address") or "")
            quote=str((p.get("quoteToken") or {}).get("address") or "")
            if mint in {base,quote}:
                mint_pairs.append(p)
        return mint_pairs[0] if mint_pairs else (pairs[0] if pairs else {})

    def _market(self, pool_id: str, mint: str) -> dict:
        p={}
        headers={"User-Agent":"SiRisky/0.3-telegram-position"}
        try:
            r=requests.get(DEX_PAIR_URL.format(pair=pool_id),headers=headers,timeout=8)
            if r.ok:
                p=self._pick_pair(r.json() or {},pool_id,mint)
        except Exception:
            p={}
        if not p:
            try:
                r=requests.get(DEX_TOKEN_URL.format(mint=mint),headers=headers,timeout=8)
                if r.ok:
                    p=self._pick_pair(r.json() or {},pool_id,mint)
            except Exception:
                p={}

        base=p.get("baseToken") or {}
        quote=p.get("quoteToken") or {}
        base_addr=str(base.get("address") or "")
        quote_addr=str(quote.get("address") or "")
        token=base if base_addr==mint else (quote if quote_addr==mint else base)
        liq=p.get("liquidity") or {}
        base_symbol=str(base.get("symbol") or "").upper()
        quote_symbol=str(quote.get("symbol") or "").upper()
        quote_depth_sol=None
        if quote_symbol in SOL_SYMBOLS:
            quote_depth_sol=_num(liq.get("quote"),0.0)
        elif base_symbol in SOL_SYMBOLS:
            quote_depth_sol=_num(liq.get("base"),0.0)

        sol_usd_hint=0.0
        try:
            price_usd=_num(p.get("priceUsd"),0.0)
            price_native=_num(p.get("priceNative"),0.0)
            if quote_symbol in SOL_SYMBOLS and price_usd>0 and price_native>0:
                sol_usd_hint=price_usd/price_native
            elif base_symbol in SOL_SYMBOLS and price_usd>0:
                sol_usd_hint=price_usd
        except Exception:
            pass

        return {
            "symbol":str(token.get("symbol") or "UNKNOWN"),
            "name":str(token.get("name") or "Unknown token"),
            "dex":str(p.get("dexId") or "unknown"),
            "pair_name":f"{base.get('symbol') or '?'} / {quote.get('symbol') or '?'}",
            "pool_id":str(p.get("pairAddress") or pool_id),
            "viewer":str(p.get("url") or f"https://dexscreener.com/solana/{pool_id}"),
            "liquidity_usd":_num(liq.get("usd"),0.0),
            "quote_depth_sol":quote_depth_sol,
            "sol_usd_hint":sol_usd_hint,
        }

    def _sol_usd(self, market=None) -> float:
        now=time.time(); cached_at,cached=self._sol_usd_cache
        if cached>0 and now-cached_at<30:
            return cached
        hint=_num((market or {}).get("sol_usd_hint"),0.0)
        if hint>0:
            self._sol_usd_cache=(now,hint)
            return hint
        try:
            r=requests.get(COINGECKO_SOL,params={"ids":"solana","vs_currencies":"usd"},headers={"User-Agent":"SiRisky/0.3-telegram-position"},timeout=8)
            r.raise_for_status()
            value=_num(((r.json() or {}).get("solana") or {}).get("usd"),0.0)
            if value>0:
                self._sol_usd_cache=(now,value)
                return value
        except Exception:
            pass
        if cached>0:
            return cached
        return _num(self.settings.runtime().get("sol_usd_fallback"),0.0)

    def _network_fee_sol(self, signature: str) -> float:
        if not signature:
            return 0.0
        try:
            rpc=self.settings.resolve_rpc("http")
            payload={"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[signature,{"encoding":"jsonParsed","commitment":"confirmed","maxSupportedTransactionVersion":0}]}
            r=requests.post(rpc,json=payload,timeout=12); r.raise_for_status()
            result=(r.json() or {}).get("result") or {}
            return _num((result.get("meta") or {}).get("fee"),0.0)/1e9
        except Exception:
            return 0.0

    def _execution_by_signature(self, signature: str) -> dict:
        if not signature:
            return {}
        try:
            for row in reversed(read_rows(self.settings.csv_dir/"executions.csv")):
                if str(row.get("signature") or "")==signature:
                    return row
        except Exception:
            pass
        return {}

    def _live_index(self, engine, position_id: str):
        rows=[r for r in engine.open_positions() if str(r.get("mode") or "").upper()=="LIVE"]
        rows.sort(key=lambda r:int(_num(r.get("opened_epoch"),0)))
        total=len(rows)
        for idx,row in enumerate(rows,1):
            if str(row.get("position_id") or "")==position_id:
                return idx,total
        return (1,total) if total else (0,0)

    def _ensure_context(self, position: dict, engine, market=None, sol_usd=None) -> dict:
        pid=str(position.get("position_id") or "")
        ctx=self.state.get(pid) or {}
        market=market or self._market(str(position.get("pool_id") or ""),str(position.get("mint") or ""))
        sol_usd=_num(sol_usd,0.0) or self._sol_usd(market)
        entry_sol=_num(position.get("entry_sol"),_num(position.get("entry_lamports"),0)/1e9)
        if not ctx:
            pool_usd=_num(market.get("liquidity_usd"),0.0)
            ctx={
                "position_id":pid,
                "mint":str(position.get("mint") or ""),
                "pool_id":str(position.get("pool_id") or market.get("pool_id") or ""),
                "symbol":str(market.get("symbol") or "UNKNOWN"),
                "name":str(market.get("name") or "Unknown token"),
                "dex":str(market.get("dex") or "unknown"),
                "pair_name":str(market.get("pair_name") or "? / ?"),
                "viewer":str(market.get("viewer") or ""),
                "opened_epoch":int(_num(position.get("opened_epoch"),time.time())),
                "entry_sol":entry_sol,
                "entry_sol_usd":sol_usd,
                "entry_usd":entry_sol*sol_usd if sol_usd>0 else 0.0,
                "open_pool_usd":pool_usd,
                "open_pool_sol":pool_usd/sol_usd if sol_usd>0 else 0.0,
                "prev_position_sol":entry_sol,
                "prev_position_usd":entry_sol*sol_usd if sol_usd>0 else 0.0,
                "prev_pool_usd":pool_usd,
                "prev_pool_sol":pool_usd/sol_usd if sol_usd>0 else 0.0,
                "last_notice_epoch":0.0,
                "buy_signature":str(position.get("buy_signature") or ""),
                "buy_network_fee_sol":self._network_fee_sol(str(position.get("buy_signature") or "")),
            }
            self.state[pid]=ctx
            self._save_state()
        else:
            for key in ("symbol","name","dex","pair_name","viewer"):
                if market.get(key) and (not ctx.get(key) or str(ctx.get(key)).lower() in {"unknown","unknown token"}):
                    ctx[key]=market[key]
            self.state[pid]=ctx
        return ctx

    def _position_message(self, event: str, position: dict, engine, current_sol: float, net_pct: float, force=False):
        pid=str(position.get("position_id") or "")
        now=time.time()
        existing=self.state.get(pid) or {}
        if not force and now-_num(existing.get("last_notice_epoch"),0.0)<self.interval_seconds():
            return None

        pool_id=str(position.get("pool_id") or existing.get("pool_id") or "")
        mint=str(position.get("mint") or existing.get("mint") or "")
        market=self._market(pool_id,mint)
        sol_usd=self._sol_usd(market)
        ctx=self._ensure_context(position,engine,market,sol_usd)
        entry_sol=_num(ctx.get("entry_sol"),0.0)
        entry_usd=_num(ctx.get("entry_usd"),entry_sol*sol_usd)
        current_usd=current_sol*sol_usd if sol_usd>0 else 0.0
        unreal_sol=current_sol-entry_sol
        unreal_usd=current_usd-entry_usd
        prev_sol=_num(ctx.get("prev_position_sol"),entry_sol)
        prev_usd=_num(ctx.get("prev_position_usd"),entry_usd)
        delta_prev_sol=current_sol-prev_sol
        delta_prev_usd=current_usd-prev_usd
        delta_prev_pct=((current_sol/prev_sol)-1.0)*100.0 if prev_sol>0 else 0.0

        pool_usd=_num(market.get("liquidity_usd"),0.0)
        pool_sol=pool_usd/sol_usd if sol_usd>0 else 0.0
        open_pool_usd=_num(ctx.get("open_pool_usd"),0.0)
        open_pool_sol=_num(ctx.get("open_pool_sol"),0.0)
        pool_open_delta=pool_usd-open_pool_usd
        pool_open_pct=((pool_usd/open_pool_usd)-1.0)*100.0 if open_pool_usd>0 else 0.0
        prev_pool_usd=_num(ctx.get("prev_pool_usd"),open_pool_usd)
        prev_pool_sol=_num(ctx.get("prev_pool_sol"),open_pool_sol)
        pool_prev_delta=pool_usd-prev_pool_usd
        pool_prev_pct=((pool_usd/prev_pool_usd)-1.0)*100.0 if prev_pool_usd>0 else 0.0
        quote_depth=market.get("quote_depth_sol")
        idx,total=self._live_index(engine,pid)
        age=now-_num(ctx.get("opened_epoch"),now)

        title="🟢 BUY LIVE" if event=="BUY" else "🔄 NewPoll45"
        lines=[
            title,
            f"Open Position {idx or 1} of {total or 1}",
            f"1. Token: {ctx.get('symbol') or market.get('symbol') or 'UNKNOWN'} — {ctx.get('name') or market.get('name') or 'Unknown token'}",
            f"2. Mint: {mint}",
            f"3. Entry value: {_fmt_sol(entry_sol)} / {_fmt_usd(entry_usd)}",
            f"4. Current exit value: {_fmt_sol(current_sol)} / {_fmt_usd(current_usd)}",
            f"5. Unrealised P&L: {_fmt_sol(unreal_sol)} / {_fmt_usd(unreal_usd)} / {_fmt_pct(net_pct)}",
            "6. Realised P&L: 0.000000000 SOL / $0.0000 (position open)",
            f"7. Change since open: {_fmt_sol(unreal_sol)} / {_fmt_usd(unreal_usd)} / {_fmt_pct(net_pct)}",
            f"8. Since previous NewPoll45: {_fmt_sol(delta_prev_sol)} / {_fmt_usd(delta_prev_usd)} / {_fmt_pct(delta_prev_pct)}",
            f"9. Pool at open: {_fmt_sol(open_pool_sol)} eq / {_fmt_usd(open_pool_usd)}",
            f"10. Pool now: {_fmt_sol(pool_sol)} eq / {_fmt_usd(pool_usd)}",
            f"11. Pool change since open: {_fmt_pct(pool_open_pct)} / {_fmt_usd(pool_open_delta)}",
            f"12. Pool change since previous NewPoll45: {_fmt_pct(pool_prev_pct)} / {_fmt_usd(pool_prev_delta)}",
            f"13. SOL-quoted liquidity/depth: {_fmt_sol(quote_depth) if quote_depth is not None else _fmt_sol(pool_sol)+' total eq'}",
            f"14. DEX/pool: {market.get('dex') or ctx.get('dex') or 'unknown'} · {market.get('pair_name') or ctx.get('pair_name') or '? / ?'} · {pool_id}",
            f"15. DEX Viewer: {market.get('viewer') or ctx.get('viewer') or ('https://dexscreener.com/solana/'+pool_id)}",
            f"16. Time open: {_fmt_duration(age)}",
        ]
        if event=="BUY" and position.get("buy_signature"):
            lines.append(f"BUY tx: {position.get('buy_signature')}")

        ctx.update({
            "symbol":market.get("symbol") or ctx.get("symbol"),
            "name":market.get("name") or ctx.get("name"),
            "dex":market.get("dex") or ctx.get("dex"),
            "pair_name":market.get("pair_name") or ctx.get("pair_name"),
            "viewer":market.get("viewer") or ctx.get("viewer"),
            "prev_position_sol":current_sol,
            "prev_position_usd":current_usd,
            "prev_pool_usd":pool_usd,
            "prev_pool_sol":pool_sol,
            "last_notice_epoch":now,
        })
        self.state[pid]=ctx; self._save_state()
        return "\n".join(lines)[:4000]

    def opened(self, result: dict, engine):
        pos=dict(result.get("position") or {})
        if str(pos.get("mode") or "").upper()!="LIVE":
            return None
        entry_sol=_num(pos.get("entry_sol"),_num(pos.get("entry_lamports"),0)/1e9)
        return self._position_message("BUY",pos,engine,entry_sol,0.0,force=True)

    def hold(self, result: dict, engine):
        pid=str(result.get("position_id") or "")
        pos=next((dict(r) for r in engine.open_positions() if str(r.get("position_id") or "")==pid),None)
        if not pos or str(pos.get("mode") or "").upper()!="LIVE":
            return None
        net_pct=_num(result.get("net_pct"),0.0)
        entry_sol=_num(pos.get("entry_sol"),_num(pos.get("entry_lamports"),0)/1e9)
        current_sol=entry_sol*(1.0+net_pct/100.0)
        return self._position_message("POLL",pos,engine,current_sol,net_pct,force=False)

    def closed(self, result: dict, engine):
        row=dict(result.get("closed") or {})
        if str(row.get("mode") or "").upper()!="LIVE":
            return None
        pid=str(row.get("position_id") or "")
        ctx=self.state.get(pid) or {}
        mint=str(row.get("mint") or ctx.get("mint") or "")
        pool_id=str(ctx.get("pool_id") or "")
        market=self._market(pool_id,mint) if pool_id else {}
        sol_usd=self._sol_usd(market)
        entry_sol=_num(row.get("entry_sol"),_num(ctx.get("entry_sol"),0.0))
        sell_sig=str(row.get("sell_signature") or "")
        execution=self._execution_by_signature(sell_sig)
        actual_exit_sol=_num(execution.get("output_raw"),0.0)/1e9 if execution else _num(row.get("exit_sol"),0.0)
        gross_sol=actual_exit_sol-entry_sol
        buy_fee=_num(ctx.get("buy_network_fee_sol"),0.0)
        if buy_fee<=0:
            buy_fee=self._network_fee_sol(str(row.get("buy_signature") or ctx.get("buy_signature") or ""))
        sell_fee=self._network_fee_sol(sell_sig)
        realised_sol=gross_sol-buy_fee-sell_fee
        realised_usd=realised_sol*sol_usd if sol_usd>0 else 0.0
        gross_pct=(gross_sol/entry_sol*100.0) if entry_sol>0 else 0.0
        now=time.time(); age=now-_num(ctx.get("opened_epoch"),now)
        pool_usd=_num(market.get("liquidity_usd"),0.0)
        pool_sol=pool_usd/sol_usd if sol_usd>0 else 0.0
        open_pool_usd=_num(ctx.get("open_pool_usd"),0.0)
        pool_delta=pool_usd-open_pool_usd
        pool_pct=((pool_usd/open_pool_usd)-1)*100.0 if open_pool_usd>0 else 0.0
        live_now=len([r for r in engine.open_positions() if str(r.get("mode") or "").upper()=="LIVE"])
        lines=[
            "🔴 SELL LIVE",
            f"Position closed · Open LIVE positions now: {live_now}",
            f"Token: {ctx.get('symbol') or market.get('symbol') or 'UNKNOWN'} — {ctx.get('name') or market.get('name') or 'Unknown token'}",
            f"Mint: {mint}",
            f"Entry: {_fmt_sol(entry_sol)} / {_fmt_usd(entry_sol*_num(ctx.get('entry_sol_usd'),sol_usd))}",
            f"Actual SELL proceeds: {_fmt_sol(actual_exit_sol)} / {_fmt_usd(actual_exit_sol*sol_usd)}",
            f"Gross trade P&L: {_fmt_sol(gross_sol)} / {_fmt_usd(gross_sol*sol_usd)} / {_fmt_pct(gross_pct)}",
            f"Realised P&L after network fees: {_fmt_sol(realised_sol)} / {_fmt_usd(realised_usd)}",
            f"Network fees BUY+SELL: {_fmt_sol(buy_fee+sell_fee)} / {_fmt_usd((buy_fee+sell_fee)*sol_usd)}",
            f"Exit reason: {row.get('exit_reason') or 'n/a'}",
            f"Pool at open: {_fmt_sol(_num(ctx.get('open_pool_sol'),0.0))} eq / {_fmt_usd(open_pool_usd)}",
            f"Pool at close: {_fmt_sol(pool_sol)} eq / {_fmt_usd(pool_usd)}",
            f"Pool change: {_fmt_pct(pool_pct)} / {_fmt_usd(pool_delta)}",
            f"DEX/pool: {market.get('dex') or ctx.get('dex') or 'unknown'} · {market.get('pair_name') or ctx.get('pair_name') or '? / ?'} · {pool_id or 'n/a'}",
            f"DEX Viewer: {market.get('viewer') or ctx.get('viewer') or ('https://dexscreener.com/solana/'+pool_id if pool_id else 'n/a')}",
            f"Time held: {_fmt_duration(age)}",
        ]
        if sell_sig:
            lines.append(f"SELL tx: {sell_sig}")
        ctx["closed_epoch"]=int(now); ctx["last_notice_epoch"]=now; self.state[pid]=ctx; self._save_state()
        return "\n".join(lines)[:4000]

    def messages(self, result: dict, engine):
        status=str(result.get("status") or "").upper()
        if status=="OPENED":
            msg=self.opened(result,engine)
            return [msg] if msg else []
        if status=="HOLD":
            msg=self.hold(result,engine)
            return [msg] if msg else []
        if status=="CLOSED":
            msg=self.closed(result,engine)
            return [msg] if msg else []
        return []
