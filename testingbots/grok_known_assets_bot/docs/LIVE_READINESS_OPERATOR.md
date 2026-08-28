Operator command: `/groklivecheck on CONFIRM`.

Expected status after enable: mode `LIVE_READINESS`, arm ON, real-money signing DISABLED, transaction broadcast DISABLED.

On a qualified strategy entry the runner performs fresh public Jupiter route checks and emits either `LIVE_READY` or `LIVE_PREFLIGHT_REJECT`. No wallet/private-key access is part of this path.
