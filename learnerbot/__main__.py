# Install Telegram transport safety before telegram_ui imports API helpers.
from . import telegram_safety_patch  # noqa: F401
# Install optional Telegram dashboard extensions before cli imports menu functions.
from . import telegram_dashboard_patch  # noqa: F401
# Install SiBot controls after the capital dashboard so both extensions compose.
from . import telegram_sibot_patch  # noqa: F401
# Keep SiBot Top 20 profit-first; apply stricter filters only to copy leaders.
from . import sibot_reasonable_top20_patch  # noqa: F401
# Restore the MASTER Auto Deploy status/timer menu after SiBot wraps the base menu.
from . import telegram_deploy_status_patch  # noqa: F401
# Slash commands must always bypass/cancel pending SiBot numeric prompts.
from . import telegram_pending_command_patch  # noqa: F401
# Apply the final compact visual presentation after all feature patches are installed.
from . import telegram_visual_ui_patch  # noqa: F401
# Make SiBot Top-20 and leader wallet addresses open the correct configured explorer.
from . import telegram_wallet_links_patch  # noqa: F401
# Add leader last-entry study, same-address EVM cross-chain intelligence and adaptive exits.
from . import sibot_intelligence_patch  # noqa: F401
# Add Solana discovery/ranking and leader monitoring.
from . import telegram_sibot_intelligence_patch  # noqa: F401
# Broaden EVM and Solana profit research before the final Top-20 selection.
from . import profit_research_expansion_patch  # noqa: F401
# Stop the older Top-20 compatibility layer from re-relaxing leader quality defaults.
from . import sibot_quality_compat_patch  # noqa: F401
# Send hourly Telegram capital/gas/opportunity-without-capital reminders.
from . import hourly_capital_alert_patch  # noqa: F401
# Final UI guard: Solana must always be visible in Top-20/Leaders pickers.
from . import telegram_solana_visibility_patch  # noqa: F401
# Immediate leader BUY/SELL funding, gas and token-readiness alerts + hourly exit audit.
from . import sibot_readiness_alert_patch  # noqa: F401
# Keep decorative Telegram dividers short enough for iPhone/mobile widths.
from . import telegram_mobile_divider_patch  # noqa: F401
# Manage separate per-user Solana public/signing wallets from Telegram.
from . import telegram_solana_wallet_patch  # noqa: F401
# Final wallet UI: manage multiple EVM wallets and multiple Solana wallets independently.
from . import telegram_multi_wallet_manager_patch  # noqa: F401
# Show Solana as an explicit ACTIVE/INACTIVE chain on the Telegram Chains status screen.
from . import telegram_solana_chains_patch  # noqa: F401
# Replace new Solana SHADOW entries with guarded LIVE Jupiter execution.
from . import solana_live_patch  # noqa: F401
# Count every landed-invalid LIVE transaction at user level, including monitor exits.
from . import solana_execution_fault_counter_patch  # noqa: F401
# Pin each Solana LIVE position to its entry wallet and quarantine stale legacy rows.
from . import solana_position_wallet_binding_patch  # noqa: F401
# Private-chat LIVE arming/disarming and Solana LIVE dashboard.
from . import telegram_solana_live_patch  # noqa: F401
# Quality-first EVM leader selection, copied-performance checks, dynamic sizing and circuit breakers.
from . import sibot_profit_guard_patch  # noqa: F401
# Lightweight report/test app objects do not run one-shot runtime migrations.
from . import sibot_profit_guard_runtime_compat_patch  # noqa: F401
# Apply profit-factor, drawdown, recent-performance and copied-performance gates to Solana leaders too.
from . import solana_profit_guard_patch  # noqa: F401
# Every relevant Telegram page includes the correct Solana equivalent.
from . import telegram_solana_everywhere_patch  # noqa: F401
# Final aliases + Capital/P&L integration after the visual/dashboard patches.
from . import telegram_solana_everywhere_compat_patch  # noqa: F401
# Final truth layer for hourly capital, LIVE states and combined EVM+Solana leaders/reports.
from . import telegram_live_reporting_patch  # noqa: F401
# User-confirmed manual Solana wallet transfers (SOL amount or USD-equivalent SOL).
from . import telegram_solana_send_patch  # noqa: F401
# One-shot migration requested by the user: set Solana LIVE trade size and reserve to built-in hard minimums.
from . import solana_minimum_settings_migration  # noqa: F401
# Earlier quality preset; the balanced-frequency preset below intentionally relaxes only opportunity-selection thresholds.
from . import solana_quality_settings_migration  # noqa: F401
# Restore a balanced Solana opportunity rate while preserving LIVE/simulation/reserve safeguards.
from . import solana_frequency_settings_migration  # noqa: F401
# Prioritise real-time selected-leader monitoring over non-urgent history backfill without weakening the 30s entry freshness gate.
from . import solana_live_latency_settings_migration  # noqa: F401
# Allow a second distinct guarded Solana LIVE position; same-mint repeat buys remain blocked.
from . import solana_position_capacity_migration  # noqa: F401
# Expose the expanded quality/profit controls in the existing SiBot settings callbacks.
from . import telegram_sibot_quality_settings_patch  # noqa: F401
# Show the Solana PF/drawdown/recent-performance gates on the dedicated Solana LIVE page.
from . import telegram_solana_quality_settings_patch  # noqa: F401
# Final presentation layer: show current USD equivalents beside asset/native values across Telegram pages.
from . import telegram_usd_everywhere_patch  # noqa: F401
# Apply the same USD presentation to confirmed EVM/Solana BUY and SELL notifications.
from . import telegram_order_usd_patch  # noqa: F401
# Master-controlled Polygon-only AUTO focus; never changes LIVE/ARMED/signing gates.
from . import polygon_focus_patch  # noqa: F401
# Final wallet/capital truth layer: every EVM chain remains visible with USD, gas reserve and LIVE/AUTO readiness.
from . import telegram_capital_readiness_patch  # noqa: F401
# Final Solana activity truth layer: record BUY/SELL/SKIP/REJECT decisions and show why no trade occurred.
from . import solana_trade_diagnostics_patch  # noqa: F401
# Establish the requested Telegram roles only; never mirror or copy trading settings between accounts.
from . import telegram_account_roles_patch  # noqa: F401
# Explicit owner-authorised one-shot migration: arm Telegram 6760898817 for real EVM/SiBot/Solana execution only.
from . import telegram_676_full_live_migration  # noqa: F401
# Owner-authorised Polygon LIVE migration: enable Polygon-only direct AUTO while preserving all profit/simulation gates.
from . import polygon_live_enable_migration  # noqa: F401
# Per-user low-capital Solana canary: 0.0005 SOL trade + 0.005 SOL reserve for 6760898817 only.
from . import telegram_676_solana_low_capital_migration  # noqa: F401
# Do not let unreconciled/refundable Solana account funding block subsequent LIVE entries platform-wide.
from . import solana_overhead_gate_correction_patch  # noqa: F401
# Before enforcing the LIVE position cap, quarantine only positions proven empty across all registered wallets.
from . import solana_entry_capacity_reconcile_patch  # noqa: F401
# Preflight must price the same router universe that the single-signer LIVE executor can execute.
from . import solana_quote_execution_consistency_patch  # noqa: F401
# Share the exact same fresh preflight result across users so one leader signal does not multiply Jupiter load.
from . import solana_preflight_cache_patch  # noqa: F401
# Do not let old pre-rent-reconciliation P&L poison future copied-leader selection.
from . import solana_profit_accounting_epoch_patch  # noqa: F401
# EVM leader monitoring must retry an uncertain block/receipt rather than permanently skipping it.
from . import sibot_evm_worker_reliability_patch  # noqa: F401
# Retry transient RPC failures, never skip a failed finalized slot, and decouple fresh discovery from history backfill.
from . import solana_worker_reliability_patch  # noqa: F401
# Advance each selected Solana leader cursor only through signatures actually fetched/processed.
from . import solana_leader_cursor_reliability_patch  # noqa: F401
# On a full exit, reclaim only zero-balance token accounts proven to have been created by this bot's BUY.
from . import solana_token_account_reclaim_patch  # noqa: F401
# Exclude refundable token-account rent from open loss signals and keep it attached through partial exits.
from . import solana_refundable_rent_accounting_patch  # noqa: F401
# Require signed BUY simulation to prove the wallet still retains its configured emergency reserve.
from . import solana_simulated_reserve_guard_patch  # noqa: F401
# Hard-cap fees/tips and price impact; full exits atomically sell + reclaim proven token-account rent.
from . import solana_execution_efficiency_patch  # noqa: F401
# Legacy/unproven full exits fall back to the same capped managed SELL instead of becoming unsellable.
from . import solana_atomic_close_fallback_patch  # noqa: F401
# Final Solana SELL presentation: 💚 profit, ❤️ loss, 🍉 break-even on realised net P&L.
from . import solana_sell_pnl_emoji_patch  # noqa: F401
# Final Telegram command scope: USER chats never receive MASTER slash commands in the blue command menu.
from . import telegram_command_scope_patch  # noqa: F401
# Final USER-only presentation guard: keep the non-MASTER /menu short after all compatibility layers.
from . import telegram_user_menu_compact_patch  # noqa: F401
# Expand personal Telegram reports with profit alerts, user-chosen percentages and time presets.
from . import telegram_profit_report_alerts_patch  # noqa: F401
# Present loss alerts as positive loss magnitude: alert when loss reaches or exceeds the chosen percentage.
from . import telegram_loss_alert_direction_patch  # noqa: F401
# One-shot greeting requested for Telegram user 461513364; does not alter account/trading settings.
from . import telegram_hi_keefek_patch  # noqa: F401
# Loss exits may unwind in safe slices when whole-position Solana liquidity is too shallow; 100% impact is never bypassed.
from . import solana_emergency_liquidity_unwind_patch  # noqa: F401
# Extend emergency slice search down to 1% while preserving the same impact ceiling and adding a pre-broadcast dust-output floor.
from . import solana_stuck_liquidity_safe_slice_patch  # noqa: F401
# Critical EVM safety fix: native transfers must include the validated destination in the signed transaction.
from . import evm_transfer_native_hotfix_patch  # noqa: F401
# Export already-sanitised runtime forensics to a local bridge readable by the self-hosted GitHub runner.
from . import loss_forensics_runtime_bridge_patch  # noqa: F401
# Extend sanitised forensics with Aug-18-to-present Solana LIVE P/L, decision, leader/asset and strategy-SHA evidence only.
from . import solana_incident_forensics_patch  # noqa: F401
# Refuse startup if any feature or presentation layer displaced an audited trading hook.
from . import trading_runtime_invariant_patch  # noqa: F401
# Re-tighten Solana leader-quality thresholds and restore the leader-event circuit
# breaker that the first-day rollback loosened; keeps first-day timing/frequency
# settings and the positive-executable-edge preflight untouched.
from . import solana_leader_quality_restore_patch  # noqa: F401
# Additional final invariant for Polygon-only AUTO focus and fresh runtime-evidence handoff.
from . import polygon_live_runtime_invariant_patch  # noqa: F401
# Final trade attribution layer: stamp each EVM/Solana position with immutable strategy version + exact opening Git SHA.
from . import trade_strategy_provenance_patch  # noqa: F401
# Feed real SiBot leader-copy and direct-market-arbitrage results into the Strategy
# Lab's per-chain scorecards. Recording only -- never arms LIVE trading, changes
# capital or bypasses a safety gate (see strategy_lab.py's own invariants).
from . import strategy_lab_live_recording_patch  # noqa: F401
# The one bounded, evidence-based action taken on that data: proportionally reduce
# SiBot position sizing for a chain whose Strategy Lab evidence has become
# REPLACE/REWORK-worthy, using the same multiplier pattern sibot_profit_guard_patch
# already applies for its own profit-lock throttle. Never a hard stop.
from . import sibot_strategy_lab_throttle_patch  # noqa: F401
# Telegram /strategylab: master-only, read-only report of real Strategy Lab
# evidence (per-family net P&L, profit factor, trades). Reporting only.
from . import telegram_strategy_lab_report_patch  # noqa: F401
# Telegram /solanaforceexit POSITION_ID CONFIRM: operator-confirmed manual override
# for a Solana position the automatic emergency-exit path can never safely close
# (liquidity permanently gone, not a transient dip). Ownership-checked, capped
# below a literal ~100% impact quote even when confirmed.
from . import telegram_solana_force_exit_patch  # noqa: F401
# Restore hard floors on EVM SiBot leader-selection and copied-performance quality
# thresholds so no per-user override (existing or future) can weaken them below the
# platform-restored bar -- the EVM equivalent of solana_leader_quality_restore_patch.py,
# which already protects the Solana side the same way.
from . import sibot_leader_quality_hard_floor_patch  # noqa: F401
# Start the loopback-only, zero-token-routing WebSocket agent bus as a daemon thread
# owned by learnerbot. It never edits trading state and skips itself if an external
# broker is already serving 127.0.0.1:8765.
from . import ai_agent_ws_runtime_patch  # noqa: F401
# Base fail-closed composition gate for the complete pre-existing trading stack.
# Change Set 4 is applied only after this succeeds, then gets its own final gate.
from . import final_runtime_integrity_patch  # noqa: F401
# Permanent MASTER Telegram bridge to the isolated Google learner wallet/LIVE controls.
from . import telegram_google_learner_launcher_patch  # noqa: F401
# CHANGE SET 4 — approved 2026-08-29T10:38:58Z / 11:38:58 BST.
# Subject: historical BUY restore + 0.005 SOL/10 positions + 30+3m full-exit + LP revalidation.
from . import solana_owner_changeset_4_patch  # noqa: F401
# Refuse startup if any stamped Change Set 4 wrapper is missing or displaced.
from . import solana_owner_changeset_4_integrity_patch  # noqa: F401
from .cli import main
raise SystemExit(main())