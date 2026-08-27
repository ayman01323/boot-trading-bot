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

OPEN_HEADERS=["position_id","opportunity_id","strategy_id","pool_id","age_class","temperature","mint","opened_epoch","entry_sol","entry_lamports","token_raw","target_net_pct","max_hold_seconds","mode","buy_signature","status"]
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
    """Canonical Stage 1-8 controller.

    BUY:  1 -> 2 -> 3 -> 4 -> 5 -> Open Positions -> 6
    EXIT: 6 -> 4 -> 5 -> Closed Positions -> 7 -> 8 -> next Stage 2 cycle

    In SHADOW with paper_auto_trade_enabled=1 the lifecycle is fully automatic.
    Any non-SHADOW path keeps the manual/external-signature gate when enabled.
    """

    def __init__(self, settings=None):
        self.settings=settings or Settings.load()
        self.s1=Stage1Data(self.settings)
        self.s2=Stage2Strategy(self.settings)
        self.s3=Stage3Risk(self.settings)
        self.s4=Stage4Dispatcher()
        self.s5=Stage5Trade(self.settings)
        self.s6=Stage6Monitor(self.settings)
        self.s7=Stage7Cycle(self.settings)
        self.s8=Stage8Review(self.settings)
        self.approvals=ManualApprovalGate(self.settings)

    def open_positions(self):
        return [r for r in read_rows(self.settings.csv_dir/"open_positions.csv") if str(r.get("status") or "").upper()=="OPEN"]

    def pending_approvals(self):
        return self.approvals.pending()

    def _trading_mode(self):
        return str(self.settings.runtime().get("trading_mode") or "SHADOW").strip().upper()

    def _paper_auto_enabled(self):
        rt=self.settings.runtime()
        return self._trading_mode()=="SHADOW" and as_bool(rt.get("paper_auto_trade_enabled"),False)

    def _manual_approval_enabled(self):
        return as_bool(self.settings.runtime().get("manual_approval_enabled"),False)

    def _approval_required(self):
        # SHADOW paper execution is automatic by design. The manual gate is
        # retained for any non-SHADOW path and therefore cannot be silently
        # converted into an autonomous real-money signing path here.
        if self._paper_auto_enabled():
            return False
        return self._manual_approval_enabled()

    def _auto_candidate_evaluation_enabled(self):
        return as_bool(self.settings.runtime().get("auto_promote_to_selected"),False)

    def _candidate_limit(self):
        try:
            return max(1,min(25,int(float(self.settings.runtime().get("auto_evaluate_candidate_limit") or 5))))
        except Exception:
            return 5

    def _candidate_pools(self):
        rows=read_rows(self.settings.csv_dir/"stage1_candidates.csv")
        out=[]
        for row in rows[:self._candidate_limit()]:
            if str(row.get("status") or "DISCOVERED").upper() not in {"DISCOVERED","READY","CANDIDATE"}:
                continue
            pool=dict(row)
            # Compatibility with the current Stage-2 trigger. This is an
            # automatic Stage-1 -> Stage-2 feed, not a human approval.
            pool["manual_ready"]="1"
            pool["enabled"]="1"
            if not pool.get("temperature_hint"):
                pool["temperature_hint"]=pool.get("temperature") or "COLD"
            out.append(pool)
        return out

    def _save_open(self, rows):
        write_rows_atomic(self.settings.csv_dir/"open_positions.csv",OPEN_HEADERS,rows)

    def _log_order(self, order):
        append_row(self.settings.csv_dir/"orders.csv",ORDER_HEADERS,{
            "timestamp":int(time.time()),"order_id":order.order_id,"action":order.action,
            "mint":order.mint,"amount_raw":order.amount_raw,"opportunity_id":order.opportunity_id,
            "reason":order.reason,
        })

    @staticmethod
    def _candidate_result_summary(result,index):
        return {
            "candidate":index,
            "status":str(result.get("status") or ""),
            "pool_id":str(result.get("pool_id") or ""),
            "mint":str(result.get("mint") or ""),
            "stage3_passed":result.get("stage3_passed"),
            "forecast_net_pct":result.get("forecast_net_pct"),
            "exit_health_pct":result.get("exit_health_pct"),
            "reasons":list(result.get("reasons") or []),
            "error":str(result.get("error") or "")[:240],
        }

    def _evaluate_pool_for_entry(self,pool,discovery):
        try:
            probe=float(pool.get("probe_sol") or 0.0005)
            snap=self.s1.snapshot(pool,probe)
            for key in RISK_META_KEYS:
                if key in pool and str(pool.get(key) or "").strip() != "":
                    snap.meta[key]=pool.get(key)
            snap.meta.setdefault("reverse_quote_present", True)
        except Exception as exc:
            return {"status":"CANDIDATE_QUOTE_REJECT","pool_id":str(pool.get("pool_id") or ""),
                    "mint":str(pool.get("base_mint") or pool.get("mint") or ""),
                    "stage3_passed":None,"error":type(exc).__name__,"discovery":discovery}

        # Stage 2: strategy/opportunity only.
        opp=self.s2.create_opportunity(snap,pool)
        if not opp:
            return {"status":"NO_TRIGGER","pool_id":snap.pool_id,"mint":str(getattr(snap,"mint","") or pool.get("base_mint") or ""),
                    "stage3_passed":None,"discovery":discovery}

        # Stage 3: all pre-trade risk gates.
        risk=self.s3.check(opp)
        append_row(self.settings.csv_dir/"risk_checks.csv",RISK_HEADERS,{
            "timestamp":int(time.time()),"opportunity_id":opp.opportunity_id,"pool_id":opp.pool_id,
            "mint":opp.mint,"passed":str(risk.passed).lower(),"reasons":"|".join(risk.reasons),
            "forecast_net_pct":f"{opp.forecast_net_pct:.6f}","exit_health_pct":f"{opp.snapshot.exit_health_pct:.6f}",
        })
        common={
            "pool_id":opp.pool_id,
            "mint":opp.mint,
            "forecast_net_pct":round(float(opp.forecast_net_pct),6),
            "exit_health_pct":round(float(opp.snapshot.exit_health_pct),6),
            "min_forecast_net_pct":float(self.settings.risk().get("min_forecast_net_pct") or 0.25),
            "discovery":discovery,
        }
        if not risk.passed:
            return {**common,"status":"RISK_REJECT","stage3_passed":False,"reasons":risk.reasons}

        # Stage 4: one dispatcher only.
        order=self.s4.buy_order(opp)
        self._log_order(order)

        # Live/non-SHADOW remains approval-gated. SHADOW intentionally flows
        # straight into the same Stage 5 execution module automatically.
        if self._approval_required():
            try:
                prepared=self.approvals.prepare(order,{
                    "pool_id":opp.pool_id,"strategy_id":opp.strategy_id,
                    "exit_health_pct":opp.snapshot.exit_health_pct,
                    "risk_reasons":"|".join(risk.reasons),
                })
                return {**common,"status":"WAITING_FOR_MANUAL_APPROVAL","stage3_passed":True,
                        "order_id":order.order_id,"proposal":prepared["proposal"],"new_proposal":prepared["created"],
                        "risk_reasons":risk.reasons}
            except Exception as exc:
                return {**common,"status":"MANUAL_APPROVAL_PREP_FAILED","stage3_passed":True,
                        "order_id":order.order_id,"error":(str(exc)[:240] or type(exc).__name__)}

        # Stage 5: execution only. A failed/no-route candidate is a normal
        # automatic-engine outcome, not a process-wide RuntimeError. In SHADOW
        # the caller can continue to the next ranked candidate immediately.
        try:
            result=self.s5.execute(order)
        except Exception as exc:
            return {**common,"status":"EXECUTION_REJECT","stage3_passed":True,
                    "order_id":order.order_id,"stage5_status":"FAILED","error":str(exc)[:240] or type(exc).__name__}

        token_raw=int(result.get("output_raw") or 0)
        mode=str(result.get("mode") or "SHADOW")
        pos={
            "position_id":"pos-"+uuid.uuid4().hex[:12],
            "opportunity_id":opp.opportunity_id,
            "strategy_id":opp.strategy_id,
            "pool_id":opp.pool_id,
            "age_class":opp.age_class,
            "temperature":opp.temperature,
            "mint":opp.mint,
            "opened_epoch":int(time.time()),
            "entry_sol":opp.position_sol,
            "entry_lamports":order.amount_raw,
            "token_raw":token_raw,
            "target_net_pct":max(0.1,opp.forecast_net_pct),
            "max_hold_seconds":opp.max_hold_seconds,
            "mode":mode,
            "buy_signature":result.get("signature","") or "",
            "status":"OPEN",
        }
        rows=self.open_positions()
        rows.append(pos)
        self._save_open(rows)
        return {**common,"status":"OPENED","stage3_passed":True,"stage5_status":str(result.get("status") or ""),
                "position":pos,"execution":result,"automatic":self._paper_auto_enabled()}

    def entry_cycle(self):
        # Stage 1 continuous discovery/live data update.
        if hasattr(self.s1,"discover_if_due"):
            discovery=self.s1.discover_if_due()
        else:
            discovery={"status":"TEST_STUB","count":0,"updated":False}

        pools=self.settings.selected_pools()
        auto_mode=False
        if not pools and self._auto_candidate_evaluation_enabled():
            pools=self._candidate_pools()
            auto_mode=True
        if not pools:
            count=int(discovery.get("count") or 0)
            status="AUTO_CANDIDATES_READY" if count else "NO_ENABLED_POOL"
            return {"status":status,"candidate_count":count,"discovery":discovery}

        if auto_mode:
            attempted=0
            summaries=[]
            for pool in pools:
                attempted+=1
                result=self._evaluate_pool_for_entry(pool,discovery)
                summaries.append(self._candidate_result_summary(result,attempted))
                # Exactly one position/order lifecycle at a time. Stop as soon
                # as Stage 3 passes into either SHADOW execution or live review.
                if result.get("status") in {"OPENED","WAITING_FOR_MANUAL_APPROVAL","MANUAL_APPROVAL_PREP_FAILED"}:
                    result["auto_candidate_evaluation"]=True
                    result["attempted_candidates"]=attempted
                    result["candidate_results"]=summaries
                    return result

            stage3_passed=sum(1 for row in summaries if row.get("stage3_passed") is True)
            risk_rejects=sum(1 for row in summaries if row.get("status")=="RISK_REJECT")
            execution_rejects=sum(1 for row in summaries if row.get("status")=="EXECUTION_REJECT")
            quote_rejects=sum(1 for row in summaries if row.get("status")=="CANDIDATE_QUOTE_REJECT")
            no_triggers=sum(1 for row in summaries if row.get("status")=="NO_TRIGGER")
            # Do not mislabel the whole batch as RISK_REJECT when one or more
            # candidates actually passed Stage 3 and failed later in Stage 5.
            return {
                "status":"CANDIDATE_BATCH_NO_OPEN",
                "discovery":discovery,
                "auto_candidate_evaluation":True,
                "attempted_candidates":attempted,
                "stage3_passed_candidates":stage3_passed,
                "risk_rejects":risk_rejects,
                "execution_rejects":execution_rejects,
                "quote_rejects":quote_rejects,
                "no_triggers":no_triggers,
                "candidate_results":summaries,
            }

        return self._evaluate_pool_for_entry(pools[0],discovery)

    def monitor_cycle(self):
        # Stage 6 only monitors the Open Positions DB plus current live data and
        # strategy/exit rules. EXIT always routes back to the same Stage 4/5.
        rows=self.open_positions()
        if not rows:
            return {"status":"NO_OPEN_POSITION"}

        pos=rows[0]
        ev=self.s6.evaluate(pos)
        if ev["decision"]=="HOLD":
            return {"status":"HOLD","position_id":pos["position_id"],"net_pct":ev["net_pct"],
                    "temperature":ev.get("temperature"),"peak_net_pct":ev.get("peak_net_pct")}
        if int(ev.get("sell_raw") or 0)<=0:
            return {"status":"EXIT_BLOCKED_ZERO_BALANCE","position_id":pos["position_id"]}

        order=self.s4.exit_order(pos,ev["reason"],int(ev["sell_raw"]))
        self._log_order(order)

        if self._approval_required():
            try:
                prepared=self.approvals.prepare(order,{
                    "pool_id":str(pos.get("pool_id") or ""),
                    "strategy_id":str(pos.get("strategy_id") or ""),
                    "exit_health_pct":ev.get("exit_health_pct") or "",
                    "temperature":ev.get("temperature") or "",
                    "net_pct":ev.get("net_pct"),
                })
                return {"status":"WAITING_FOR_MANUAL_APPROVAL","order_id":order.order_id,
                        "position_id":pos.get("position_id"),"proposal":prepared["proposal"],
                        "new_proposal":prepared["created"],"exit_reason":ev.get("reason"),
                        "temperature":ev.get("temperature")}
            except Exception as exc:
                return {"status":"MANUAL_APPROVAL_PREP_FAILED","order_id":order.order_id,
                        "position_id":pos.get("position_id"),"error":(str(exc)[:240] or type(exc).__name__)}

        # Failed exits must leave the position OPEN so Stage 6 can retry on the
        # next cycle. Only a successful Stage-5 SELL may flow to Closed/7/8.
        try:
            sell=self.s5.execute(order)
        except Exception as exc:
            return {"status":"EXIT_EXECUTION_RETRY","position_id":pos.get("position_id"),
                    "order_id":order.order_id,"error":str(exc)[:240] or type(exc).__name__,
                    "exit_reason":ev.get("reason")}

        remaining=[r for r in rows if r.get("position_id")!=pos.get("position_id")]
        self._save_open(remaining)

        # Stage 5 successful SELL -> Closed DB through Stage 7 -> Stage 8 review.
        closed=self.s7.close(pos,sell,ev)
        review=self.s8.review(closed,pos)
        return {"status":"CLOSED","closed":closed,"review":review,
                "next":"STAGE_2","automatic":self._paper_auto_enabled()}

    def run_once(self):
        # The service loop calls this continuously. An open position means Stage
        # 6; otherwise the next cycle starts at Stage 1 and flows toward Stage 2.
        if self.open_positions():
            return self.monitor_cycle()
        return self.entry_cycle()
