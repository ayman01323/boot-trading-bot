from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .contracts import TradeIntent
from .market_data import MarketEvidenceBook
from .ports import PoolCheckDecision


_HARD_BLOCK_TTL_SECONDS = 15 * 60
_TRANSIENT_HARD_BLOCK_CODES = {
    "RUGCHECK_UNAVAILABLE",
    "RUGCHECK_INVALID",
    "DEXSCREENER_UNAVAILABLE",
    "DEX_DATA_INVALID",
    "POOLCHECK_PROVIDER_ERROR",
}


class MandatoryShadowPoolCheck:
    """Central PoolCheck gate for the SiBot 1 SHADOW runtime.

    The bridge deliberately never upgrades partial evidence to LIVE PASS. A
    candidate may be studied in SHADOW when upstream evidence is incomplete, but
    LIVE sellability remains unproven until the hardened chain-specific full
    reverse/simulation/stress path is explicitly wired to a real executor.

    Structural Solana HARD_BLOCK decisions are cached briefly so the same unsafe
    mint is not repeatedly sent to external providers. Provider outages are not
    cached: they remain fail-closed but can recover on the next assessment.
    """

    def __init__(self, csv_dir: str | Path, evidence: MarketEvidenceBook):
        self.csv_dir = Path(csv_dir)
        self.evidence = evidence
        self._settings: dict[str, str] | None = None
        self._hard_blocks: dict[str, tuple[float, PoolCheckDecision]] = {}

    def _pool_settings(self) -> dict[str, str]:
        if self._settings is not None:
            return dict(self._settings)
        try:
            from learnerbot.config import load_kv_scoped
            self._settings = load_kv_scoped(self.csv_dir / "auto_trading_settings.csv", 0)
        except Exception:
            self._settings = {}
        return dict(self._settings)

    @staticmethod
    def _decision(verdict: str, *reasons: str, evidence: dict[str, Any] | None = None) -> PoolCheckDecision:
        return PoolCheckDecision(str(verdict).upper(), tuple(str(x) for x in reasons if x), dict(evidence or {}))

    def _cached_hard_block(self, mint: str) -> PoolCheckDecision | None:
        key = str(mint or "").strip()
        if not key:
            return None
        item = self._hard_blocks.get(key)
        if not item:
            return None
        expiry, original = item
        remaining = expiry - time.monotonic()
        if remaining <= 0:
            self._hard_blocks.pop(key, None)
            return None
        evidence = dict(original.evidence)
        evidence.update({
            "poolcheck_hard_block_cache_hit": True,
            "poolcheck_hard_block_cache_ttl_seconds": _HARD_BLOCK_TTL_SECONDS,
            "poolcheck_hard_block_cache_remaining_seconds": int(remaining),
        })
        reasons = tuple(original.reasons) + (
            f"duplicate HARD_BLOCK suppressed for {int(remaining)}s",
        )
        return self._decision("HARD_BLOCK", *reasons, evidence=evidence)

    def _remember_hard_block(self, mint: str, decision: PoolCheckDecision, reason_code: str) -> None:
        if decision.verdict.upper() != "HARD_BLOCK":
            return
        code = str(reason_code or "").upper()
        if code in _TRANSIENT_HARD_BLOCK_CODES:
            return
        key = str(mint or "").strip()
        if not key:
            return
        self._hard_blocks[key] = (time.monotonic() + _HARD_BLOCK_TTL_SECONDS, decision)
        if len(self._hard_blocks) > 2048:
            now = time.monotonic()
            self._hard_blocks = {k: v for k, v in self._hard_blocks.items() if v[0] > now}

    def _solana(self, mint: str, base_evidence: dict[str, Any]) -> PoolCheckDecision:
        cached = self._cached_hard_block(mint)
        if cached is not None:
            merged = {**base_evidence, **dict(cached.evidence)}
            return self._decision(cached.verdict, *cached.reasons, evidence=merged)

        try:
            from learnerbot.solana_pool_risk_gate import external_pool_check
            result = external_pool_check(mint, self._pool_settings())
        except Exception as exc:
            return self._decision(
                "HARD_BLOCK",
                f"POOLCHECK_PROVIDER_ERROR:{type(exc).__name__}",
                evidence={**base_evidence, "external_poolcheck_available": False},
            )
        verdict = str((result or {}).get("decision") or "HARD_BLOCK").upper()
        reason_code = str((result or {}).get("reason_code") or "POOLCHECK")
        reason = str((result or {}).get("reason") or reason_code)
        merged = {**base_evidence, **dict((result or {}).get("evidence") or {}), "poolcheck_reason_code": reason_code}
        if verdict in {"HARD_BLOCK", "COOLING", "SHADOW_ONLY"}:
            decision = self._decision(verdict, reason, evidence=merged)
            self._remember_hard_block(mint, decision, reason_code)
            return decision
        # External token/pool screening is necessary but not sufficient for LIVE.
        # Full-position reverse sellability, signed/full sell simulation and the
        # >=3x stress reverse quote are still intentionally absent from SHADOW.
        merged.update({
            "full_reverse_sellability_proven": False,
            "full_sell_simulation_proven": False,
            "stress_exit_3x_proven": False,
            "live_eligible": False,
        })
        return self._decision(
            "SHADOW_ONLY",
            "external PoolCheck passed; full reverse/simulation/3x stress evidence is not proven in SHADOW",
            evidence=merged,
        )

    def _evm(self, base_evidence: dict[str, Any]) -> PoolCheckDecision:
        required = (
            "exact_quote_ok",
            "simulation_ok",
            "liquidity_ok",
            "sellability_ok",
            "whole_route_approved",
        )
        missing_or_false = [key for key in required if not bool(base_evidence.get(key))]
        evidence = dict(base_evidence)
        evidence.update({
            "live_eligible": False,
            "full_external_rug_revalidation_proven": False,
            "stress_exit_3x_proven": False,
        })
        if missing_or_false:
            return self._decision(
                "SHADOW_ONLY",
                "upstream executable route evidence incomplete: " + ",".join(missing_or_false),
                evidence=evidence,
            )
        return self._decision(
            "SHADOW_ONLY",
            "route executable evidence present; independent hardened rug + 3x exit stress revalidation not proven in SHADOW",
            evidence=evidence,
        )

    def assess_entry(self, intent: TradeIntent) -> PoolCheckDecision:
        evidence = self.evidence.get(intent.market_event_id)
        chain = str(intent.chain or "").lower()
        if chain == "solana":
            mint = str(intent.asset_out or evidence.get("mint") or "").strip()
            if not mint:
                return self._decision("HARD_BLOCK", "missing Solana mint", evidence=evidence)
            return self._solana(mint, evidence)
        return self._evm(evidence)

    def assess_open_position(self, *, chain: str, asset: str) -> PoolCheckDecision:
        if str(chain).lower() == "solana":
            return self._solana(str(asset), {"mint": str(asset), "open_position_recheck": True})
        return self._decision(
            "SHADOW_ONLY",
            "open EVM position remains paper-only; LIVE external/stress revalidation not wired",
            evidence={"chain": str(chain), "asset": str(asset), "live_eligible": False},
        )
