# Deployment attestation watcher fix

The deployment attestation previously checked the `ai-ops-watcher` startup marker only in the latest bounded status snapshot. During a healthy restart, that early startup line can scroll out of the status wrapper output before the later Telegram command verification marker appears.

The attestation now keeps authoritative SHA and service-state checks bound to the current status snapshot, while startup markers are detected across both the deploy wrapper output and the refreshed status snapshot. The generated attestation records which source supplied the watcher marker.

This changes deployment reporting only. It does not change trading, LIVE/ARMED state, signing, balances, risk controls, Telegram command registration, or the AI watcher itself.
