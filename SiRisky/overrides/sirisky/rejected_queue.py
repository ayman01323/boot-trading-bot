from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

from .csvio import as_bool
from .jupiter import WSOL_MINT


class RejectedOpportunityConsumer:
    """Claim rejected Solana opportunities and feed them through HR-CWH normally.

    The queue is only a candidate source. It does not grant LIVE permission and
    never bypasses Stage 1 quotes, Stage 2 strategy rules, Stage 3 risk, Stage 4
    dispatch, Stage 5 execution, manual approval, signing, capital or wallet gates.
    """

    def __init__(self, settings):
        self.settings = settings
        self.root = Path(
            os.environ.get(
                "BOOT_REJECTED_OPPORTUNITY_DIR",
                "/home/ayman01323/BOOT/data/candidates/REJECTED OPPORTUNITY",
            )
        )
        self.code_root = Path(
            os.environ.get(
                "BOOT_REJECTED_OPPORTUNITY_CODE_ROOT",
                "/home/ayman01323/BOOT/datacentre/rejected_router",
            )
        )
        self.worker_id = f"sirisky-{socket.gethostname()}-{os.getpid()}"
        self._queue_obj = None

    def enabled(self) -> bool:
        return as_bool(self.settings.runtime().get("rejected_opportunity_consumer_enabled"), False)

    def _queue(self):
        if self._queue_obj is not None:
            return self._queue_obj
        import sys
        if str(self.code_root) not in sys.path:
            sys.path.insert(0, str(self.code_root))
        from boot_platform.rejected_opportunity_queue import RejectedOpportunityQueue
        self._queue_obj = RejectedOpportunityQueue(self.root)
        return self._queue_obj

    def claim_pool(self):
        if not self.enabled():
            return None
        try:
            lease = int(float(self.settings.runtime().get("rejected_opportunity_claim_lease_seconds") or 120))
        except Exception:
            lease = 120
        claims = self._queue().claim(self.worker_id, limit=1, lease_seconds=max(30, lease))
        if not claims:
            return None
        item = claims[0]
        observations = list(item.get("observations") or [])
        latest = observations[0] if observations else {}
        try:
            payload = json.loads(str(latest.get("payload_json") or "{}"))
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}

        first_seen = int(item.get("first_seen_at") or 0)
        import time
        age_seconds = max(0, int(time.time()) - first_seen) if first_seen else 0
        if age_seconds <= 15 * 60:
            age_class = "NEW"
        elif age_seconds <= 2 * 60 * 60:
            age_class = "EARLY"
        else:
            age_class = "ESTABLISHED"

        pool = {
            "pool_id": str(item.get("pool_address") or item.get("token_address") or ""),
            "pair_address": str(item.get("pool_address") or ""),
            "base_mint": str(item.get("token_address") or ""),
            "quote_mint": WSOL_MINT,
            "chain": "solana",
            "dex": str(item.get("dex") or payload.get("dex") or "rejected-opportunity"),
            "age_seconds": age_seconds,
            "age_class": str(payload.get("age_class") or age_class).upper(),
            "temperature": str(payload.get("temperature") or "COLD").upper(),
            "temperature_hint": str(payload.get("temperature") or "COLD").upper(),
            "probe_sol": str(payload.get("probe_sol") or self.settings.runtime().get("auto_probe_sol") or "0.0005"),
            "source": "central-rejected-opportunity-queue",
            "status": "DISCOVERED",
            "manual_ready": "1",
            "enabled": "1",
            "_queue_candidate_id": str(item.get("candidate_id") or ""),
            "_queue_generation": int(item.get("generation") or 1),
        }
        # Pass only data/evidence forward. Stage 1 refreshes executable quotes.
        for key, value in payload.items():
            if key not in {"private_key", "secret", "seed", "api_key"}:
                pool[key] = value
        pool["risk_class"] = str(item.get("current_risk_class") or payload.get("risk_class") or "")
        pool["rejection_class"] = str(latest.get("rejection_class") or pool.get("risk_class") or "")
        pool["rejection_reason"] = str(latest.get("rejection_reason") or "")
        return item, pool

    @staticmethod
    def _hard_from_result(result: dict[str, Any]) -> tuple[bool, str]:
        text = " ".join(
            [str(result.get("error") or ""), *[str(x) for x in (result.get("reasons") or [])]]
        ).upper()
        terms = ("HONEYPOT", "NO_SELL", "FREEZE_AUTHORITY", "MINT_AUTHORITY", "MALICIOUS_TRANSFER")
        found = next((term for term in terms if term in text), "")
        return bool(found), found

    def finish(self, item: dict[str, Any], pool: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        status = str(result.get("status") or "").upper()
        passed = status in {"OPENED", "WAITING_FOR_MANUAL_APPROVAL"}
        decision = "PASS" if passed else "REJECT"
        hard, hard_reason = self._hard_from_result(result)
        reasons = [str(x) for x in (result.get("reasons") or [])]
        reason = "|".join(reasons) or str(result.get("error") or status or "SiRisky evaluation complete")
        evidence = {
            "queue_source": "REJECTED OPPORTUNITY",
            "engine_status": status,
            "pool_id": result.get("pool_id") or pool.get("pool_id"),
            "mint": result.get("mint") or pool.get("base_mint"),
            "forecast_net_pct": result.get("forecast_net_pct"),
            "exit_health_pct": result.get("exit_health_pct"),
            "stage3_passed": result.get("stage3_passed"),
            "reasons": reasons,
            "error": str(result.get("error") or "")[:500],
        }
        decision_row = self._queue().decide(
            candidate_id=str(item.get("candidate_id") or ""),
            worker_id=self.worker_id,
            decision=decision,
            strategy_buy_id="HR_CWH_QUEUE_BUY",
            strategy_sell_id="HR_CWH_QUEUE_EXIT",
            decision_reason=reason[:1000],
            temperature=str(pool.get("temperature") or ""),
            hard_block=hard,
            hard_block_reason=hard_reason,
            reverse_sell_ok=None,
            simulation_ok=None,
            evidence=evidence,
        )
        result["rejected_queue_candidate_id"] = str(item.get("candidate_id") or "")
        result["rejected_queue_generation"] = int(item.get("generation") or 1)
        result["rejected_queue_decision"] = decision_row.get("status")
        return result
