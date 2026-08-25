from __future__ import annotations

import hashlib
import json
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
_STRUCTURAL_HARD_BLOCK_CODES = {
    "FREEZE_AUTHORITY_ENABLED",
    "MINT_AUTHORITY_ENABLED",
    "HONEYPOT",
    "BLACKLIST_TRANSFER_CONTROL",
    "MALICIOUS_TRANSFER_HOOK",
    "NONSTANDARD_TRANSFER_FEE",
    "NON_STANDARD_TRANSFER_FEE",
    "UNSAFE_PROXY_UPGRADE",
}
_STRUCTURAL_DANGER_TERMS = (
    "freeze authority",
    "mint authority",
    "permanent delegate",
    "honeypot",
    "rugged",
    "blacklist",
    "non-transferable",
    "default account state",
    "transfer hook",
    "malicious transfer",
    "non-standard transfer fee",
    "nonstandard transfer fee",
    "unsafe proxy",
    "upgrade authority",
)
_LIQUIDITY_ONLY_TERMS = (
    "lp unlocked",
    "liquidity unlocked",
    "unlocked lp",
    "large amount of lp unlocked",
    "low amount of lp providers",
    "low amount of liquidity providers",
    "lp provider concentration",
    "liquidity provider concentration",
    "top holder concentration",
    "holder concentration",
    "creator holds",
    "creator ownership",
    "low liquidity",
)


class MandatoryShadowPoolCheck:
    """Central PoolCheck gate for the SiBot 1 SHADOW runtime.

    The bridge deliberately never upgrades partial evidence to LIVE PASS. A
    candidate may be studied in SHADOW when upstream evidence is incomplete, but
    LIVE sellability remains unproven until the hardened chain-specific full
    reverse/simulation/stress path is explicitly wired to a real executor.

    Only durable structural Solana HARD_BLOCK decisions are retained in the
    local 15-minute duplicate cache. Dynamic liquidity/cooling findings and
    provider outages remain SHADOW-only and are reassessed. The lower provider
    layer already has its own response cache, so checking its evidence fingerprint
    here does not require repeated paid model calls and lets changed evidence
    invalidate a stale local structural block.
    """

    def __init__(self, csv_dir: str | Path, evidence: MarketEvidenceBook):
        self.csv_dir = Path(csv_dir)
        self.evidence = evidence
        self._settings: dict[str, str] | None = None
        self._hard_blocks: dict[str, tuple[float, str, PoolCheckDecision]] = {}

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

    @staticmethod
    def _risk_has_any(value: str, terms: tuple[str, ...]) -> bool:
        text = str(value or "").lower()
        return any(term in text for term in terms)

    @classmethod
    def _liquidity_only_provider_hard_block(cls, reason_code: str, evidence: dict[str, Any]) -> bool:
        if str(reason_code or "").upper() != "TOKEN_SECURITY_SEVERE":
            return False
        blocking = str(evidence.get("rugcheck_blocking_risk") or "").strip()
        if blocking:
            if cls._risk_has_any(blocking, _STRUCTURAL_DANGER_TERMS):
                return False
            return cls._risk_has_any(blocking, _LIQUIDITY_ONLY_TERMS)

        names = [str(x or "").strip() for x in (evidence.get("rugcheck_risks") or []) if str(x or "").strip()]
        if not names:
            return False
        if any(cls._risk_has_any(name, _STRUCTURAL_DANGER_TERMS) for name in names):
            return False
        return all(cls._risk_has_any(name, _LIQUIDITY_ONLY_TERMS) for name in names)

    @classmethod
    def _cacheable_structural_hard_block(cls, reason_code: str, evidence: dict[str, Any]) -> bool:
        code = str(reason_code or "").upper()
        if code in _STRUCTURAL_HARD_BLOCK_CODES:
            return True
        if code != "TOKEN_SECURITY_SEVERE":
            return False
        blocking = str(evidence.get("rugcheck_blocking_risk") or "")
        return bool(blocking and cls._risk_has_any(blocking, _STRUCTURAL_DANGER_TERMS))

    @staticmethod
    def _evidence_fingerprint(verdict: str, reason_code: str, reason: str, evidence: dict[str, Any]) -> str:
        relevant = {
            key: evidence.get(key)
            for key in sorted(evidence)
            if key.startswith("rugcheck_")
            or key.startswith("dex_")
            or key in {
                "mint_authority",
                "freeze_authority",
                "transfer_hook",
                "holder_concentration_pct",
                "lp_unlocked_pct",
                "liquidity_usd",
            }
        }
        payload = {
            "verdict": str(verdict or "").upper(),
            "reason_code": str(reason_code or "").upper(),
            "reason": str(reason or ""),
            "evidence": relevant,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _cached_hard_block(self, mint: str, fingerprint: str) -> PoolCheckDecision | None:
        key = str(mint or "").strip()
        if not key:
            return None
        item = self._hard_blocks.get(key)
        if not item:
            return None
        expiry, cached_fingerprint, original = item
        if cached_fingerprint != str(fingerprint or ""):
            self._hard_blocks.pop(key, None)
            return None
        remaining = expiry - time.monotonic()
        if remaining <= 0:
            self._hard_blocks.pop(key, None)
            return None
        evidence = dict(original.evidence)
        evidence.update({
            "poolcheck_hard_block_cache_hit": True,
            "poolcheck_hard_block_cache_ttl_seconds": _HARD_BLOCK_TTL_SECONDS,
            "poolcheck_hard_block_cache_remaining_seconds": int(remaining),
            "poolcheck_evidence_fingerprint": cached_fingerprint[:16],
        })
        reasons = tuple(original.reasons) + (
            f"duplicate structural HARD_BLOCK matched unchanged evidence for {int(remaining)}s",
        )
        return self._decision("HARD_BLOCK", *reasons, evidence=evidence)

    def _remember_hard_block(
        self,
        mint: str,
        decision: PoolCheckDecision,
        reason_code: str,
        fingerprint: str,
    ) -> None:
        if decision.verdict.upper() != "HARD_BLOCK":
            return
        if not self._cacheable_structural_hard_block(reason_code, dict(decision.evidence)):
            return
        key = str(mint or "").strip()
        if not key:
            return
        self._hard_blocks[key] = (
            time.monotonic() + _HARD_BLOCK_TTL_SECONDS,
            str(fingerprint or ""),
            decision,
        )
        if len(self._hard_blocks) > 2048:
            now = time.monotonic()
            self._hard_blocks = {k: v for k, v in self._hard_blocks.items() if v[0] > now}

    def _solana(self, mint: str, base_evidence: dict[str, Any]) -> PoolCheckDecision:
        try:
            from learnerbot.solana_pool_risk_gate import external_pool_check
            result = external_pool_check(mint, self._pool_settings())
        except Exception as exc:
            return self._decision(
                "SHADOW_ONLY",
                f"POOLCHECK_PROVIDER_ERROR:{type(exc).__name__}; SHADOW remains available but LIVE must fail closed",
                evidence={
                    **base_evidence,
                    "external_poolcheck_available": False,
                    "source_poolcheck_verdict": "HARD_BLOCK",
                    "live_eligible": False,
                },
            )

        verdict = str((result or {}).get("decision") or "HARD_BLOCK").upper()
        reason_code = str((result or {}).get("reason_code") or "POOLCHECK")
        reason = str((result or {}).get("reason") or reason_code)
        provider_evidence = dict((result or {}).get("evidence") or {})
        merged = {
            **base_evidence,
            **provider_evidence,
            "poolcheck_reason_code": reason_code,
            "source_poolcheck_verdict": verdict,
            "live_eligible": False,
        }

        if verdict == "HARD_BLOCK" and str(reason_code).upper() in _TRANSIENT_HARD_BLOCK_CODES:
            return self._decision(
                "SHADOW_ONLY",
                f"{reason}; provider uncertainty is SHADOW-only and LIVE remains fail-closed",
                evidence={**merged, "external_poolcheck_available": False},
            )

        if verdict == "HARD_BLOCK" and self._liquidity_only_provider_hard_block(reason_code, provider_evidence):
            return self._decision(
                "SHADOW_ONLY",
                f"liquidity/ownership risk remains SHADOW-only: {reason}",
                evidence={
                    **merged,
                    "poolcheck_reclassified_from": "HARD_BLOCK",
                    "poolcheck_reclassification": "LIQUIDITY_ONLY_TO_SHADOW",
                },
            )

        if verdict == "COOLING":
            return self._decision(
                "SHADOW_ONLY",
                f"COOLING:{reason}",
                evidence={**merged, "shadow_cooling": True},
            )

        if verdict == "SHADOW_ONLY":
            return self._decision("SHADOW_ONLY", reason, evidence=merged)

        if verdict == "HARD_BLOCK":
            decision = self._decision("HARD_BLOCK", reason, evidence=merged)
            fingerprint = self._evidence_fingerprint(verdict, reason_code, reason, provider_evidence)
            cached = self._cached_hard_block(mint, fingerprint)
            if cached is not None:
                return self._decision(
                    cached.verdict,
                    *cached.reasons,
                    evidence={**base_evidence, **dict(cached.evidence)},
                )
            self._remember_hard_block(mint, decision, reason_code, fingerprint)
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
