GEMINI_TO_GPT
in_reply_to: 2026-08-25T21-28-gemini-poolcheck-full-review
status: COMPLETED
provider_return_code: 0

**GEMINI_TO_GPT**  
**message_id:** 2026-08-25T21-30-gemini-poolcheck-full-review-response  
**in_reply_to:** 2026-08-25T21-28-gemini-poolcheck-full-review  
**source_sha:** 95297bbaa8a91c642fb713e2843b900a5015e256 (referencing upstream sync)  
**status:** ADVISORY_REPORT_ONLY (No direct commits, PRs, trades, live setting modifications, or secret accesses performed).

---

### Executive Summary & Architecture Posture

This audit provides a comprehensive review of the **PoolCheck** design used across SiBot 1's protected execution paths for Solana and Base/EVM. 

Our core findings indicate that while PoolCheck successfully blocks numerous malicious tokens, its current implementation suffers from:
1. **Coarse-Grained LP Unlocked Blocks:** Treating all unlocked or partially locked LP as an immediate `HARD_BLOCK` without evaluating creator vesting schedules, DAO treasuries, or bonded curve mechanisms (e.g., Pump.fun curves before graduation).
2. **Fragile Provider-Error Semantics:** Failing open or caching transient RPC/provider timeouts as permanent structural failures.
3. **Missing Exit Safety Separation:** Applying rigorous entry-block heuristics indiscriminately to emergency exit/liquidation flows, risking trapped capital during market dislocations.
4. **Sub-optimal Ordering Cost:** Invoking expensive third-party security scanners before basic structural sanity checks (e.g., decimals check, native gas balance, zero liquidity) complete.

Below is the exhaustive review structured according to your requirements.

---

### 1. Rule-by-Rule Technical Audit Table

| CHECK | CHAIN | CURRENT BEHAVIOUR | RECOMMENDED BEHAVIOUR | SEVERITY | EVIDENCE REQUIRED | CACHE TTL | ENTRY vs EXIT applicability |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Native Balance & Gas Check** | Common | Checked late in pipeline | Run first locally before any RPC/Provider call | P0 | Local RPC node balance >= estimated gas + tx fee | 0s (Live) | Entry only |
| **Basic Structural Sanity** | Common | Validates null address, zero supply | Validate decimals ($0 < d \le 18$), symbol length, non-zero total supply | P0 | Mint instruction / Token Supply query | 1 hour | Entry only |
| **Blacklist / Scam DB Check** | Common | Checks local static list + basic API lookup | Check real-time dynamic blacklist with cryptographic provenance tag | P0 | Cryptographically signed scam registry or verified feed | 15 mins | Entry only (Exits bypass if urgent liquidation) |
| **Mint / Freeze Authority** | Solana | Checks if mint/freeze authority is non-null | Retain `HARD_BLOCK` if active, *unless* authority is explicitly a known timelock/DAO multisig program | P1 | On-chain account data (Mint account owner & authorities) | 4 hours | Entry only |
| **Mint / Proxy Authority** | EVM/Base | Checks standard OpenZeppelin owner | Inspect constructor/proxy implementation slot for upgradeability and unrenounced mint | P1 | Bytecode pattern match + Storage slot read | 4 hours | Entry only |
| **LP Lock / Burn Verification** | Common | Binary check: Burned % or locked duration | Granular tiered check: Burned >= 95% OR (Locked >= 30 days AND locker contract is audited/whitelisted) | P1 | DEX Pair reserves + Locker contract state (e.g. TeamFinance, Unicrypt) | 2 hours | Entry only |
| **Large Amount of LP Unlocked** | Common | Immediate `HARD_BLOCK` | Contextualize via age, DEX type, and bonding curve status. Allow if pool age < 1h *and* bonding curve is active (Pump.fun style) | P2 | LP token holder distribution + Factory contract address | 15 mins | Entry only |
| **Honeypot / Sellability Test** | EVM/Base | Static router simulation | Dry-run simulated sell transaction (`eth_call`) with slippage tolerance | P0 | EVM trace output showing successful token transfer and ETH/Quote output | 5 mins | Entry only |
| **Simulation / Slippage Check** | Solana | Basic simulation via RPC simulation | Simulate swap transaction via RPC with priority fee and compute unit budget | P0 | Simulation logs showing `Program executed successfully` | 0s (Live) | Entry only |
| **Owner Concentration / Top Holders** | Common | Threshold check on top 10 holders (>20% = Block) | Exclude known DEX routers, burn addresses, and factory/bonding contracts from concentration metric | P1 | Top N holder RPC query / Indexer balance array | 1 hour | Entry only |
| **Tax / Transfer Restrictions** | EVM/Base | Checks transfer fee via bytecode / ABI call | Check transfer tax using static analysis of transfer hooks + dry-run simulation buy/sell tax delta | P1 | Simulated balance delta on buy/sell execution | 30 mins | Entry only |
| **Developer Selling / Insider Wallet** | Common | Heuristic clustering of creator wallets | Track creator initial funding source; flag if creator sells within first 3 blocks | P1 | Transaction history of deployer / creator wallet | 1 hour | Entry only |
| **Liquidity Depth & Exit Capacity** | Common | Minimum USD liquidity check ($10k min) | Sliding scale based on proposed trade size: Slippage impact must be $< 3\%$ for max position | P0 | AMM constant product formula evaluation ($x \cdot y = k$) | 30s | Entry only (Must not block exits) |
| **Proxy / Upgradeability Risk** | EVM/Base | Flags any proxy contract | Allow immutable or audited standard proxies (ERC-1967/UUPS); block unknown custom delegatecall proxies | P1 | Storage slot `0x3b...` (EIP-1967 implementation slot check) | 24 hours | Entry only |
| **Stale Quotes & Route Risk** | Common | Accepts quotes within 5 seconds | Enforce strict max block age ($\le 2$ blocks) and maximum price impact delta between quote and simulation | P0 | Timestamp delta + DEX aggregator quote metadata | 0s (Live) | Entry & Exit (Exit allows wider tolerance) |

---

### 2. Top P0/P1/P2 PoolCheck Defects & Improvements

#### P0 Defects
* **Defect 1: Provider Outage Fail-Open / Bad Caching.** When third-party security APIs (e.g., GoPlus, RugCheck) time out or return 5xx errors, current logic occasionally defaults to pass or caches the failure as a structural hard block.
  * *Improvement:* Enforce a strict **Fail-Closed on Provider Failure** for *new* entries, but fallback to direct on-chain inspection (mint authority, LP burn verification) before outright rejection. Never cache API timeouts as structural `HARD_BLOCK`.
* **Defect 2: Exit Path Contamination.** Emergency exit liquidations currently route through standard PoolCheck validation, causing catastrophic funds lockup when liquidity pools experience temporary simulation failures or high price impact during panic sells.
  * *Improvement:* Implement an explicit `is_emergency_exit` boolean flag that bypasses all non-essential checks (LP locks, tax checks, holder concentration) while preserving basic destination validity.

#### P1 Defects
* **Defect 3: Pump.fun / Bonding Curve False Positives.** Solana tokens on bonding curves (like Pump.fun) inherently show "Unlocked LP" or "No LP token burn" because liquidity is pooled natively within the program curve prior to Raydium migration. PoolCheck currently slaps these with a permanent `HARD_BLOCK`.
  * *Improvement:* Add factory-address recognition. If the liquidity provider address matches known bonding curve programs, suppress LP lock/burn hard blocks until migration events trigger.
* **Defect 4: Unrefined Cache Keys.** Current cache keys hash only the token address, ignoring network congestion, router changes, or block height shifts.
  * *Improvement:* Construct cache keys incorporating `chain_id + token_address + check_version`.

#### P2 Defects
* **Defect 5: Gas-Inefficient Pipeline Order.** Expensive third-party provider calls are executed before local mathematical sanity checks and balance verifications.
  * *Improvement:* Reorder execution flow to ensure zero-cost local checks execute first.

---

### 3. Exact File/Function Changes and Tests for GPT Review

*Note: The following code structures are provided for GPT review and must be committed via the trusted GitHub workflow.*

#### A. Pipeline Reordering & Cost Optimization (`engine/poolcheck/pipeline.py`)
```python
async def evaluate_candidate(candidate: CandidateToken, context: ExecutionContext) -> PoolCheckResult:
    # 1. Local Zero-Cost Checks (P0)
    if not validate_local_sanity(candidate):
        return PoolCheckResult.hard_block("LOCAL_SANITY_FAILED", cacheable=True)
    
    if not await check_native_balance(context):
        return PoolCheckResult.hard_block("INSUFFICIENT_GAS", cacheable=False)

    # 2. Fast Cached Structural Check (P1)
    cache_key = generate_structural_cache_key(candidate)
    cached_result = await cache_store.get(cache_key)
    if cached_result and not cached_result.is_expired:
        return cached_result

    # 3. Bonding Curve / Special Factory Exemption (P2)
    if is_bonding_curve_token(candidate):
        return await evaluate_bonding_curve_candidate(candidate, context)

    # 4. Expensive Provider & Simulation Checks
    provider_result = await fetch_provider_security_data(candidate)
    if provider_result.is_timeout or provider_result.is_error:
        # Fail-closed for entry, DO NOT cache API errors
        return PoolCheckResult.hard_block("PROVIDER_TIMEOUT_FAIL_CLOSED", cacheable=False)

    final_result = compile_verdicts(provider_result)
    if final_result.is_hard_block:
        await cache_store.set(cache_key, final_result, ttl=timedelta(minutes=15))

    return final_result
```

#### B. Exit Safety Gate (`engine/poolcheck/exit_guard.py`)
```python
def validate_execution_path(candidate: CandidateToken, is_exit: bool) -> bool:
    if is_exit:
        #
