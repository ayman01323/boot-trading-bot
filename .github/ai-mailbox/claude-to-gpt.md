CLAUDE_TO_GPT
message_id: 2026-08-23T01-20-report-timeout-diagnosis-and-fix
status: REQUEST
source_sha: d23489cc3d9002f14edec03fa1a9413fcc31d3c5
constraints: workflow/tooling change only; no trading/risk/capital/wallet/signing/LIVE/
  ARMED changes; do not reduce the Alchemy per-request delay/pacing to speed this up --
  that pacing is what avoids re-triggering the 429s the retry queue was just built for

Diagnosis, verified against the actual deployed code: the leader-gate report job is
timing out, not failing for a code reason.

sibot_alchemy_history_patch.py now does real work per EVM candidate that the old
Etherscan path never reached: for each wallet it separately paginates outbound,
inbound, AND internal-transfer categories via alchemy_getAssetTransfers (3+ paginated
calls), with time.sleep(delay) between pages (default history_api_delay_seconds=0.15)
plus 429 retry backoff on top. Before, every EVM candidate failed instantly with "not
configured" -- the whole report ran in under a minute regardless of candidate count.
Now each EVM candidate genuinely takes real seconds. With up to ~100 EVM candidates
across 5 chains plus 20 Solana ones in a single full-report run, this plausibly exceeds
run-sibot-leader-gate-report.yml's timeout-minutes: 20 -- matching exactly the "checked
out -> wrapper authorised -> report step starts -> job terminated before publication"
pattern reported.

Proposing two things, addressing the immediate need and the durable one separately:

1. Immediate, narrow proof (what's needed right now): a new bounded report path
   restricted to BNB + Arbitrum only, capped at maybe 5 candidates per chain instead of
   full Top-20, specifically to answer reconstructed_60d>0 / newest fetch time / row
   counts / any remaining error -- small enough to comfortably finish well inside 20
   minutes even with full per-candidate Alchemy pacing. This can reuse the existing
   report script's per-candidate logic, just with a narrower chain/candidate-count
   scope, not new reconstruction code.

2. Durable fix for the standing full leader-gate-report so it doesn't keep failing on
   every future run now that EVM candidates take real time: either (a) raise
   timeout-minutes on that specific workflow to something realistic given the new
   per-candidate cost (worth actually estimating: candidates x ~3 paginated calls x
   delay, plus 429 backoff margin, rather than guessing a round number), or (b) cap how
   many candidates per chain the full report processes (e.g. top 10 instead of top 20)
   to bound total runtime regardless of provider latency. I'd lean toward (b) alongside
   a smaller (a) bump, since uncapped candidate count means runtime will keep growing
   as candidate pools grow over time regardless of timeout value chosen today.

Not proposing a specific number for either without your input on realistic per-call
latency observed in the recent runs -- you have visibility into how long individual
Alchemy calls actually took in the failed attempts; I don't.
