from __future__ import annotations

import time, uuid
from .config import Settings
from .csvio import append_row, read_rows, write_rows_atomic
from .stage1_data import Stage1Data
from .stage2_strategy import Stage2Strategy
from .stage3_risk import Stage3Risk
from .stage4_dispatch import Stage4Dispatcher
from .stage5_trade import Stage5Trade
from .stage6_monitor import Stage6Monitor
from .stage7_cycle import Stage7Cycle
from .stage8_review import Stage8Review

OPEN_HEADERS=["position_id","opportunity_id","strategy_id","age_class","temperature","mint","opened_epoch","entry_sol","entry_lamports","token_raw","target_net_pct","max_hold_seconds","mode","buy_signature","status"]
RISK_HEADERS=["timestamp","opportunity_id","pool_id","mint","passed","reasons","forecast_net_pct","exit_health_pct"]
ORDER_HEADERS=["timestamp","order_id","action","mint","amount_raw","opportunity_id","reason"]

class SiRiskyEngine:
    def __init__(self, settings=None):
        self.settings=settings or Settings.load(); self.s1=Stage1Data(self.settings); self.s2=Stage2Strategy(self.settings); self.s3=Stage3Risk(self.settings); self.s4=Stage4Dispatcher(); self.s5=Stage5Trade(self.settings); self.s6=Stage6Monitor(self.settings); self.s7=Stage7Cycle(self.settings); self.s8=Stage8Review(self.settings)

    def open_positions(self): return [r for r in read_rows(self.settings.csv_dir/"open_positions.csv") if str(r.get("status") or "").upper()=="OPEN"]

    def _save_open(self, rows): write_rows_atomic(self.settings.csv_dir/"open_positions.csv",OPEN_HEADERS,rows)

    def _log_order(self, order): append_row(self.settings.csv_dir/"orders.csv",ORDER_HEADERS,{"timestamp":int(time.time()),"order_id":order.order_id,"action":order.action,"mint":order.mint,"amount_raw":order.amount_raw,"opportunity_id":order.opportunity_id,"reason":order.reason})

    def entry_cycle(self):
        # Stage 1 discovers fresh Solana pools automatically from public market data.
        # Discovery writes CSV/stage1_candidates.csv only; it never auto-promotes a
        # candidate into the execution-approved stage1_selected_pools.csv.
        if hasattr(self.s1, "discover_if_due"):
            discovery=self.s1.discover_if_due()
        else:
            # Keeps existing isolated flow tests/stubs compatible.
            discovery={"status":"TEST_STUB","count":0,"updated":False}
        pools=self.settings.selected_pools()
        if not pools:
            count=int(discovery.get("count") or 0)
            status="AUTO_CANDIDATES_READY" if count else "NO_ENABLED_POOL"
            return {"status":status,"candidate_count":count,"discovery":discovery}
        pool=pools[0]
        probe=float(pool.get("probe_sol") or 0.0005); snap=self.s1.snapshot(pool,probe); opp=self.s2.create_opportunity(snap,pool)
        if not opp: return {"status":"NO_TRIGGER","pool_id":snap.pool_id,"discovery":discovery}
        risk=self.s3.check(opp); append_row(self.settings.csv_dir/"risk_checks.csv",RISK_HEADERS,{"timestamp":int(time.time()),"opportunity_id":opp.opportunity_id,"pool_id":opp.pool_id,"mint":opp.mint,"passed":str(risk.passed).lower(),"reasons":"|".join(risk.reasons),"forecast_net_pct":f"{opp.forecast_net_pct:.6f}","exit_health_pct":f"{opp.snapshot.exit_health_pct:.6f}"})
        if not risk.passed: return {"status":"RISK_REJECT","reasons":risk.reasons,"discovery":discovery}
        order=self.s4.buy_order(opp); self._log_order(order); result=self.s5.execute(order)
        token_raw=int(result.get("output_raw") or 0); mode=str(result.get("mode") or "SHADOW")
        pos={"position_id":"pos-"+uuid.uuid4().hex[:12],"opportunity_id":opp.opportunity_id,"strategy_id":opp.strategy_id,"age_class":opp.age_class,"temperature":opp.temperature,"mint":opp.mint,"opened_epoch":int(time.time()),"entry_sol":opp.position_sol,"entry_lamports":order.amount_raw,"token_raw":token_raw,"target_net_pct":max(0.1,opp.forecast_net_pct),"max_hold_seconds":opp.max_hold_seconds,"mode":mode,"buy_signature":result.get("signature","") or "","status":"OPEN"}
        rows=self.open_positions(); rows.append(pos); self._save_open(rows)
        return {"status":"OPENED","position":pos,"execution":result,"discovery":discovery}

    def monitor_cycle(self):
        rows=self.open_positions()
        if not rows: return {"status":"NO_OPEN_POSITION"}
        pos=rows[0]; ev=self.s6.evaluate(pos)
        if ev["decision"]=="HOLD": return {"status":"HOLD","position_id":pos["position_id"],"net_pct":ev["net_pct"]}
        if int(ev.get("sell_raw") or 0)<=0: return {"status":"EXIT_BLOCKED_ZERO_BALANCE","position_id":pos["position_id"]}
        order=self.s4.exit_order(pos,ev["reason"],int(ev["sell_raw"])); self._log_order(order); sell=self.s5.execute(order)
        remaining=[r for r in rows if r.get("position_id")!=pos.get("position_id")]; self._save_open(remaining)
        closed=self.s7.close(pos,sell,ev); review=self.s8.review(closed,pos)
        return {"status":"CLOSED","closed":closed,"review":review}

    def run_once(self):
        if self.open_positions(): return self.monitor_cycle()
        return self.entry_cycle()
