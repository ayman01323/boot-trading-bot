#!/usr/bin/env bash
set -Eeuo pipefail

test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323

sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky

echo "=== SIRISKY OWNER PROFILE: 10PCT FEES / 10PCT TP / 3 POS / 8PCT ROUND / 3X DEPTH ==="

OPEN_LIVE=$(PYTHONPATH=. .venv/bin/python - <<"PY"
import csv
from pathlib import Path
p=Path("CSV/open_positions.csv")
n=0
if p.exists():
    with p.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("status") or "").upper()=="OPEN" and str(r.get("mode") or "").upper()=="LIVE":
                n += 1
print(n)
PY
)
echo "open_live_before=$OPEN_LIVE"
if [ "$OPEN_LIVE" != "0" ]; then
  echo "REFUSING_SOURCE_RESTART_WITH_OPEN_LIVE_POSITIONS=$OPEN_LIVE"
  exit 42
fi

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
for f in sirisky/engine.py sirisky/stage3_risk.py sirisky/safety_v2.py; do
  cp -a "$f" "$f.bak.$STAMP"
done

PYTHONPATH=. .venv/bin/python - <<"PY"
from pathlib import Path

MARKER = "OWNER_PROFILE_10PCT_3POS_20260828"

def patch_engine():
    p=Path("sirisky/engine.py")
    s=p.read_text(encoding="utf-8")
    if MARKER in s:
        print("engine_patch=already_present")
        return
    s=s.replace("import time, uuid", "import os, time, uuid", 1)
    old='''RISK_META_KEYS=(\n    "risk_flags","rugcheck_risks","poolcheck_risks","risk_text",\n    "lp_concentration_risk","lp_depth_test_pass","lp_depth_test_slippage_pct",\n    "recent_sell_sim_age_sec","lp_unlock_transparent","no_recent_liquidity_withdrawal",\n    "reverse_quote_present","active_liquidity_removal","catastrophic_price_impact",\n    "failed_simulation","stale_quote","malicious_deployer","wallet_signer_overlap","no_sell",\n)'''
    new='''RISK_META_KEYS=(\n    "risk_flags","rugcheck_risks","poolcheck_risks","risk_text",\n    "lp_concentration_risk","lp_depth_test_pass","lp_depth_test_slippage_pct",\n    "recent_sell_sim_age_sec","lp_unlock_transparent","no_recent_liquidity_withdrawal",\n    "reverse_quote_present","active_liquidity_removal","catastrophic_price_impact",\n    "failed_simulation","stale_quote","malicious_deployer","wallet_signer_overlap","no_sell",\n    "liquidity_usd","pool_depth_position_multiple",\n)'''
    if old not in s: raise SystemExit("engine RISK_META_KEYS pattern not found")
    s=s.replace(old,new,1)
    old='''    def _candidate_limit(self):\n        try:\n            return max(1,min(25,int(float(self.settings.runtime().get("auto_evaluate_candidate_limit") or 5))))\n        except Exception:\n            return 5\n'''
    new='''    def _candidate_limit(self):\n        override=getattr(self,"_candidate_limit_override",None)\n        if override is not None:\n            try:\n                return max(1,min(25,int(override)))\n            except Exception:\n                pass\n        try:\n            return max(1,min(25,int(float(self.settings.runtime().get("auto_evaluate_candidate_limit") or 5))))\n        except Exception:\n            return 5\n\n    def _max_open_positions(self):\n        try:\n            configured=max(1,int(float(self.settings.risk().get("max_open_positions") or 1)))\n        except Exception:\n            configured=1\n        raw=os.getenv("MAX_OPEN_POSITIONS")\n        if raw is None or str(raw).strip()=="":\n            return configured\n        try:\n            return max(1,min(configured,int(float(raw))))\n        except Exception:\n            return configured\n'''
    if old not in s: raise SystemExit("engine candidate_limit pattern not found")
    s=s.replace(old,new,1)
    old='''    def run_once(self):\n        # The service loop calls this continuously. An open position means Stage\n        # 6; otherwise the next cycle starts at Stage 1 and flows toward Stage 2.\n        if self.open_positions():\n            return self.monitor_cycle()\n        return self.entry_cycle()'''
    new='''    def run_once(self):\n        # OWNER_PROFILE_10PCT_3POS_20260828\n        # Monitor one open position every cycle. While below the configured\n        # portfolio cap, also test one ranked entry candidate. Rotate open rows\n        # after HOLD so all positions receive Stage-6 monitoring round-robin.\n        rows=self.open_positions()\n        if not rows:\n            return self.entry_cycle()\n\n        result=self.monitor_cycle()\n        status=str(result.get("status") or "")\n        current=self.open_positions()\n        if status=="HOLD" and current:\n            monitored_id=str(result.get("position_id") or "")\n            if len(current)>1:\n                self._save_open(current[1:]+current[:1])\n            if len(current)<self._max_open_positions():\n                self._candidate_limit_override=1\n                try:\n                    entry=self.entry_cycle()\n                finally:\n                    self._candidate_limit_override=None\n                if str(entry.get("status") or "") in {"OPENED","WAITING_FOR_MANUAL_APPROVAL","MANUAL_APPROVAL_PREP_FAILED"}:\n                    entry["monitored_position_id"]=monitored_id\n                    entry["portfolio_open_before_entry"]=len(current)\n                    entry["portfolio_max_open"]=self._max_open_positions()\n                    return entry\n                result["entry_scan_status"]=str(entry.get("status") or "")\n                result["entry_scan_attempted_candidates"]=int(entry.get("attempted_candidates") or 0)\n                result["portfolio_open"]=len(current)\n                result["portfolio_max_open"]=self._max_open_positions()\n        return result'''
    if old not in s: raise SystemExit("engine run_once pattern not found")
    s=s.replace(old,new,1)
    p.write_text(s,encoding="utf-8")
    print("engine_patch=applied")

def patch_safety():
    p=Path("sirisky/safety_v2.py")
    s=p.read_text(encoding="utf-8")
    if MARKER in s:
        print("safety_patch=already_present")
        return
    old='''    original_eval_pool = SiRiskyEngine._evaluate_pool_for_entry\n    def safe_eval_pool(self, pool, discovery):\n        patched = dict(pool)\n        patched["probe_sol"] = f"{entry_sol(self.settings):.9f}"\n        return original_eval_pool(self, patched, discovery)'''
    new='''    original_eval_pool = SiRiskyEngine._evaluate_pool_for_entry\n    def safe_eval_pool(self, pool, discovery):\n        # OWNER_PROFILE_10PCT_3POS_20260828\n        patched = dict(pool)\n        position_sol = entry_sol(self.settings)\n        patched["probe_sol"] = f"{position_sol:.9f}"\n        liquidity_usd = max(0.0, _num(patched.get("liquidity_usd"), 0.0))\n        position_usd = max(0.000001, position_sol * sol_usd(self.settings))\n        patched["pool_depth_position_multiple"] = f"{(liquidity_usd / position_usd):.6f}"\n        return original_eval_pool(self, patched, discovery)'''
    if old not in s: raise SystemExit("safety safe_eval_pool pattern not found")
    s=s.replace(old,new,1)
    p.write_text(s,encoding="utf-8")
    print("safety_patch=applied")

def patch_risk():
    p=Path("sirisky/stage3_risk.py")
    s=p.read_text(encoding="utf-8")
    if MARKER in s:
        print("risk_patch=already_present")
        return
    old='''        if _truthy(meta.get("malicious_deployer")):\n            hard.append("MALICIOUS_DEPLOYER")\n        if _truthy(meta.get("wallet_signer_overlap")):\n            hard.append("WALLET_SIGNER_OWNERSHIP_RISK")\n'''
    new='''        # OWNER_PROFILE_10PCT_3POS_20260828\n        # Owner-selected rug/deployer policy: capital-depth is the hard gate.\n        # Deployer/signer signals remain visible as advisory telemetry only.\n        min_depth_multiple = max(0.0, _num(cfg.get("min_pool_position_depth_multiple"), 3.0))\n        depth_multiple = _num(meta.get("pool_depth_position_multiple"), 0.0)\n        if depth_multiple < min_depth_multiple:\n            hard.append("POOL_DEPTH_LT_3X_POSITION")\n        if _truthy(meta.get("malicious_deployer")):\n            advisory.append("MALICIOUS_DEPLOYER_SIGNAL")\n        if _truthy(meta.get("wallet_signer_overlap")):\n            advisory.append("WALLET_SIGNER_OWNERSHIP_SIGNAL")\n'''
    if old not in s: raise SystemExit("risk deployer pattern not found")
    s=s.replace(old,new,1)
    start=s.find('''        # Dedicated high-risk policy for the RugCheck "Large Amount of LP''')
    end=s.find('''        if self._flag(meta, "high holder concentration", "single holder ownership")''')
    if start < 0 or end < 0 or end <= start: raise SystemExit("risk LP block pattern not found")
    replacement='''        # Under the owner-selected 3x-capital policy, LP/deployer flags are\n        # advisory rather than independent hard blockers. Direct executable\n        # protections above (reverse route, active withdrawal, impact, simulation)\n        # remain hard gates.\n        lp_unlocked = (\n            _truthy(meta.get("lp_concentration_risk"))\n            or self._flag(meta, "large amount of lp unlocked", "lp_concentration_risk")\n        )\n        if lp_unlocked:\n            advisory.append("LP_CONCENTRATION_RISK:Large Amount of LP Unlocked")\n\n'''
    s=s[:start]+replacement+s[end:]
    p.write_text(s,encoding="utf-8")
    print("risk_patch=applied")

patch_engine(); patch_safety(); patch_risk()
PY

PYTHONPATH=. .venv/bin/python - <<"PY"
import csv, os
from pathlib import Path

def update(path, updates):
    p=Path(path)
    with p.open(encoding="utf-8-sig", newline="") as f:
        rd=csv.DictReader(f); fields=list(rd.fieldnames or []); rows=list(rd)
    if len(fields)<2: raise SystemExit(f"bad csv schema: {path}")
    k="key" if "key" in fields else fields[0]
    v="value" if "value" in fields else fields[1]
    seen=set()
    for r in rows:
        key=str(r.get(k) or "")
        if key in updates:
            r[v]=str(updates[key]); seen.add(key)
    for key,val in updates.items():
        if key not in seen:
            r={x:"" for x in fields}; r[k]=key; r[v]=str(val); rows.append(r)
    tmp=p.with_suffix(".tmp")
    with tmp.open("w",encoding="utf-8",newline="") as f:
        wr=csv.DictWriter(f,fieldnames=fields); wr.writeheader(); wr.writerows(rows)
    os.replace(tmp,p)

update("CSV/runtime.csv", {
    "max_buy_network_fee_pct":"10.0",
    "max_sell_network_fee_pct":"10.0",
    "max_emergency_sell_network_fee_pct":"10.0",
    "max_priority_fee_lamports":"895000",
    "auto_probe_sol":"0.009",
    "auto_entry_min_sol":"0.009",
    "auto_entry_max_sol":"0.009",
    "auto_evaluate_candidate_limit":"5",
})
update("CSV/risk.csv", {
    "max_open_positions":"3",
    "max_round_trip_cost_pct":"8.0",
    "fast_take_profit_floor_pct":"10.0",
    "fast_take_profit_cap_pct":"10.0",
    "fast_target_net_pct":"10.0",
    "fast_target_net_cap_pct":"10.0",
    "min_pool_position_depth_multiple":"3.0",
})
print("csv_profile_updated=true")
PY

mkdir -p /etc/systemd/system/sirisky.service.d
cat >/etc/systemd/system/sirisky.service.d/30-owner-profile-3pos.conf <<"EOF"
[Service]
Environment=MAX_OPEN_POSITIONS=3
EOF
systemctl daemon-reload

PYTHONPATH=. .venv/bin/python -m py_compile sirisky/engine.py sirisky/stage3_risk.py sirisky/safety_v2.py
PYTHONPATH=. .venv/bin/python run.py selftest

systemctl restart sirisky.service
sleep 6
test "$(systemctl is-active sirisky.service)" = active

PYTHONPATH=. .venv/bin/python - <<"PY"
import os
from sirisky.config import Settings
from sirisky.engine import SiRiskyEngine
from sirisky.safety_v2 import entry_sol
s=Settings.load(); rt=s.runtime(); risk=s.risk(); e=SiRiskyEngine(s)
assert str(rt.get("trading_mode")).upper()=="LIVE"
assert str(rt.get("live_enabled"))=="1"
assert str(rt.get("broadcast_enabled"))=="1"
assert str(rt.get("manual_approval_enabled"))=="0"
assert abs(entry_sol(s)-0.009)<1e-12
assert float(rt.get("max_buy_network_fee_pct"))==10.0
assert float(rt.get("max_sell_network_fee_pct"))==10.0
assert float(rt.get("max_emergency_sell_network_fee_pct"))==10.0
assert int(float(rt.get("max_priority_fee_lamports")))==895000
assert int(float(risk.get("max_open_positions")))==3
assert float(risk.get("max_round_trip_cost_pct"))==8.0
assert float(risk.get("fast_take_profit_floor_pct"))==10.0
assert float(risk.get("fast_take_profit_cap_pct"))==10.0
assert float(risk.get("min_pool_position_depth_multiple"))==3.0
assert e._max_open_positions()==3
print("service=active")
print("trading_mode=LIVE")
print("fixed_entry_sol=0.009")
print("buy_network_fee_pct_cap=10")
print("sell_network_fee_pct_cap=10")
print("emergency_sell_network_fee_pct_cap=10")
print("priority_fee_cap_lamports=895000")
print("take_profit_true_net_pct=10")
print("max_open_positions=3")
print("max_round_trip_cost_pct=8")
print("rug_deployer_hard_gate=POOL_DEPTH_3X_POSITION")
print("reverse_quote_simulation_liquidity_removal_guards=PRESERVED")
print("FINAL_STATE=PASS_OWNER_PROFILE_10_10_3_8_3X")
PY
'
