import re
import subprocess
import sys
from pathlib import Path


def test_main_patch_import_order_starts_cleanly():
    """Import every `from . import patch` from __main__ in its real order.

    This reproduces the service's patch-composition phase without invoking
    learnerbot.cli.main(), starting workers, polling Telegram, or trading.
    """
    main_path = Path(__file__).resolve().parents[1] / "learnerbot" / "__main__.py"
    modules = []
    pattern = re.compile(r"^from \. import ([A-Za-z0-9_]+)")
    for line in main_path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line.strip())
        if match:
            modules.append(match.group(1))

    assert modules, "No ordered patch imports found in learnerbot/__main__.py"

    watched = [
        "solana_simulated_reserve_guard_patch",
        "solana_execution_efficiency_patch",
        "solana_atomic_close_fallback_patch",
        "solana_liquidity_fail_closed_patch",
        "solana_execution_validation_patch",
        "solana_exit_circuit_breaker_patch",
    ]
    script_lines = [
        "import importlib, sys",
        f"watched={watched!r}",
        "seen=set()",
    ]
    for name in modules:
        script_lines += [
            f"importlib.import_module('learnerbot.{name}')",
            f"trigger={name!r}",
            "for w in watched:",
            "    full='learnerbot.'+w",
            "    if full in sys.modules and w not in seen:",
            "        seen.add(w); print('FIRST_LOAD', w, 'while_importing', trigger, flush=True)",
        ]
    script_lines.append("print('MAIN_PATCH_IMPORT_ORDER_OK')")
    result = subprocess.run(
        [sys.executable, "-c", "\n".join(script_lines)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "MAIN_PATCH_IMPORT_ORDER_OK" in result.stdout
