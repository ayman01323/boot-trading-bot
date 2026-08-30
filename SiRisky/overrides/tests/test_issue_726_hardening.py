from __future__ import annotations

import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sirisky.stage5_trade import Stage5Trade, WSOL_MINT, reconcile_transaction, realised_cycle_pnl_lamports, account_funding_delta_lamports


TAKER="Wallet111111111111111111111111111111111111"
TARGET="Target111111111111111111111111111111111111"
WSOL_ACCOUNT="WsolAta111111111111111111111111111111111"
TARGET_ACCOUNT="TargetAta1111111111111111111111111111111"
DEX_WSOL="DexWsol1111111111111111111111111111111111"
DEX_TARGET="DexTarget11111111111111111111111111111111"
FEE_ACCOUNT="Fee1111111111111111111111111111111111111"


def transfer(source, destination, mint, amount):
    return {"parsed":{"type":"transferChecked","info":{"source":source,"destination":destination,"mint":mint,"tokenAmount":{"amount":str(amount)}}}}


def init(account, mint):
    return {"parsed":{"type":"initializeAccount3","info":{"account":account,"mint":mint,"owner":TAKER}}}


def tx(native_delta, fee, instructions):
    pre=50_000_000
    return {
        "slot":123,"blockTime":1788027485,
        "transaction":{"message":{"accountKeys":[{"pubkey":TAKER},{"pubkey":WSOL_ACCOUNT},{"pubkey":TARGET_ACCOUNT},{"pubkey":DEX_WSOL},{"pubkey":DEX_TARGET},{"pubkey":FEE_ACCOUNT}],"instructions":[]}},
        "meta":{"err":None,"fee":fee,"preBalances":[pre,0,0,0,0,0],"postBalances":[pre+native_delta,0,0,0,0,0],"preTokenBalances":[],"postTokenBalances":[],"innerInstructions":[{"index":0,"instructions":instructions}]},
    }


class SettlementMathTests(unittest.TestCase):
    def test_deal_1_matches_audited_settlement(self):
        buy=tx(-15_229_039,6_799,[
            init(WSOL_ACCOUNT,WSOL_MINT),init(TARGET_ACCOUNT,TARGET),
            transfer(WSOL_ACCOUNT,DEX_WSOL,WSOL_MINT,8_991_000),
            transfer(WSOL_ACCOUNT,FEE_ACCOUNT,WSOL_MINT,9_000),
            transfer(DEX_TARGET,TARGET_ACCOUNT,TARGET,130_307_601_523),
        ])
        sell=tx(7_992_971,6_006,[
            init(WSOL_ACCOUNT,WSOL_MINT),init(TARGET_ACCOUNT,TARGET),
            transfer(TARGET_ACCOUNT,DEX_TARGET,TARGET,130_307_601_523),
            transfer(DEX_WSOL,WSOL_ACCOUNT,WSOL_MINT,10_083_140),
            transfer(WSOL_ACCOUNT,FEE_ACCOUNT,WSOL_MINT,10_083),
        ])
        b=reconcile_transaction(buy,TAKER,TARGET); s=reconcile_transaction(sell,TAKER,TARGET)
        self.assertEqual(b["wsol_delta_raw"],-9_000_000)
        self.assertEqual(b["account_funding_delta_lamports"],-6_222_240)
        self.assertEqual(s["wsol_delta_raw"],10_073_057)
        self.assertEqual(s["account_funding_delta_lamports"],-2_074_080)
        self.assertEqual(realised_cycle_pnl_lamports(b,s),1_060_252)

    def test_persistent_wsol_account_funding_excludes_trade_cashflow(self):
        settlement={
            "native_delta_lamports":-10_000,
            "network_fee_lamports":5_000,
            "wsol_delta_raw":-9_000_000,
        }
        self.assertEqual(account_funding_delta_lamports(settlement,persistent_wsol=True),-5_000)
        self.assertEqual(account_funding_delta_lamports(settlement,persistent_wsol=False),8_995_000)

    def test_three_deal_total_matches_audit(self):
        settlements=[
            ({"wsol_delta_raw":-9_000_000,"network_fee_lamports":6_799},{"wsol_delta_raw":10_073_057,"network_fee_lamports":6_006}),
            ({"wsol_delta_raw":-9_000_000,"network_fee_lamports":6_879},{"wsol_delta_raw":9_295_542,"network_fee_lamports":6_404}),
            ({"wsol_delta_raw":-9_000_000,"network_fee_lamports":6_150},{"wsol_delta_raw":8_431_443,"network_fee_lamports":6_783}),
        ]
        self.assertEqual(sum(realised_cycle_pnl_lamports(b,s) for b,s in settlements),761_021)


class FakeSettings:
    def __init__(self): self.csv_dir=Path("/tmp/sirisky-test")
    def runtime(self): return {"same_mint_reentry_cooldown_seconds":"60"}


class EntryGuardTests(unittest.TestCase):
    def test_same_mint_recent_sell_is_blocked(self):
        now=int(time.time())
        rows={
            "executions.csv":[{"timestamp":str(now-8),"order_id":"sell-1","action":"SELL","mint":"M","status":"SUCCESS","signature":"sig"}],
            "orders.csv":[],"open_positions.csv":[],
        }
        def fake_read(path): return rows.get(Path(path).name,[])
        order=SimpleNamespace(action="BUY",mint="M",amount_raw=9_000_000,order_id="buy-2",opportunity_id="opp-2",reason="test")
        with patch("sirisky.stage5_trade.read_rows",side_effect=fake_read):
            with self.assertRaisesRegex(RuntimeError,"SAME_MINT_REENTRY_COOLDOWN"):
                Stage5Trade(FakeSettings())._entry_guard(order)

    def test_same_opportunity_cannot_execute_twice(self):
        rows={
            "executions.csv":[{"timestamp":"1","order_id":"buy-1","action":"BUY","mint":"M","status":"SUCCESS","signature":"sig"}],
            "orders.csv":[{"order_id":"buy-1","action":"BUY","mint":"M","opportunity_id":"opp-1"}],
            "open_positions.csv":[],
        }
        def fake_read(path): return rows.get(Path(path).name,[])
        order=SimpleNamespace(action="BUY",mint="M",amount_raw=9_000_000,order_id="buy-2",opportunity_id="opp-1",reason="test")
        with patch("sirisky.stage5_trade.read_rows",side_effect=fake_read):
            with self.assertRaisesRegex(RuntimeError,"DUPLICATE_ENTRY_BLOCKED"):
                Stage5Trade(FakeSettings())._entry_guard(order)


if __name__ == "__main__":
    unittest.main()
