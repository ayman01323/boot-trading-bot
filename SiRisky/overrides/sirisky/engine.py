from __future__ import annotations

import time, uuid
from .config import Settings
from .csvio import append_row, read_rows, write_rows_atomic, as_bool
from .manual_approval import ManualApprovalGate
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

RISK_META_KEYS=(
    "risk_flags","rugcheck_risks","poolcheck_risks","risk_text",
    "lp_concentration_risk","lp_depth_test_pass","lp_depth_test_slippage_pct",
    "recent_sell_sim_age_sec","lp_unlock_transparent","no_recent_liquidity_withdrawal",
    "reverse_quote_present","active_liquidity_removal","catastrophic_price_impact",
    "failed_simulation","stale_quote","malicious_deployer","wallet_signer_overlap","no_sell",
)

class SiRiskyEngine:
    def __init__(self, settings=None):
        self.settings=settings or Settings.load(); self.s1=Stage1Data(self.settings); self.s2=Stage2Strategy(self.settings); self.s3=Stage3Risk(self.settings); self.s4=Stage4Dispatcher(); self.s5=Stage5Trade(self.settings); self.s6=Stage6Monitor(self.settings); self.s7=Stage7Cycle(self.settings); self.s8=Stage8Review(self.settings); self.approvals=ManualApprovalGate(self.settings)

    def open_positions(self): return [r for r in read_rows(self.settings.csv_dir/"open_positions.csv") if str(r.get("status") or "").upper()=="OPEN"]

    def pending_approvals(self): return self.approvals.pending()

    def _manual_approval_enabled(self): return as_bool(self.settings.runtime().get("manual_approval_enabled"),False)

    def _auto_candidate_evaluation_enabled(self): return as_bool(self.settings.runtime().get("auto_promote_to_selected"),False)

    def _candidate_limit(self):
        try: return max(1,min(25,int(float(self.settings.runtime().get("auto_evaluate_candidate_limit") or 5))))
        except Exception: return 5

    def _candidate_pools(self):
        rows=read_rows(self.settings.csv_dir/"stage1_candidates.csv")
        out=[]
        for row in rows[:self._candidate_limit()]:
            if str(row.get("status") or "DISCOVERED").upper() not in {"DISCOVERED","READY","CANDIDATE"}: continue
            pool=dict(row)
            # Auto-promotion is evaluation-only. It satisfies the existing Stage-2
            # MANUAL_READY trigger in memory, but Stage 5 still cannot broadcast
            # because manual approval + external signature remain mandatory.
            pool["manual_ready"]="1"
            pool["enabled"]="1"
            if not pool.get("temperature_hint"): pool["temperature_hint"]=pool.get("temperature") or "COLD"
            out.append(pool)
        return out

    def _save_open(self, rows): write_rows_atomic(self.settings.csv_dir/"open_positions.csv",OPEN_HEADERS,rows)

    def _log_order(self, order): append_row(self.settings.csv_dir/"orders.csv",ORDER_HEADERS,{"timestamp":int(time.time()),"order_id":order.order_id,"action":order.action,"mint":order.mint,"amount_raw":order.amount_raw,"opportunity_id":order.opportunity_id,"reason":order.reason})

    def _evaluate_pool_for_entry(self,pool,discovery):
        try:
            probe=float(pool.get("probe_sol") or 0.0005); snap=self.s1.snapshot(pool,probe)
            # Preserve optional PoolCheck/RugCheck evidence supplied by a selected
            # pool or future Stage-1 enrichment. Stage 3 decides how each flag is
            # enforced; the engine never interprets it.
            for key in RISK_META_KEYS:
                if key in pool and str(pool.get(key) or "").strip() != "":
                    snap.meta[key]=pool.get(key)
            # A successful Stage-1 round trip already obtained an executable
            # reverse quote, unless an upstream source explicitly says otherwise.
            snap.meta.setdefault("reverse_quote_present", True)
        except Exception as exc:
            return {"status":"CANDIDATE_QUOTE_REJECT","pool_id":str(pool.get("pool_id") or ""),"error":type(exc).__name__,"discovery":discovery}
        opp=self.s2.create_opportunity(snap,pool)
        if not opp: return {"status":"NO_TRIGGER","pool_id":snap.pool_id,"discovery":discovery}
        risk=self.s3.check(opp); append_row(self.settings.csv_dir/"risk_checks.csv",RISK_HEADERS,{"timestamp":int(time.time()),"opportunity_id":opp.opportunity_id,"pool_id":opp.pool_id,"mint":opp.mint,"passed":str(risk.passed).lower(),"reasons":"|".join(risk.reasons),"forecast_net_pct":f"{opp.forecast_net_pct:.6f}","exit_health_pct":f"{opp.snapshot.exit_health_pct:.6f}"})
        if not risk.passed: return {"status":"RISK_REJECT","pool_id":opp.pool_id,"reasons":risk.reasons,"discovery":discovery}
        order=self.s4.buy_order(opp); self._log_order(order)
        if self._manual_approval_enabled():
            try:
                prepared=self.approvals.prepare(order,{"pool_id":opp.pool_id,"strategy_id":opp.strategy_id,"exit_health_pct":opp.snapshot.exit_health_pct,"risk_reasons":"|".join(risk.reasons)})
                return {"status":"WAITING_FOR_MANUAL_APPROVAL","order_id":order.order_id,"proposal":prepared["proposal"],"new_proposal":prepared["created"],"risk_reasons":risk.reasons,"discovery":discovery}
            except Exception as exc:
                return {"status":"MANUAL_APPROVAL_PREP_FAILED","order_id":order.order_id,"error":type(exc).__name__,"discovery":discovery}
        result=self.s5.execute(order)
        token_raw=int(result.get("output_raw") or 0); mode=str(result.get("mode") or "SHADOW")
        pos={"position_id":"pos-"+uuid.uuid4().hex[:12],"opportunity_id":opp.opportunity_id,"strategy_id":opp.strategy_id,"age_class":opp.age_class,"temperature":opp.temperature,"mint":opp.mint,"opened_epoch":int(time.time()),"entry_sol":opp.position_sol,"entry_lamports":order.amount_raw,"token_raw":token_raw,"target_net_pct":max(0.1,opp.forecast_net_pct),"max_hold_seconds":opp.max_hold_seconds,"mode":mode,"buy_signature":result.get("signature","") or "","status":"OPEN"}
        rows=self.open_positions(); rows.append(pos); self._save_open(rows)
        return {"status":"OPENED","position":pos,"execution":result,"discovery":discovery}

    def entry_cycle(self):
        if hasattr(self.s1,"discover_if_due"):
            discovery=self.s1.discover_if_due()
        else:
            discovery={"status":"TEST_STUB","count":0,"updated":False}

        pools=self.settings.selected_pools()
        auto_mode=False
        if not pools and self._auto_candidate_evaluation_enabled():
            pools=self._candidate_pools(); auto_mode=True
        if not pools:
            count=int(discovery.get("count") or 0)
            status="AUTO_CANDIDATES_READY" if count else "NO_ENABLED_POOL"
            return {"status":status,"candidate_count":count,"discovery":discovery}

        # In auto-armed manual-approval mode, try the highest-ranked candidates
        # until one reaches Stage 3 PASS. No transaction can be broadcast here.
        if auto_mode:
            last={"status":"NO_TRIGGER","discovery":discovery}
            attempted=0
            for pool in pools:
                attempted+=1
                result=self._evaluate_pool_for_entry(pool,discovery)
                if result.get("status") in {"WAITING_FOR_MANUAL_APPROVAL","MANUAL_APPROVAL_PREP_FAILED"}:
                    result["auto_candidate_evaluation"]=True; result["attempted_candidates"]=attempted; return result
                last=result
            last["auto_candidate_evaluation"]=True; last["attempted_candidates"]=attempted; return last

        return self._evaluate_pool_for_entry(pools[0],discovery)

    def monitor_cycle(self):
        rows=self.open_positions()
        if not rows: return {"status":"NO_OPEN_POSITION"}
        pos=rows[0]; ev=self.s6.evaluate(pos)
        if ev["decision"]=="HOLD": return {"status":"HOLD","position_id":pos["position_id"],"net_pct":ev["net_pct"],"temperature":ev.get("temperature"),"peak_net_pct":ev.get("peak_net_pct")}
        if int(ev.get("sell_raw") or 0)<=0: return {"status":"EXIT_BLOCKED_ZERO_BALANCE","position_id":pos["position_id"]}
        order=self.s4.exit_order(pos,ev["reason"],int(ev["sell_raw"])); self._log_order(order)
        if self._manual_approval_enabled():
            try:
                prepared=self.approvals.prepare(order,{"pool_id":str(pos.get("pool_id") or ""),"strategy_id":str(pos.get("strategy_id") or ""),"exit_health_pct":ev.get("exit_health_pct") or "","temperature":ev.get("temperature") or "","net_pct":ev.get("net_pct")})
                return {"status":"WAITING_FOR_MANUAL_APPROVAL","order_id":order.order_id,"position_id":pos.get("position_id"),"proposal":prepared["proposal"],"new_proposal":prepared["created"],"exit_reason":ev.get("reason"),"temperature":ev.get("temperature")}
            except Exception as exc:
                return {"status":"MANUAL_APPROVAL_PREP_FAILED","order_id":order.order_id,"position_id":pos.get("position_id"),"error":type(exc).__name__}
        sell=self.s5.execute(order)
        remaining=[r for r in rows if r.get("position_id")!=pos.get("position_id")]; self._save_open(remaining)
        closed=self.s7.close(pos,sell,ev); review=self.s8.review(closed,pos)
        return {"status":"CLOSED","closed":closed,"review":review}

    def run_once(self):
        if self.open_positions(): return self.monitor_cycle()
        return self.entry_cycle()
