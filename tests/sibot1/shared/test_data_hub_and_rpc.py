from sibot1_engines._shared.contracts import MarketEvent
from sibot1_engines._shared.data_hub import InMemorySharedDataHub
from sibot1_engines._shared.rpc_registry import RpcEndpoint, select_endpoints


def test_shared_data_hub_fans_same_event_to_independent_engines():
    hub = InMemorySharedDataHub(); seen = []
    hub.subscribe("gpt", lambda e: seen.append(("gpt", e.event_id)))
    hub.subscribe("gemini", lambda e: seen.append(("gemini", e.event_id)))
    hub.publish(MarketEvent(event_id="e1", chain="base", observed_at_ms=1, source="x", event_type="tick"))
    assert seen == [("gpt", "e1"), ("gemini", "e1")]


def test_hybrid_rpc_prefers_engine_dedicated_then_shared():
    rows = (
        RpcEndpoint("shared","*","base","a","ws","shared",10,True,"REF1","market"),
        RpcEndpoint("gpt","gpt","base","b","ws","dedicated",20,True,"REF2","market"),
    )
    out = select_endpoints(rows, engine_id="gpt", chain="base", mode="HYBRID")
    assert [x.rpc_id for x in out] == ["gpt", "shared"]
