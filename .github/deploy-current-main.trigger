requested: 2026-08-24T20:52+01:00
purpose: deploy Basic Engine v0 as the primary EVM AUTO entrypoint using the existing main-bot RPC/config stack; no local APEX API/RPC proxy
source_pr: 616
base_engine_merge: 4acc7830f4be9b6d8efc714c04b1abd3cec3f546
constraints: exact current main only; restricted deploy wrapper; full test suite already green; automatic restore on failure; preserve wallet/private-key isolation, LIVE/ARMED controls, quarantine, scanner verification, pool-rug gates, minimum-profit protection and final pre-broadcast eth_call; do not force LIVE/ARMED values
