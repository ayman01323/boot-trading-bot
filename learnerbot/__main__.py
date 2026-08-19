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
# One-shot greeting requested for Telegram user 461513364; does not alter account/trading settings.
from . import telegram_hi_keefek_patch  # noqa: F401
# This is deliberately the final import before CLI: refuse startup if any feature or presentation layer displaced an audited trading hook.
from . import trading_runtime_invariant_patch  # noqa: F401
from .cli import main
raise SystemExit(main())
