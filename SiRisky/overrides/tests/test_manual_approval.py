from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sirisky.manual_approval import ManualApprovalGate
from sirisky.stage5_trade import Stage5Trade


class FakeSettings:
    def __init__(self, root: Path):
        self.csv_dir = root

    def runtime(self):
        return {
            "live_enabled": "1",
            "broadcast_enabled": "1",
            "manual_approval_enabled": "1",
            "manual_approval_require_external_signature": "1",
        }


class ManualApprovalTests(unittest.TestCase):
    def test_proposal_hash_binds_trade_fields(self):
        row = {
            "created_epoch": "100",
            "expires_epoch": "160",
            "order_id": "ord-1",
            "action": "BUY",
            "pool_id": "pool-1",
            "mint": "mint-1",
            "amount_raw": "500000",
            "expected_output_raw": "123",
            "min_output_raw": "120",
            "slippage_bps": "100",
            "price_impact_pct": "1.2",
            "estimated_fee_lamports": "5000",
            "exit_health_pct": "90",
            "strategy_id": "S1",
            "opportunity_id": "opp-1",
            "route_summary": "{}",
            "status": "WAITING_FOR_MANUAL_APPROVAL",
        }
        h1 = ManualApprovalGate.proposal_hash(row)
        row["status"] = "EXPIRED"
        self.assertEqual(h1, ManualApprovalGate.proposal_hash(row))
        row["amount_raw"] = "500001"
        self.assertNotEqual(h1, ManualApprovalGate.proposal_hash(row))

    def test_expiry_changes_waiting_to_expired(self):
        rows = [{"status": "WAITING_FOR_MANUAL_APPROVAL", "expires_epoch": "99"}]
        self.assertTrue(ManualApprovalGate._expire_rows(rows, 100))
        self.assertEqual(rows[0]["status"], "EXPIRED")

    def test_stage5_refuses_server_side_live_broadcast_in_manual_mode(self):
        with tempfile.TemporaryDirectory() as td:
            settings = FakeSettings(Path(td))
            order = SimpleNamespace(action="BUY", mint="mint", amount_raw=1, order_id="ord", reason="test")
            with self.assertRaisesRegex(RuntimeError, "MANUAL_APPROVAL_EXTERNAL_SIGNATURE_REQUIRED"):
                Stage5Trade(settings).execute(order)


if __name__ == "__main__":
    unittest.main()
