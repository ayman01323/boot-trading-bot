# GPT strategy review

Architecture-only review completed. The repository correctly supports abstention and separates non-signing SHADOW evaluation from protected execution, but no fresh runtime forensics exist and the current SHADOW layer lacks realised out-of-sample outcome attribution. Solana observations deliberately contain no executable edge estimate; EVM observations are quote/simulation estimates rather than realised returns. Profitability, CANARY readiness and LIVE readiness are therefore unproven. Future evaluation must classify negative realised NET P&L after all costs as STRATEGY/MARKET loss, while RPC, simulation, signing, submission, confirmation and settlement faults remain EXECUTION/INFRASTRUCTURE failures.

## KEEP — Common positive executable-edge and abstention policy
This policy aligns selection with durable money-weighted NET P&L rather than trade count or win rate.

## NEW_SHADOW — Solana executable leader-copy edge
Solana economics require decision-time forward and reverse quotes at the intended size, dynamic priority-fee estimates, token-account/rent effects and latency-aware outcomes. Historical leader returns or win ratios do not establish copyable edge.

## IMPROVE — EVM cross-venue and learned-route strategies
A score calculated from the same quote used to select a route cannot prove persistence. EVM evidence must measure later executable results under chain-specific gas markets, reverts, priority bidding and route decay.

## REWORK — Strategy Lab lifecycle evidence
Promotion evidence should be based on independent, fully costed, money-weighted outcomes, with infrastructure failure rates reported separately. Raw wins and pre-cost profit factor can otherwise overstate durability.
