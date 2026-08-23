import importlib
import pkgutil
import subprocess
import sys

import learnerbot


# __main__ starts the application. These runtime-integrity guards are deliberately
# imported only after the audited production patch stack has been composed, so an
# alphabetical standalone import is not meaningful. Their actual final composition
# is exercised by the real `python -m learnerbot --help` startup test below and by
# tests/test_solana_runtime_composition.py.
_IMPORT_SWEEP_EXCLUDES = {
    "__main__",
    "trading_runtime_invariant_patch",
    "final_runtime_integrity_patch",
}


def test_all_learnerbot_modules_import():
    failures = []
    for m in pkgutil.iter_modules(learnerbot.__path__):
        if m.name in _IMPORT_SWEEP_EXCLUDES:
            continue
        name = "learnerbot." + m.name
        try:
            importlib.import_module(name)
        except Exception as exc:
            failures.append((name, type(exc).__name__, str(exc)))
    assert not failures, failures


def test_python_m_learnerbot_real_startup_stack_reaches_cli_help():
    """Exercise the actual __main__ import order, not a hand-built test order."""
    result = subprocess.run(
        [sys.executable, "-m", "learnerbot", "--help"],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    assert result.returncode == 0, combined
    assert "Audited trading runtime invariant failed" not in combined
    assert "Final runtime integrity failed" not in combined
    assert "[final-runtime-integrity] OK" in combined
