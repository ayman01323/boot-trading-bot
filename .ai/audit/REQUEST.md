# Immediate three-agent full-program audit

Frozen source commit: `128fe91d2a0f2095be823d0a11a6f311e5593cfc`

Scope: FULL_REPOSITORY_BUG_AUDIT.

GPT, Gemini and Copilot must independently inspect the entire program: EVM and Solana execution, transaction construction, quote/order paths, accounting and realised P&L, databases and concurrency, retries/timeouts/rate limits, liquidity/sellability protections, Telegram permissions and reporting, Strategy Lab/shadow/canary boundaries, GitHub Actions/deployment, configuration loading, error handling and tests.

Priority: find concrete software defects that can cause avoidable losses, failed exits, incorrect balances/P&L, unsafe execution, missed trades, corrupted state, silent failures or misleading operator reports.

REPORT ONLY. Do not trade, deploy, alter live/risk/capital settings, edit secrets/wallet/signing material, or weaken any safety gate. Every finding must cite concrete file/symbol evidence, severity P0-P3, impact, root cause, bounded corrective action and required regression tests.
