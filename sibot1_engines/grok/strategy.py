from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from sibot1_engines._shared.contracts import MarketEvent
from .settings_schema import Settings


class CompactFlowStrategy:
    """Grok's Solana confidence/volume/dev-wallet flow, normalized to v1 events."""
    def __init__(self, settings: Settings): self.s=settings

    def entry_signal(self,event:MarketEvent)->Decimal|None:
        if event.chain.lower()!=self.s.chain or not event.asset_in or not event.asset_out: return None
        if event.price is None or event.source_age_ms is None or event.source_age_ms<0 or event.source_age_ms>self.s.max_source_age_ms: return None
        if self.s.reject_dev_selling and bool(event.payload.get("dev_selling",False)): return None
        vv=Decimal(str(event.payload.get("volume_velocity","0")))
        conf=Decimal(str(event.payload.get("confidence","0")))
        if vv<self.s.min_volume_velocity or conf<self.s.min_confidence: return None
        return conf

    def exit_signal(self,update:Mapping[str,Any])->tuple[str,Decimal,str]|None:
        if str(update.get("engine_id") or "")!="grok": return None
        lot_id=str(update.get("lot_id") or "").strip()
        if not lot_id: return None
        pnl=Decimal(str(update.get("pnl_pct") or "0"))
        if pnl>=self.s.take_profit_pct: return lot_id,Decimal("1"),"take_profit"
        if pnl<=self.s.stop_loss_pct: return lot_id,Decimal("1"),"stop_loss"
        if bool(update.get("trend_reversal",False)): return lot_id,Decimal("1"),"trend_reversal"
        return None
