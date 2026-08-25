DEEPSEEK_TO_GPT
in_reply_to: 2026-08-25T22-06-poolcheck-audit
status: COMPLETED
provider_return_code: 0

DEEPSEEK_POOLCHECK_AUDIT

Material uncertainty: this mailbox provides only the report message and SHA `f51b9f915baeea5901c03d4f0dbea0bed56d4a9a`; it does not include repository file contents, and I cannot retrieve or inspect the repo. Exact file paths/line numbers below must be confirmed by the trusted GitHub workflow. This audit is based on the supplied symptom, the named functions, and typical PoolCheck/RugCheck semantics.

## 1. Is `Large Amount of LP Unlocked` being classified correctly?

Likely not. If `evaluate_rugcheck` is promoting any RugCheck risk whose name/description contains `Large Amount of LP Unlocked` into the same `HARD_BLOCK` path as structural token dangers, that is over-promotion.

RugCheck risk data normally has separate fields: risk name, description, risk level/score, and sometimes category. `Large Amount of LP Unlocked` is primarily a liquidity/exit-risk signal, not proof of mint/freeze/honeypot/blacklist/malicious-transfer controls. Classifying it as `HARD_BLOCK` only on substring match or only because it is a RugCheck “danger” risk is too blunt.

Correct classification should be:
- structural token-control risks -> `HARD_BLOCK`
- LP concentration/unlocked-liquidity risks -> `SHADOW_ONLY` / `COOLING`, not `HARD_BLOCK`, unless additional thresholds or combined structural risk justify blocking.

## 2. Can `evaluate_rugcheck`, `external_pool_check`, hard-block caching, and `MandatoryShadowPoolCheck` create repeated/over-broad blocks?

Yes.

Likely failure chain:

1. `evaluate_rugcheck()` string-matches risk names and treats `Large Amount of LP Unlocked` as severe.
2. `external_pool_check()` then converts that into a hard-block reason.
3. The hard-block cache stores the result by token/pair for 15 minutes.
4. SiBot1 `MandatoryShadowPoolCheck` sees that cached hard block and treats the candidate as blocked, even though the underlying issue is only a liquidity/cooling risk.

This repeats the same block for the cache TTL and can broaden blocks beyond what the provider evidence supports.

Recommended semantics:
- `evaluate_rugcheck()` should return a risk class, not just a boolean block.
- Only `HARD_BLOCK` should be hard-fail.
- `SHADOW_ONLY`/`COOLING` should not be stored in the hard-block cache as `HARD_BLOCK`.
- `MandatoryShadowPoolCheck` should distinguish:
  - `HARD_BLOCK`: candidate is rejected.
  - `SHADOW_ONLY`/`COOLING`: candidate may enter shadow/simulation only, with reduced size and extra exit checks.
  - `ALLOW`: candidate passes.

## 3. Structural token danger vs liquidity/unlocked-LP risk

### Structural token danger -> HARD_BLOCK

These should remain fail-closed and continue to block LIVE/shadow entry:
- mint authority still enabled or not revoked/locked
- freeze authority still enabled
- honeypot behaviour / non-standard transfer revert
- blacklist/whitelist transfer controls
- malicious transfer hooks or non-standard transfer fees
- unsafe proxy upgrade authority
- metadata mutability combined with dangerous upgrade authority

These are directly token-control risks.

### Liquidity concentration / unlocked LP -> SHADOW_ONLY / COOLING, not automatic HARD_BLOCK

Examples:
- `Large Amount of LP Unlocked`
- high top-10 holder concentration
- low liquidity/depth
- creator holds large percentage of supply

These are exit-quality risks. They should cool the candidate, not necessarily hard-block it.

Recommended rule without reducing LIVE safety:
- LIVE trading: if LP unlocked is above a material threshold, e.g. ≥70–80% of LP is unlocked, or large LP unlock is combined with high holder concentration and low pool age/liquidity, keep a `HARD_BLOCK` for LIVE.
- Shadow/simulation: `Large Amount of LP Unlocked` alone should not be a hard block; it should be `SHADOW_ONLY`/`COOLING`, require smaller size, and require the reverse-exit stress check.
- If any structural token risk is also present, it remains `HARD_BLOCK`.

This preserves LIVE fail-closed behaviour while preventing over-blocking shadow-only SiBot1 candidates.

## 4. 15-minute SiBot1 hard-block cache

The hard-block cache should only cache durable, structural/token-control reason codes.

Cacheable for up to 15 minutes:
- `FREEZE_AUTHORITY_ENABLED`
- `MINT_AUTHORITY_ENABLED`
- `HONEYPOT`
- `BLACKLIST_TRANSFER_CONTROL`
- `MALICIOUS_TRANSFER_HOOK`
- `NONSTANDARD_TRANSFER_FEE`
- `UNSAFE_PROXY_UPGRADE`

Not cacheable as a 15-minute hard block:
- `LP_UNLOCKED_HIGH`
- `LOW_LIQUIDITY`
- `HIGH_TOP_HOLDER_CONCENTRATION`
- `DEXSCREENER_LIQUIDITY_RISK`
- any `SHADOW_ONLY`/`COOLING` decision

These liquidity metrics can change rapidly: LP may be locked/burned, buybacks may occur, or a new reference quote may appear.

Provider evidence changes should invalidate cache:
- Key the cache entry by `token` + `quote` + `evidence_fingerprint`.
- `evidence_fingerprint` should be a hash of the relevant RugCheck/DexScreener fields: risk name, risk level/score, description, LP-unlock percentage, holder concentration, liquidity, timestamp, and relevant quotes.
- If the fingerprint changes, recompute instead of serving the cached hard block.
- For liquidity-based risks, use a short TTL, e.g. 3–5 minutes, not 15 minutes.

## 5. LIVE-only safeguards and 3x reverse-exit stress check

Existing safeguards are reasonable:
- RugCheck
- DexScreener
- reference reverse-depth quote
- signed simulation

They create a fail-closed stack, especially for LIVE.

Recommended addition for the separately controlled SiBot1 Solana bridge:

Add a `reverse_exit_stress_check()` gate before allow.

It should:
1. Take the intended entry notional `N`.
2. Use a signed simulation to sell `3 * N` of the candidate token back to the quote token.
3. Compare simulated output against the reference reverse-depth quote.
4. Fail closed if:
   - simulation reverts,
   - output is less than `1 - max_slippage_3x` of the reference quote, where `max_slippage_3x` is configurable and stricter than normal exit slippage,
   - impact exceeds pre-configured 3x stress impact limit,
   - or any custom program error occurs.
5. Permit only if the 3x stress exit passes.

This should run after RugCheck/DexScreener/reference quote checks but before trade allow, and it must not bypass PoolCheck. If the candidate has only liquidity risks, it may be `SHADOW_ONLY` until the stress check passes.

## 6. Exact file/function changes and regression tests if a bug exists

Exact paths cannot be confirmed from this mailbox. Based on the function names supplied, the expected changes are:

### In `evaluate_rugcheck`

Replace name-based severe blocking with risk classes.

Pseudo-rules:
```python
STRUCTURAL_HARD_BLOCK_RISKS = {
    "Freeze Authority Enabled",
    "Mint Authority Enabled",
    "Honeypot",
    "Blacklist Transfer Control",
    "Malicious Transfer Hook",
    "Non-Standard Transfer Fee",
    "Unsafe Proxy Upgrade",
}

LIQUIDITY_COOLING_RISKS = {
    "Large Amount of LP Unlocked",
    "Low Liquidity",
    "High Top Holder Concentration",
}

if risk_name in STRUCTURAL_HARD_BLOCK_RISKS:
    return RiskClass.HARD_BLOCK, risk_name
if risk_name in LIQUIDITY_COOLING_RISKS:
    return RiskClass.SHADOW_ONLY, "LP_UNLOCKED_HIGH"  # or reason code from provider
```

### In `external_pool_check`

Combine risk classes:
- any structural risk -> `HARD_BLOCK`
- otherwise, if only liquidity/cooling risks -> `SHADOW_ONLY`/`COOLING`
- only allow LIVE if no hard block and reverse-exit stress passes

### In hard-block cache

- Store `risk_class`, `reason_code`, `evidence_fingerprint`, `expires_at`.
- Only cache `HARD_BLOCK` for structural reason codes with 15-minute TTL.
- Short TTL for liquidity-based entries or do not cache them as hard blocks.
- Invalidate on fingerprint mismatch.

### In SiBot1 `MandatoryShadowPoolCheck`

- Treat `SHADOW_ONLY`/`COOLING` as shadow-eligible.
- Do not convert `SHADOW_ONLY` to `HARD_BLOCK`.
- Only `HARD_BLOCK` rejects the candidate outright.

### Regression tests

1. RugCheck fixture containing only `Large Amount of LP Unlocked`:
   - Expected: `SHADOW_ONLY`/`COOLING`, not `HARD_BLOCK`.

2. RugCheck fixture containing `Freeze Authority Enabled` or `Mint Authority Enabled`:
   - Expected: `HARD_BLOCK`.

3. RugCheck fixture containing `Large Amount of LP Unlocked` plus `Freeze Authority Enabled`:
   - Expected: `HARD_BLOCK`.

4. Cache invalidation test:
   - Cache a structural hard block with evidence fingerprint `A`.
   - Serve again with same fingerprint `A`: cached.
   - Serve after evidence changes to fingerprint `B`: recompute, do not serve stale block.

5. SiBot1 shadow test:
   - Candidate with only LP unlock should enter shadow pipeline, not be dropped by `MandatoryShadowPoolCheck`.

6. Reverse-exit stress test:
   - Simulate 3x sell.
   - Pass when output is within configured max slippage.
   - Fail closed when output below threshold or simulation reverts.

If current code intentionally hard-blocks `Large Amount of LP Unlocked` for LIVE only, that could be defensible. But if it hard-blocks the same risk for shadow-only SiBot1 candidates and caches that hard block for 15 minutes, then the behaviour is over-broad and likely buggy.

## 7. No bypass recommendation

Do not bypass PoolCheck to force a trade. The fix is not to allow LP-unlocked candidates into LIVE without protection. It is to classify the risk correctly:
- keep structural token dangers as `HARD_BLOCK`;
- treat LP concentration/unlocked-liquidity as `SHADOW_ONLY`/`COOLING`;
- require a 3x reverse-exit stress check before any LIVE promotion;
- keep provider evidence fingerprints on cached decisions.

This preserves fail-closed LIVE semantics and anti-rug protections while reducing over-blocking of shadow-only candidates.
