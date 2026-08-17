# Install Telegram transport safety before telegram_ui imports API helpers.
from . import telegram_safety_patch  # noqa: F401
# Install optional Telegram dashboard extensions before cli imports menu functions.
from . import telegram_dashboard_patch  # noqa: F401
from .cli import main
raise SystemExit(main())
