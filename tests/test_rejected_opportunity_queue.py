from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from boot_platform.rejected_opportunity_queue import RejectedOpportunityQueue


class RejectedOpportunityQueueTests(unittest.TestCase):
    def test_publish_claim_decide_and_material_recheck(self):
        with tempfile.TemporaryDirectory() as td:
            q = RejectedOpportunityQueue(Path(td) / "REJECTED OPPORTUNITY")

            first = q.publish(
                chain="solana",
                token_address="MintABC",
                pool_address="PoolABC",
                source_bot="grok",
                source_strategy_id="GROK_COMPACT_V1",
                source_event_id="evt-1",
                rejection_class="LP_CONCENTRATION_RISK",
                rejection_reason="Large Amount of LP Unlocked",
                priority=85,
                observed_at=1000,
                payload={"risk_class": "LP_CONCENTRATION_RISK", "liquidity_usd": 8000, "lp_status": "UNLOCKED"},
            )
            self.assertEqual(first.status, "NEW")
            self.assertTrue(first.sirisky_eligible)

            duplicate = q.publish(
                chain="solana",
                token_address="MintABC",
                pool_address="PoolABC",
                source_bot="grok",
                source_strategy_id="GROK_COMPACT_V1",
                source_event_id="evt-1",
                rejection_class="LP_CONCENTRATION_RISK",
                rejection_reason="Large Amount of LP Unlocked",
                priority=85,
                observed_at=1000,
                payload={"risk_class": "LP_CONCENTRATION_RISK", "liquidity_usd": 8000, "lp_status": "UNLOCKED"},
            )
            self.assertFalse(duplicate.inserted_observation)

            claims = q.claim("sirisky-w1", limit=5, lease_seconds=60)
            self.assertEqual(len(claims), 1)
            self.assertEqual(q.claim("sirisky-w2", limit=5, lease_seconds=60), [])

            result = q.decide(
                candidate_id=first.candidate_id,
                worker_id="sirisky-w1",
                decision="REJECT",
                strategy_buy_id="HR_CWH_BUY_V1",
                strategy_sell_id="HR_CWH_SELL_V1",
                decision_reason="exit impact too high",
            )
            self.assertEqual(result["status"], "SIRISKY_REJECT")
            self.assertEqual(q.claim("sirisky-w2", limit=5, lease_seconds=60), [])

            unchanged = q.publish(
                chain="solana",
                token_address="MintABC",
                pool_address="PoolABC",
                source_bot="gemini",
                source_strategy_id="GEMINI_PULSE_V1",
                source_event_id="evt-2",
                rejection_class="LP_CONCENTRATION_RISK",
                rejection_reason="LP still unlocked",
                priority=90,
                observed_at=1010,
                payload={"risk_class": "LP_CONCENTRATION_RISK", "liquidity_usd": 8000, "lp_status": "UNLOCKED"},
            )
            self.assertEqual(unchanged.status, "SIRISKY_REJECT")

            changed = q.publish(
                chain="solana",
                token_address="MintABC",
                pool_address="PoolABC",
                source_bot="gemini",
                source_strategy_id="GEMINI_PULSE_V1",
                source_event_id="evt-3",
                rejection_class="LP_CONCENTRATION_RISK",
                rejection_reason="LP still unlocked but liquidity changed materially",
                priority=90,
                observed_at=1020,
                payload={"risk_class": "LP_CONCENTRATION_RISK", "liquidity_usd": 65000, "lp_status": "UNLOCKED"},
            )
            self.assertEqual(changed.status, "RECHECK")
            self.assertEqual(changed.generation, 2)
            claims2 = q.claim("sirisky-w2", limit=5, lease_seconds=60)
            self.assertEqual(len(claims2), 1)
            self.assertEqual(claims2[0]["generation"], 2)

    def test_hard_block_and_non_solana_are_not_claimed(self):
        with tempfile.TemporaryDirectory() as td:
            q = RejectedOpportunityQueue(Path(td))
            hard = q.publish(
                chain="solana",
                token_address="BadMint",
                source_bot="learnerbot",
                rejection_class="TOKEN_SECURITY_SEVERE",
                rejection_reason="honeypot / no-sell",
                payload={"honeypot": True},
            )
            self.assertEqual(hard.status, "HARD_BLOCK")
            self.assertFalse(hard.sirisky_eligible)

            evm = q.publish(
                chain="base",
                token_address="0xabc",
                source_bot="gpt",
                rejection_class="STRATEGY_REJECT",
                rejection_reason="net edge below threshold",
            )
            self.assertEqual(evm.status, "RECORDED")
            self.assertFalse(evm.sirisky_eligible)
            self.assertEqual(q.claim("sirisky", limit=10), [])


if __name__ == "__main__":
    unittest.main()
