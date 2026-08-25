from decimal import Decimal
from sibot1_engines._shared.contracts import ExitIntent,MarketEvent,TradeIntent
from sibot1_engines.grok.engine import GrokCompactFlowEngine
from sibot1_engines.grok.settings_schema import Settings


def ev(**kw):
 d=dict(event_id="e",chain="solana",observed_at_ms=1,source="hub",event_type="pool_update",asset_in="USDC",asset_out="TOKEN",price=Decimal("1"),source_age_ms=10,payload={"venue":"raydium","confidence":"0.75","volume_velocity":"2","dev_selling":False});d.update(kw);return MarketEvent(**d)

def test_signal_and_dev_filter(tmp_path):
 e=GrokCompactFlowEngine(Settings(),tmp_path);o=e.on_market_event(ev());assert isinstance(o,TradeIntent) and o.engine_id=="grok"
 assert e.on_market_event(ev(payload={"confidence":"0.9","volume_velocity":"3","dev_selling":True})) is None

def test_exit_ownership(tmp_path):
 e=GrokCompactFlowEngine(Settings(),tmp_path);assert e.on_position_update({"engine_id":"gemini","lot_id":"x","pnl_pct":"9"}) is None
 o=e.on_position_update({"engine_id":"grok","lot_id":"g1","pnl_pct":"0.04","chain":"solana"});assert isinstance(o,ExitIntent) and o.lot_id=="g1"
