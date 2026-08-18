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
# Allow a second distinct guarded Solana LIVE position; same-mint repeat buys remain blocked.
from . import solana_position_capacity_migration  # noqa: F401
# Expose the expanded quality/profit controls in the existing SiBot settings callbacks.
from . import telegram_sibot_quality_settings_patch  # noqa: F401
# Show the Solana PF/drawdown/recent-performance gates on the dedicated Solana LIVE page.
from . import telegram_solana_quality_settings_patch  # noqa: F401
# Final presentation layer: show current USD equivalents beside asset/native values across Telegram pages.
from . import telegram_usd_everywhere_patch  # noqa: F401
# Master-controlled Polygon-only AUTO focus; never changes LIVE/ARMED/signing gates.
from . import polygon_focus_patch  # noqa: F401
# Final wallet/capital truth layer: every EVM chain remains visible with USD, gas reserve and LIVE/AUTO readiness.
from . import telegram_capital_readiness_patch  # noqa: F401
# Final Solana activity truth layer: record BUY/SELL/SKIP/REJECT decisions and show why no trade occurred.
from . import solana_trade_diagnostics_patch  # noqa: F401
# Establish the requested Telegram roles only; never mirror or copy trading settings between accounts.
from . import telegram_account_roles_patch  # noqa: F401
# Final Telegram command scope: USER chats never receive MASTER slash commands in the blue command menu.
from . import telegram_command_scope_patch  # noqa: F401
from .cli import main
raise SystemExit(main())
