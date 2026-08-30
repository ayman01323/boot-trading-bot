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


def _live_ready_activation_wording(text: str | None) -> str | None:
    """Report the real canary state without weakening the approval boundary."""
    if text is None:
        return None
    state = _base.load_state()
    if not bool(state.get("live_money_enabled")):
        return text
    return (
        str(text)
        .replace("Signing: DISABLED", "Signing: ENABLED (manual canary)")
        .replace(
            "Broadcast: DISABLED",
            "Broadcast path: ENABLED — APPROVAL-GATED; no transaction sent yet",
        )
    )


def _status_text() -> str:
    return str(_entry_wording(_ORIG_STATUS_TEXT()) or "")


def handle_command(text: str, chat_id: str, from_user_id: str | None = None) -> str | None:
    """Preserve immutable Telegram sender identity for LIVE approvals."""
    return _entry_wording(_ORIG_HANDLE_COMMAND(text, chat_id, from_user_id))


def _event_alert_text(kind: str, asset: str, payload: dict[str, Any]) -> str | None:
    # A malformed zero-value approval must never be presented as actionable.
    if kind == "CANARY_PENDING":
        spend = int(payload.get("input_micro_usdc") or 0)
        min_out = int(payload.get("min_out_lamports") or 0)
        approval_id = str(payload.get("approval_id") or "").strip()
        if spend <= 0 or min_out <= 0 or not approval_id:
            return (
                "⚠️ GROK LIVE CANARY — MALFORMED APPROVAL BLOCKED\n"
                f"Asset: {asset or 'system'}\n"
                "No approval command issued. No broadcast allowed."
            )
        return (
            "🟠 GROK LIVE CANARY — APPROVAL NEEDED\n"
            f"Asset: {asset or 'system'}\n"
            f"Ticket ID: {approval_id}\n"
            f"Target: {int(payload.get('target_lamports') or 0)} lamports\n"
            f"Spend: {spend} micro-USDC\n"
            f"Minimum output: {min_out} lamports\n\n"
            "APPROVE EXACTLY:\n"
            f"/grokapprove {approval_id} CONFIRM\n\n"
            "Expires fast. The runner revalidates, simulates and checks funding before broadcast."
        )
    text = _entry_wording(_ORIG_EVENT_ALERT_TEXT(kind, asset, payload))
    if kind == "LIVE_READY":
        text = _live_ready_activation_wording(text)
    return text


def run() -> int:
    _base._status_text = _status_text
    _base.handle_command = handle_command
    _base._event_alert_text = _event_alert_text
    return _base.run()


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
