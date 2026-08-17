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
# Add Solana discovery/ranking/SHADOW monitoring and Telegram intelligence controls.
from . import telegram_sibot_intelligence_patch  # noqa: F401
# Broaden EVM and Solana profit research before the final Top-20 selection.
from . import profit_research_expansion_patch  # noqa: F401
# Send hourly Telegram capital/gas/opportunity-without-capital reminders.
from . import hourly_capital_alert_patch  # noqa: F401
# Final UI guard: Solana must always be visible in Top-20/Leaders pickers.
from . import telegram_solana_visibility_patch  # noqa: F401
# Immediate leader BUY/SELL funding, gas and token-readiness alerts + hourly exit audit.
from . import sibot_readiness_alert_patch  # noqa: F401
# Keep decorative Telegram dividers short enough for iPhone/mobile widths.
from . import telegram_mobile_divider_patch  # noqa: F401
# Manage separate per-user Solana public wallets from Telegram (SHADOW identity/funding only).
from . import telegram_solana_wallet_patch  # noqa: F401
# Final wallet UI: manage multiple EVM wallets and multiple Solana wallets independently.
from . import telegram_multi_wallet_manager_patch  # noqa: F401
# Show Solana as an explicit ACTIVE/INACTIVE chain on the Telegram Chains status screen.
from . import telegram_solana_chains_patch  # noqa: F401
# TEMPORARY DIAGNOSTIC: write active Solana public wallet balance(s) to /tmp on startup.
from . import runtime_solana_balance_probe  # noqa: F401
from .cli import main
raise SystemExit(main())
