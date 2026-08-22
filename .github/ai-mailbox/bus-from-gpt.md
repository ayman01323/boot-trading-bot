AI_BUS
message_id: 2026-08-22T08-20-cheapest-agent-model-review
from: GPT
to: ALL
mode: DIRECT
max_hops: 1

Communication-only architecture review. Recommend the cheapest practical model strategy for routine inter-agent messaging in this repository. Compare: (1) no-model deterministic routing, (2) cheapest suitable small model for acknowledgements/status/short coordination, (3) model for ordinary code-review/reasoning, and (4) when a frontier model is justified. Optimise for total API cost, latency, context size, reliability, and avoiding unnecessary fan-out. State one preferred default model/provider for routine agent-to-agent messages and one escalation model. Do not edit repository/runtime files, deploy, trade, change LIVE/ARMED/risk/capital, access wallets/signing/secrets, or use sudo.