"""Fail-closed EVM execution guard.

learnerbot/live_executor.py's LiveTrader has no code-level execution guard
equivalent to solana_execution_risk_patch.py -- only CSV-driven gates
(live_trading_settings.csv:trading_enabled + per-user live_trading_enabled,
learnerbot/live_executor.py:213-222 `_require_enabled`). Those are settings
in this instance's own isolated CSV, not code, and this bot's isolation
guarantees are about the code path, not about trusting a CSV file to never
end up in a state that allows a trade.

This module wraps LiveTrader.buy/sell/execute_cycle/execute_v3_cycle --
every real signing/broadcast entry point -- and unconditionally refuses.
Not "refuses unless AUTHORISED_CHAINS says otherwise": refuses, full stop,
because no EVM equivalent of the Solana guard (identity check, SIGNER_READY,
risk limits, daily-loss/drawdown) has been built yet. Listing an EVM chain
in AUTHORISED_CHAINS does not change this -- that variable only ever
authorises a chain that ALSO has an execution guard actually enforcing
something, and none exists for EVM. Building real EVM support means adding
a real guard here with the same properties as the Solana one, not relaxing
this file.
"""

from __future__ import annotations

from learnerbot import live_executor as _executor

_original_buy = _executor.LiveTrader.buy
_original_sell = _executor.LiveTrader.sell
_original_execute_cycle = _executor.LiveTrader.execute_cycle
_original_execute_v3_cycle = _executor.LiveTrader.execute_v3_cycle


class EvmExecutionGuardError(RuntimeError):
    """Raised by every guarded EVM entry point. Always -- EVM has no execution guard yet."""


def _refuse(method_name: str, chain_slug: str):
    raise EvmExecutionGuardError(
        f"EVM execution ({method_name} on chain {chain_slug!r}) is not supported by claude-trading-bot: "
        f"no execution guard (identity/signer/risk-limit enforcement) exists yet for EVM, unlike Solana. "
        f"This refusal does not depend on AUTHORISED_CHAINS -- listing an EVM chain there is not "
        f"sufficient authorisation without a real guard to go with it."
    )


def _guarded_buy(self, token: str, amount_native, confirm: str) -> dict:
    _refuse("buy", self.chain.slug)


def _guarded_sell(self, token: str, amount_spec: str, confirm: str) -> dict:
    _refuse("sell", self.chain.slug)


def _guarded_execute_cycle(self, path, amount_native, min_net_profit_native, confirm: str = "CONFIRM") -> dict:
    _refuse("execute_cycle", self.chain.slug)


def _guarded_execute_v3_cycle(
    self, path, fees, amount_native, min_net_profit_native, router_address, quoter_address, confirm="CONFIRM"
) -> dict:
    _refuse("execute_v3_cycle", self.chain.slug)


def install() -> None:
    if getattr(_executor.LiveTrader, "_claude_evm_guard_installed", False):
        return
    _executor.LiveTrader.buy = _guarded_buy
    _executor.LiveTrader.sell = _guarded_sell
    _executor.LiveTrader.execute_cycle = _guarded_execute_cycle
    _executor.LiveTrader.execute_v3_cycle = _guarded_execute_v3_cycle
    _executor.LiveTrader._claude_evm_guard_installed = True
