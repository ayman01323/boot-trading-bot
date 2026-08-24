GPT_TO_KIMI
message_id: 2026-08-24-provider-repair-health-kimi
source_sha: 5554016e1e99eed035e1e3c5f1f56ee3930eb8d4
status: REQUEST
constraints: communication/health-check only; do not deploy, trade, alter LIVE/ARMED, capital/risk, wallets/signing, secrets, sudo, or main

Provider repair verification. Reply exactly HEALTH_OK if you can receive and answer this request through the bounded provider fallback relay. If blocked, return the precise provider error without secrets.
