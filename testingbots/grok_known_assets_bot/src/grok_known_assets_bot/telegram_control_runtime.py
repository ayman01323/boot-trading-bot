from __future__ import annotations

from typing import Any

from . import telegram_control as _base

_ORIG_STATUS_TEXT = _base._status_text
_ORIG_HANDLE_COMMAND = _base.handle_command
_ORIG_EVENT_ALERT_TEXT = _base._event_alert_text


def _entry_wording(text: str | None) -> str | None:
    if text is None:
        return None
    return (
        str(text)
        .replace(
            "Canary target: 0.0005 SOL (hard max 0.001 SOL)",
            "Entry target: 0.009 SOL (hard max 0.009 SOL)",
        )
        .replace(
            "Canary target: 0.0005 SOL; hard maximum: 0.001 SOL.",
            "Entry target: 0.009 SOL; hard maximum: 0.009 SOL.",
        )
        .replace("USDC→SOL canary:", "USDC→SOL entry:")
    )


def _status_text() -> str:
    return str(_entry_wording(_ORIG_STATUS_TEXT()) or "")


def handle_command(text: str, chat_id: str) -> str | None:
    return _entry_wording(_ORIG_HANDLE_COMMAND(text, chat_id))


def _event_alert_text(kind: str, asset: str, payload: dict[str, Any]) -> str | None:
    return _entry_wording(_ORIG_EVENT_ALERT_TEXT(kind, asset, payload))


def run() -> int:
    _base._status_text = _status_text
    _base.handle_command = handle_command
    _base._event_alert_text = _event_alert_text
    return _base.run()


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
