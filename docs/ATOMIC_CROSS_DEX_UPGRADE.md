# Atomic Cross-DEX + Adaptive Sizing Upgrade

This upgrade adds a **separate fail-closed execution path** for V2 cross-DEX opportunities already detected by the full-power scanner. It does not convert the existing shadow row into sequential EOA swaps.

## What changes

- `contracts/AtomicV2ArbExecutor.sol`
  - authorised caller whitelist in addition to router whitelist;
  - capital is pulled from and returned to the same caller;
  - stale deadline and final minimum-profit checks revert atomically;
  - owner rescue function for accidentally sent tokens.

- `learnerbot/cross_dex_executor.py`
  - consumes existing `CROSS_DEX_V2` scanner rows;
  - requires `cross_dex_live_enabled=true`;
  - requires a deployed `cross_dex_atomic_executor_address`;
  - requires caller and both routers to be whitelisted in the contract;
  - tests bounded sizes and chooses the highest expected net result among those tested;
  - estimates conservative gas and adds it to the on-chain minimum-profit floor;
  - repeats final `eth_call` before signing;
  - on BSC, private submission is required by default and can fan the same signed transaction to BlockRazor and Puissant;
  - no automatic public-mempool fallback unless `private_submission_required=false` is set explicitly.

- `learnerbot/fast_market.py`
  - retains the existing same-router V2/V3 execution path;
  - invokes the dedicated atomic cross-DEX executor independently.

- `scripts/prepare_cross_dex.py`
  - wraps capital if needed and grants an exact, bounded WBNB/WETH allowance to the configured executor.

## Required settings

Keep the master live switch global and configure the executor per chain in `CSVbot/auto_trading_settings.csv`:

```csv
chain_id,setting,value,description
*,cross_dex_live_enabled,false,Master gate - enable only after deployment and tests
56,cross_dex_atomic_executor_address,0x...,Deployed AtomicV2ArbExecutor
*,adaptive_size_multipliers,"0.5,1,2,4",Bounded candidate sizes relative to auto_input_base
*,cross_dex_gas_units_estimate,350000,Rough selection-time gas only; final estimate is mandatory
56,private_submission_required,true,Fail closed if no private builder accepts
56,private_bundle_max_blocks,3,Puissant bundle validity window in blocks
```

Private endpoint secrets should be environment variables where possible:

```bash
export BLOCKRAZOR_BSC_RPC_URL='https://...'
export BLOCKRAZOR_AUTH_TOKEN='...'
export PUISSANT_BSC_RPC_URL='https://puissant-builder.48.club/'
```

Do **not** store private keys or sensitive builder credentials in general CSV files.

## Deployment sequence

1. Compile and independently review/audit `AtomicV2ArbExecutor.sol`.
2. Deploy one executor per chain using that chain's wrapped native token.
3. `setRouter(router, true)` for each V2 router permitted for cross-DEX execution.
4. `setCaller(wallet, true)` for each wallet permitted to execute.
5. Set `cross_dex_atomic_executor_address` for the chain.
6. Run `python3 scripts/prepare_cross_dex.py --telegram-id <id> --chain bsc --amount 0.01`.
7. Leave `cross_dex_live_enabled=false` and run scanner/shadow observation first.
8. Verify builder connectivity and private submission.
9. Enable a tiny canary amount only after the above passes.
10. Set `cross_dex_live_enabled=true`.

## Important

This increases the executable opportunity universe; it does not guarantee profit. The executor deliberately reverts when its final wrapped-base balance is below capital plus the configured minimum profit. Gas can still be spent on a reverted transaction if a transaction is included and then reverts, which is why the local simulation and immediate `eth_call` remain mandatory.
