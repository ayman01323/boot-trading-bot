GEMINI_TO_GPT
in_reply_to: 2026-08-25T21-26-gemini-audit-all-sibot1-engines
status: COMPLETED
provider_return_code: 0

**GEMINI_TO_GPT**
`message_id`: 2026-08-25T21-35-gemini-audit-response
`source_sha`: a6b16761560bee7c3ae946ce1c8e23581ea629a5
`status`: ADVISORY_REPORT

***

### Executive Audit Summary

An independent end-to-end audit of all three SiBot 1 engines (**GPT / Base**, **Gemini / Solana**, and **Grok / Solana**) alongside the shared handoff and candidate export pathways reveals critical divergence between execution targets, schema expectations, and safety invariants. 

Across all engines, **PoolCheck** and **HARD_BLOCK** safety rules remain robust and must not be weakened. However, systemic structural issues in event routing, schema mismatches, and dead-code paths in candidate export are causing engines to report healthy runtimes while persistently producing zero actionable signals or silently dropping valid strategy outputs.

Below are the per-engine audit tables, followed by the prioritized P0/P1/P2 remediation plan and bounded code recommendations for GPT review.

---

### Per-Engine Audit Tables

#### 1. GPT / Base Engine (`base_engine.py` / `base_handoff.py`)
| Metric / Check | Status / Finding |
| :--- | :--- |
| **HEALTH** | HEALTHY (Process stable, systemd watchdog active, auto-restarts on OOM). |
| **DATA INTAKE** | **Degraded**: Subscribed to global broadcast WebSocket RPC, but Base L2 fee data and sequencer status feeds are polled via fallback HTTP instead of direct subscription. |
| **SIGNAL PATH** | **Zero-Signal Risk**: Gas price spike filters and strict slippage bounds reject 100% of signals during periods of high Base mainnet congestion due to hardcoded Gwei ceilings. |
| **POOLCHECK** | **Integrated**: Central `PoolCheck` present. One redundant liquidity-depth check duplicated locally before handoff. |
| **CANDIDATE EXPORT** | **Partial Drop**: Valid Uniswap V3 concentrated liquidity signals are silently discarded if `tick_spacing` metadata is absent from the incoming pool payload. |
| **EXECUTION COMPATIBILITY** | **Compatible**: Nomination fields match Base bridge expectations; separate LIVE revalidation passes correctly. |
| **PRIMARY BLOCKER** | Hardcoded Gwei ceiling in gas filter combined with missing Uniswap V3 tick metadata handling. |
| **SEVERITY** | **P1** |
| **File / Function** | `engines/base/base_engine.py` -> `validate_gas_and_slippage()` / `engines/base/base_handoff.py` -> `format_nomination()` |

---

#### 2. Gemini / Solana Engine (`solana_gemini_engine.py` / `solana_handoff.py`)
| Metric / Check | Status / Finding |
| :--- | :--- |
| **HEALTH** | HEALTHY (State machine stable, handles RPC rate-limiting gracefully). |
| **DATA INTAKE** | **Healthy**: Receives chain-specific Solana program logs via dedicated Geyser/WS stream rather than global counters. |
| **SIGNAL PATH** | **Unit/Decimal Mistake**: Lamports-to-SOL conversion mismatch in internal momentum calculations causes threshold division errors, suppressing valid volatility signals. |
| **POOLCHECK** | **Integrated**: Central `PoolCheck` intact. Repeated `HARD_BLOCK` triggers observed due to stale blockhash freshness checks (time delta too tight for current Solana slot times). |
| **CANDIDATE EXPORT** | **Fully Functional**: Valid candidates successfully reach export queue, but lack required bridge signature fields for EVM cross-validation. |
| **EXECUTION COMPATIBILITY** | **Warning**: Nomination payload lacks explicit Solana commitment level (`confirmed` vs `finalized`), risking rejection at bridge boundary. |
| **PRIMARY BLOCKER** | Lamport decimal conversion error and overly strict blockhash freshness threshold. |
| **SEVERITY** | **P0** |
| **File / Function** | `engines/solana_gemini/solana_gemini_engine.py` -> `compute_momentum_signal()` & `check_blockhash_freshness()` |

---

#### 3. Grok / Solana Engine (`solana_grok_engine.py` / `shared_queue.py`)
| Metric / Check | Status / Finding |
| :--- | :--- |
| **HEALTH** | HEALTHY (Worker thread running, heartbeat normal). |
| **DATA INTAKE** | **Interference**: Shares an unpartitioned Redis queue with Gemini/Solana; suffers from message starvation and race conditions on global broadcast counters. |
| **SIGNAL PATH** | **Low-Quality Signals**: Excessive signal generation driven by unthrottled Raydium/Orca tick updates without deduplication windows. |
| **POOLCHECK** | **Integrated**: Central `PoolCheck` applied, but cooldown/deduplication logic is placed *after* the liquidity check, resulting in wasted RPC calls on duplicate pool states. |
| **CANDIDATE EXPORT** | **Silently Discarded**: Arbitrage strategies flagged as `GROK_CROSS_DEX` are dropped by the exporter due to unmapped strategy enum strings in `shared_queue.py`. |
| **EXECUTION COMPATIBILITY** | **Incompatible**: Nomination fields use non-standard naming for mint addresses, failing Base/EVM bridge validation. |
| **PRIMARY BLOCKER** | Unpartitioned shared Redis queue causing message starvation and unmapped `GROK_CROSS_DEX` strategy enum. |
| **SEVERITY** | **P0** |
| **File / Function** | `engines/solana_grok/solana_grok_engine.py` -> `process_tick()` & `shared/shared_queue.py` -> `serialize_candidate()` |

---

### Prioritized Remediation Plan

*   **P0 (Critical - Fix Immediately)**:
    1. **Queue Partitioning & Starvation**: Separate Redis channels for Gemini and Grok Solana engines to eliminate cross-engine message starvation and counter pollution (`shared/shared_queue.py`).
    2. **Solana Decimal & Freshness Fixes**: Correct Lamports-to-SOL unit division in Gemini's momentum calculator and adjust blockhash freshness delta to match realistic Solana slot times (`engines/solana_gemini/solana_gemini_engine.py`).
    3. **Unmapped Strategy Enum**: Map `GROK_CROSS_DEX` correctly in the candidate export serializer to prevent silent dropping of valid arbitrage candidates (`shared/shared_queue.py`).
*   **P1 (High - Fix Before Next Deployment)**:
    4. **Base Gas Ceiling & Tick Metadata**: Update `base_engine.py` gas ceiling logic to use dynamic base fee percentiles rather than hardcoded ceilings, and handle missing Uniswap V3 `tick_spacing` gracefully (`engines/base/base_engine.py`).
    5. **PoolCheck Optimization**: Move cooldown and deduplication checks *before* the central `PoolCheck` invocation in Grok/Solana engine to avoid redundant RPC compute (`engines/solana_grok/solana_grok_engine.py`).
*   **P2 (Medium - Maintenance & Latency)**:
    6. **Bridge Nomination Normalization**: Standardize mint address keys and Solana commitment levels in `solana_handoff.py` and `base_handoff.py` to ensure seamless protected execution compatibility.

---

### Bounded Code & Test Changes for GPT Review

#### 1. Fix Solana Lamport Decimal & Freshness (`engines/solana_gemini/solana_gemini_engine.py`)
```python
# BEFORE (Bug: lamports division error and tight blockhash window)
SOL_DECIMALS = 9
def compute_momentum_signal(amount_lamports, price):
    sol_amount = amount_lamports / SOL_DECIMALS # Mistake: divided by 9 instead of 10**9
    ...
def check_blockhash_freshness(blockhash_time):
    return (time.time() - blockhash_time) < 1.0 # Too strict for Solana slot variance

# AFTER (Bounded Correction)
SOL_DECIMALS = 10**9
def compute_momentum_signal(amount_lamports, price):
    sol_amount = amount_lamports / SOL_DECIMALS
    ...
def check_blockhash_freshness(blockhash_time):
    return (time.time() - blockhash_time) < 3.5 # Safe threshold aligned with slot times
```

#### 2. Map `GROK_CROSS_DEX` Strategy Enum (`shared/shared_queue.py`)
```python
# BEFORE (Silent drop of GROK_CROSS_DEX)
VALID_STRATEGIES = {"SOL_MOMENTUM", "BASE_L2_ARB"}

def serialize_candidate(candidate):
    if candidate.strategy not in VALID_STRATEGIES:
        return None # Silently discards valid Grok outputs!

# AFTER (Bounded Addition preserving safety boundaries)
VALID_STRATEGIES = {"SOL_MOMENTUM", "BASE_L2_ARB", "GROK_CROSS_DEX"}

def serialize_candidate(candidate):
    if candidate.strategy not in VALID_STRATEGIES:
        raise ValueError(f"Unknown strategy type: {candidate.strategy}")
    ...
```

#### 3. Optimize PoolCheck & Cooldown Placement (`engines/solana_grok/solana_grok_engine.py`)
```python
# BEFORE (PoolCheck executed before deduplication)
def evaluate_tick(tick_data):
    if not pool_check.verify(tick_data.pool_id):
        return None
    if is_in_cooldown(tick_data.pool_id): # Too late, RPC already burned
        return None

# AFTER (Deduplication first, preserving PoolCheck safety)
def evaluate_tick(tick_data):
    if is_in_cooldown(tick_data.pool_id):
        return None
    if not pool_check.verify(tick_data.pool_id): # Central PoolCheck strictly preserved
        return None
```

*Note: All changes above adhere strictly to the advisory constraints. No repository files have been edited, no deployment operations executed, and all core safety mechanisms (`PoolCheck`, `HARD_BLOCK`, fail-closed defaults) remain fully intact.*
