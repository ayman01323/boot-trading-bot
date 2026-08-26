"""Claude-bot Telegram identity layer.

Follows this repo's existing convention of adding behaviour via a thin patch
module rather than editing learnerbot/telegram.py directly. Only touches
message formatting — no strategy, risk, or execution behaviour.
"""

from __future__ import annotations

from typing import Optional

from learnerbot import telegram as _telegram

IDENTITY_PREFIX = "\U0001F916 CLAUDE TRADING BOT\n"

_send_message_original = _telegram.send_message
_send_to_chats_original = _telegram.send_to_chats


def _prefixed(text: str) -> str:
    if text.startswith(IDENTITY_PREFIX):
        return text
    return IDENTITY_PREFIX + text


def send_message(token: str, chat_id: str, text: str, **kwargs):
    return _send_message_original(token, chat_id, _prefixed(text), **kwargs)


def send_to_chats(token: str, chat_ids: list[str], text: str, **kwargs):
    return _send_to_chats_original(token, chat_ids, _prefixed(text), **kwargs)


def install() -> None:
    """Monkey-patch learnerbot.telegram so every send goes through this module.

    Mirrors the install() convention used by evm_pool_rug_gate.py /
    solana_pool_risk_gate.py elsewhere in this codebase.
    """
    _telegram.send_message = send_message
    _telegram.send_to_chats = send_to_chats


def build_startup_message(
    *,
    version: str,
    github_sha: str,
    server_sha: str,
    mode: str,
    authorised_chains: list[str],
    active_strategy: str,
    capital_basis_usd: float,
    max_position_usd: float,
    max_total_exposure_usd: float,
    max_drawdown_pct: float,
    wallet_balance_summary: str,
    signer_ready: bool,
) -> str:
    chains = ", ".join(authorised_chains) if authorised_chains else "(none authorised)"
    return (
        f"Startup\n"
        f"Version: {version}\n"
        f"GitHub SHA: {github_sha}\n"
        f"Server SHA: {server_sha}\n"
        f"Mode: {mode}\n"
        f"Authorised chains: {chains}\n"
        f"Active strategy: {active_strategy}\n"
        f"Capital basis: ${capital_basis_usd:,.2f}\n"
        f"Max position: ${max_position_usd:,.2f}\n"
        f"Max exposure: ${max_total_exposure_usd:,.2f}\n"
        f"Drawdown latch: {max_drawdown_pct:.2f}%\n"
        f"Wallet balance: {wallet_balance_summary}\n"
        f"SIGNER_READY: {str(signer_ready).lower()} "
        + ("(broadcast possible if ARMED)" if signer_ready else "(broadcast unavailable)")
    )


def build_risk_rejection_message(reason: str, *, context: Optional[str] = None) -> str:
    lines = ["Risk rejection", f"Reason: {reason}"]
    if context:
        lines.append(f"Context: {context}")
    return "\n".join(lines)
