from __future__ import annotations

from pathlib import Path
from typing import Any,Mapping
from uuid import uuid4

from sibot1_engines._shared.contracts import ExitIntent,MarketEvent,TradeIntent
from .settings_schema import Settings,load_settings
from .strategy import CompactFlowStrategy


class GrokCompactFlowEngine:
    engine_id="grok"; engine_version="1.0.0"
    def __init__(self,settings:Settings,runtime_dir:str|Path):
        self.settings=settings; self.runtime_dir=Path(runtime_dir); self.strategy=CompactFlowStrategy(settings); self._events=0; self._signals=0

    def on_market_event(self,event:MarketEvent)->TradeIntent|None:
        self._events+=1; confidence=self.strategy.entry_signal(event)
        if confidence is None:return None
        self._signals+=1
        return TradeIntent(intent_id=f"grok-{uuid4().hex}",engine_id=self.engine_id,engine_version=self.engine_version,strategy_id=self.settings.strategy_id,
            chain=event.chain,side="BUY",asset_in=str(event.asset_in),asset_out=str(event.asset_out),requested_input_amount=self.settings.trade_amount,
            created_at_ms=event.observed_at_ms,venue=str(event.payload.get("venue") or "") or None,confidence=confidence,signal_id=f"compact:{event.event_id}",
            market_event_id=event.event_id,metadata={"dev_selling":bool(event.payload.get("dev_selling",False)),"volume_velocity":str(event.payload.get("volume_velocity","0")),"pool_id":event.pool_id or ""})

    def on_position_update(self,update:Mapping[str,Any])->ExitIntent|None:
        d=self.strategy.exit_signal(update)
        if d is None:return None
        lot_id,fraction,reason=d
        return ExitIntent(intent_id=f"grok-exit-{uuid4().hex}",engine_id=self.engine_id,engine_version=self.engine_version,strategy_id=self.settings.strategy_id,
            chain=str(update.get("chain") or self.settings.chain),created_at_ms=int(update.get("observed_at_ms") or 0),lot_id=lot_id,exit_fraction=fraction,reason=reason)

    def health(self)->Mapping[str,Any]:return {"engine_id":self.engine_id,"version":self.engine_version,"events":self._events,"signals":self._signals}


def build_engine(settings_path:str|Path,runtime_dir:str|Path)->GrokCompactFlowEngine:return GrokCompactFlowEngine(load_settings(settings_path),runtime_dir)
