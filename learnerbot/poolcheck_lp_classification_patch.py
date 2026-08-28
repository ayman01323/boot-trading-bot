from __future__ import annotations

"""Conservative RugCheck classification/cache correction for SiBot/Solana.

RugCheck can label liquidity observations such as "Large Amount of LP Unlocked"
with a severe provider level. That is an important LIVE exit/liquidity risk, but
it is not equivalent to structural token controls such as mint/freeze authority,
honeypot or blacklist behaviour.

This patch changes only that narrow class from HARD_BLOCK to SHADOW_ONLY and caps
raw RugCheck evidence freshness at five minutes so dynamic LP evidence is not held
for the old fifteen-minute window. LIVE remains fail-closed because both the
legacy Solana LIVE gate and the protected SiBot 1 Solana bridge reject every
non-PASS external PoolCheck decision.
"""

from . import solana_pool_risk_gate as _pool

_ORIGINAL_EVALUATE_RUGCHECK = _pool.evaluate_rugcheck
_ORIGINAL_FETCH_JSON = _pool._fetch_json
_MAX_RUGCHECK_CACHE_SECONDS = 5 * 60

_LIQUIDITY_ONLY_TERMS = (
    "lp unlocked",
    "liquidity unlocked",
    "unlocked lp",
    "large amount of lp unlocked",
    "low amount of lp providers",
    "low amount of liquidity providers",
    "lp provider concentration",
    "liquidity provider concentration",
)

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
)


def _risk_text(risk: dict) -> str:
    return " ".join(str(risk.get(k) or "") for k in ("name", "description", "value")).lower()


def _is_liquidity_only(text: str) -> bool:
    value = str(text or "").lower()
    return any(term in value for term in _LIQUIDITY_ONLY_TERMS)


def _has_structural_danger(text: str) -> bool:
    value = str(text or "").lower()
    return any(term in value for term in _STRUCTURAL_DANGER_TERMS)


def _fetch_json(provider: str, url: str, mint: str, ttl: float, timeout: float):
    """Cap RugCheck response freshness without loosening any decision threshold."""
    effective_ttl = float(ttl)
    if str(provider or "").lower() == "rugcheck":
        effective_ttl = min(effective_ttl, float(_MAX_RUGCHECK_CACHE_SECONDS))
    return _ORIGINAL_FETCH_JSON(provider, url, mint, effective_ttl, timeout)


def evaluate_rugcheck(summary: dict, cfg: dict) -> dict:
    """Preserve every original hard block except known liquidity-only labels."""
    result = _ORIGINAL_EVALUATE_RUGCHECK(summary, cfg)
    if str((result or {}).get("decision") or "").upper() != "HARD_BLOCK":
        return result
    if str((result or {}).get("reason_code") or "").upper() != "TOKEN_SECURITY_SEVERE":
        return result

    evidence = dict((result or {}).get("evidence") or {})
    blocking_name = str(evidence.get("rugcheck_blocking_risk") or "").strip()
    # Aggregate score hard blocks do not carry rugcheck_blocking_risk and remain
    # untouched. Unknown severe provider risks also remain fail-closed.
    if not blocking_name:
        return result

    matched = None
    for risk in (summary or {}).get("risks") or []:
        if not isinstance(risk, dict):
            continue
        if str(risk.get("name") or "").strip() == blocking_name:
            matched = risk
            break
    text = _risk_text(matched or {"name": blocking_name})
    if not _is_liquidity_only(text) or _has_structural_danger(text):
        return result

    evidence.update({
        "rugcheck_reclassified_from": "HARD_BLOCK",
        "rugcheck_reclassification": "LIQUIDITY_ONLY_TO_SHADOW",
        "rugcheck_liquidity_risk": blocking_name,
        "live_eligible": False,
    })
    return _pool._decision(
        "SHADOW_ONLY",
        "LP_CONCENTRATION_RISK",
        f"RugCheck liquidity risk requires SHADOW/LIVE revalidation: {blocking_name}",
        evidence,
    )


def install() -> None:
    if getattr(_pool, "_lp_liquidity_classification_patch_installed", False):
        return
    _pool.evaluate_rugcheck = evaluate_rugcheck
    _pool._fetch_json = _fetch_json
    _pool._lp_liquidity_classification_patch_installed = True
    print(
        "[poolcheck-lp-classification] installed=true "
        "lp-unlocked=SHADOW_ONLY structural-danger=HARD_BLOCK live-nonpass=BLOCKED "
        "rugcheck-cache-max=300s"
    )


install()

# Root-owned runtime diagnostics may read the protected Solana LIVE attempt database
# and publish only aggregate status/failure classes through the existing redacted
# snapshot. This grants no new runner/file permissions and changes no trade path.
from . import sibot1_solana_attempt_diag_patch as _sibot1_solana_attempt_diag  # noqa: E402,F401
