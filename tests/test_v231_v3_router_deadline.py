from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_pancake_v3_exactinput_uses_deadline_abi():
    from learnerbot.live_executor import V3_ROUTER_ABI
    comps=V3_ROUTER_ABI[0]["inputs"][0]["components"]
    assert [x["name"] for x in comps] == ["path","recipient","deadline","amountIn","amountOutMinimum"]


def test_v3_sim_and_prebroadcast_supply_deadline():
    s=(ROOT/"learnerbot/live_executor.py").read_text()
    assert 'self._deadline(),q["amount_in_raw"]' in s
    assert 'self._deadline(),sim["amount_in_raw"]' in s


def test_old_four_field_exactinput_call_is_gone():
    s=(ROOT/"learnerbot/live_executor.py").read_text()
    assert 'q["packed_path"],self.address,q["amount_in_raw"]' not in s
    assert 'sim["packed_path"],self.address,sim["amount_in_raw"]' not in s
