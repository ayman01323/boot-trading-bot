# Install optional Telegram dashboard extensions before cli imports menu functions.
from . import telegram_dashboard_patch  # noqa: F401
from .cli import main
raise SystemExit(main())
