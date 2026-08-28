"""Test-only helpers for Claude's composition-aware runtime-health gate.

The production gate added on 2026-08-26 deliberately requires two things that
an isolated unit-test process does not naturally have:

1. a genuine owner command must have passed through Claude's Telegram router;
2. learnerbot's final-runtime integrity module must have completed its full
   wrapper-chain audit.

`test_claude_execution_and_telegram.py` is intentionally a lower-level unit
suite; it does not boot the complete learnerbot application. Without a test
harness, the stronger production gate short-circuits every deeper risk/state
assertion with "owner command not processed" / "final runtime ... not loaded".

This file supplies only those *test-process prerequisites*. It never changes
production modules on disk, never arms a real runtime, and never calls signing
or broadcast code. The fake final-integrity result is dynamic: if a test
actually displaces a Claude guard/router, its corresponding check goes false,
so the existing composition-break tests remain meaningful rather than being
papered over.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _claude_runtime_health_unit_context(request, monkeypatch):
    """Provide final-runtime prerequisites only to the legacy composed-path suite."""
    module = getattr(request, "module", None)
    if module is None or not module.__name__.endswith("test_claude_execution_and_telegram"):
        yield
        return

    # Ensure the suite's own quarantine/env/install fixtures have run first.
    request.getfixturevalue("_env")
    request.getfixturevalue("_guard_installed")

    import claude_runtime_health_patch as runtime_health
    import evm_execution_guard_patch as evm_guard
    import solana_execution_risk_patch as guard
    import telegram_control_patch as router
    from learnerbot import live_executor as evm_executor
    from learnerbot import solana_live_executor as solana_executor
    from learnerbot import telegram_ui

    owner_id = str(getattr(module, "OWNER_ID"))

    # Simulate the prerequisite that a real authenticated owner command has
    # already traversed the router. Tests that exercise the router itself still
    # call the real router; this only prevents unrelated risk tests from being
    # short-circuited before reaching the condition they are intended to test.
    monkeypatch.setattr(runtime_health, "_ROUTER_DISPATCH_PROVEN", True)
    monkeypatch.setattr(runtime_health, "_ROUTER_DISPATCH_SENDER", owner_id)
    monkeypatch.setattr(runtime_health, "_ROUTER_DISPATCH_COMMAND", "/claude_status")
    monkeypatch.setattr(runtime_health, "_ROUTER_DISPATCH_AT", 1.0)

    def composition_checks() -> dict[str, bool]:
        # These names intentionally retain the wording asserted by the legacy
        # breakage tests. The values are live identities, not hard-coded True,
        # so monkeypatching any guard/router is still detected.
        return {
            "Telegram router": telegram_ui.handle_update is router.handle_update,
            "BUY guard": solana_executor.SolanaLiveExecutor.buy is guard._guarded_buy,
            "SELL guard": solana_executor.SolanaLiveExecutor.sell is guard._guarded_sell,
            "EVM buy guard displaced": evm_executor.LiveTrader.buy is evm_guard._guarded_buy,
            "EVM sell guard displaced": evm_executor.LiveTrader.sell is evm_guard._guarded_sell,
            "EVM execute_cycle guard displaced": (
                evm_executor.LiveTrader.execute_cycle is evm_guard._guarded_execute_cycle
            ),
            "EVM execute_v3_cycle guard displaced": (
                evm_executor.LiveTrader.execute_v3_cycle is evm_guard._guarded_execute_v3_cycle
            ),
        }

    fake_final_integrity = SimpleNamespace(composition_checks=composition_checks)
    monkeypatch.setitem(sys.modules, "learnerbot.final_runtime_integrity_patch", fake_final_integrity)

    yield
