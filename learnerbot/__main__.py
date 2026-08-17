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
from .cli import main
raise SystemExit(main())
