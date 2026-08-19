import importlib
import pkgutil

import learnerbot


# __main__ starts the application. trading_runtime_invariant_patch is deliberately
# imported only after the audited runtime patch stack has been composed, so an
# alphabetical standalone import of that final guard is not a meaningful module
# import test. Its actual final composition is covered by
# tests/test_solana_runtime_composition.py.
_IMPORT_SWEEP_EXCLUDES = {"__main__", "trading_runtime_invariant_patch"}


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
