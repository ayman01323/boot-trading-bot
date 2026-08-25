from __future__ import annotations

import csv
import json
import os
import threading
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

from . import cli as _cli
from . import sibot_alchemy_history_patch as _alchemy
from . import sibot_legacy_backlog_drainer_patch as _evm
from . import solana_sibot as _sol
from . import solana_worker_reliability_patch as _sol_reliable

"""Self-heal stalled trading research/selection workers without changing gates.

The supervisor never signs, submits or broadcasts a transaction. It only repairs
worker liveness, prevents an EVM backlog scan starvation case, refreshes stale
Solana ranking from existing evidence, and publishes sanitised telemetry.
"""

_PREV_APP = _cli._app
_ORIGINAL_EVM_ENSURE = _evm._ensure_drainer_started
_ORIGINAL_EVM_STATUS = _evm.status_for_chain

_STARTED = False
_START_LOCK = threading.Lock()
_RANK_LOCK = threading.Lock()

INITIAL_DELAY_SECONDS = 20
CHECK_SECONDS = 60
SOLANA_SELECTOR_STALE_SECONDS = 180
EVM_SCAN_EXTRA_ROWS = 250
EVM_SCAN_CAP = 2000
_BRIDGE = Path("/var/tmp/boot/trading_worker_liveness.json")
_SOL_SELECTOR = Path("/var/tmp/boot/solana_leader_selector.json")


def _alive_thread_names() -> set[str]:
    return {str(t.name) for t in threading.enumerate() if t.is_alive()}


def _evm_background_candidate_no_starvation(app, chain, now_epoch: int):
    """Look beyond the ranked queue instead of inspecting only 250 old errors."""
    ranked = _evm._ranked_wallets(app, chain)
    scan_limit = min(
        EVM_SCAN_CAP,
        max(int(_evm._SCAN_ROWS), len(ranked) + EVM_SCAN_EXTRA_ROWS),
    )
    try:
        with _evm._sibot._DB_LOCK, closing(_evm._sibot.connect(app)) as conn:
            rows = conn.execute(
                """SELECT wallet,fetched_at,error
                   FROM wallet_history_status
                   WHERE chain_id=? AND COALESCE(error,'')<>''
                   ORDER BY fetched_at ASC,wallet ASC LIMIT ?""",
                (int(chain.chain_id), int(scan_limit)),
            ).fetchall()
    except Exception:
        return None

    for wanted_kind in ("ALCHEMY_PROGRESS", "LEGACY_ETHERSCAN", "TRANSIENT_ALCHEMY"):
        for row in rows:
            wallet = str(row["wallet"] or "").lower().strip()
            if not wallet or wallet in ranked:
                continue
            kind = _evm._error_kind(
                str(row["error"] or ""),
                int(row["fetched_at"] or 0),
                int(now_epoch),
            )
            if kind == wanted_kind:
                return wallet, kind
    return None


def _evm_drainer_alive() -> bool:
    return "sibot-legacy-backlog-drainer" in _alive_thread_names()


def _ensure_evm_drainer_live(app) -> bool:
    """Supervisor-only recovery for a daemon proven absent after startup."""
    if not _evm._is_runtime_run_command():
        return False
    if _evm_drainer_alive():
        # Reconcile the boolean as well, so a later normal startup call cannot
        # mistake an already-live daemon for an unstarted one.
        _evm._DRAINER_STARTED = True
        return False
    # Do not replace the original start-once helper globally. Only the supervisor
    # clears a stale boolean after proving the uniquely named daemon is absent.
    _evm._DRAINER_STARTED = False
    return bool(_ORIGINAL_EVM_ENSURE(app))


def _status_with_provider(app, chain) -> dict:
    out = dict(_ORIGINAL_EVM_STATUS(app, chain))
    try:
        out["history_provider_available"] = bool(
            _alchemy.alchemy_rpc_url(app, int(chain.chain_id))
        )
    except Exception:
        out["history_provider_available"] = False
    try:
        ranked = _evm._ranked_wallets(app, chain)
        out["ranked_window"] = len(ranked)
        out["background_scan_limit"] = min(
            EVM_SCAN_CAP,
            max(int(_evm._SCAN_ROWS), len(ranked) + EVM_SCAN_EXTRA_ROWS),
        )
    except Exception:
        out["ranked_window"] = 0
        out["background_scan_limit"] = int(_evm._SCAN_ROWS)
    return out


def _selector_age_seconds(now: int | None = None) -> int | None:
    now = int(now or time.time())
    try:
        generated = int(
            json.loads(_SOL_SELECTOR.read_text(encoding="utf-8")).get("generated_epoch") or 0
        )
    except Exception:
        generated = 0
    return None if generated <= 0 else max(0, now - generated)


def _ensure_solana_threads(app) -> list[str]:
    """Restart only missing uniquely named workers; never duplicate a live one."""
    targets = (
        ("sibot-solana-discovery", _sol_reliable._discovery_worker),
        ("sibot-solana-history", _sol_reliable._history_worker),
        ("sibot-solana-leaders", _sol_reliable._leader_worker),
    )
    names = _alive_thread_names()
    missing = [(name, target) for name, target in targets if name not in names]
    if not missing:
        return []
    try:
        _sol.ensure_settings(app)
        _sol.connect(app).close()
    except Exception as exc:
        print("[trading-worker-liveness] solana init", type(exc).__name__, str(exc)[:220])
        return []

    launched = []
    for name, target in missing:
        if name in _alive_thread_names():
            continue
        threading.Thread(target=target, args=(app,), daemon=True, name=name).start()
        launched.append(name)
    if launched:
        _sol._WORKER_STARTED = True
        print("[trading-worker-liveness] restarted=" + ",".join(launched))
    return launched


def _refresh_solana_selector_if_stale(app, now: int | None = None) -> bool:
    now = int(now or time.time())
    age = _selector_age_seconds(now)
    if age is not None and age <= SOLANA_SELECTOR_STALE_SECONDS:
        return False
    if not _RANK_LOCK.acquire(blocking=False):
        return False
    try:
        # Same ranking function used by the normal discovery worker. It applies
        # existing quality/copy/edge gates and performs no signing/broadcast.
        _sol.refresh_rankings(app)
        print("[trading-worker-liveness] refreshed stale Solana selector thresholds=unchanged")
        return True
    except Exception as exc:
        print("[trading-worker-liveness] selector refresh", type(exc).__name__, str(exc)[:240])
        return False
    finally:
        _RANK_LOCK.release()


def _recent_auto_summary(app, now: int | None = None, seconds: int = 3600) -> dict:
    """Count recent direct-AUTO simulation outcomes without identifiers."""
    now = int(now or time.time())
    path = Path(app.csv_dir) / "auto" / "auto_trade_simulations.csv"
    buckets: dict[str, dict] = {}
    total = passed = 0
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    ts = int(float(row.get("timestamp_epoch") or 0))
                except Exception:
                    continue
                if ts <= 0 or now - ts > int(seconds):
                    continue
                chain = str(row.get("chain_slug") or row.get("chain_id") or "unknown").lower()[:40]
                ok = str(row.get("simulation_ok") or "").lower() in {"1", "true", "yes", "on"}
                total += 1
                passed += int(ok)
                bucket = buckets.setdefault(
                    chain, {"simulations": 0, "passed": 0, "reasons": Counter()}
                )
                bucket["simulations"] += 1
                bucket["passed"] += int(ok)
                if not ok:
                    reason = " ".join(str(row.get("reason") or "UNKNOWN").split())[:180]
                    bucket["reasons"][reason] += 1
    except Exception:
        pass

    clean = {}
    for chain, bucket in buckets.items():
        reasons = bucket.pop("reasons")
        clean[chain] = {**bucket, "top_rejections": dict(reasons.most_common(8))}
    return {
        "window_seconds": int(seconds),
        "simulations": total,
        "passed": passed,
        "by_chain": clean,
    }


def _provider_status(app) -> dict[str, bool]:
    out = {}
    for chain in _evm._enabled_evm_chains(app):
        try:
            out[str(chain.slug)] = bool(_alchemy.alchemy_rpc_url(app, int(chain.chain_id)))
        except Exception:
            out[str(chain.slug)] = False
    return out


def _write_bridge(app, launched: list[str], selector_refreshed: bool) -> None:
    try:
        now = int(time.time())
        names = _alive_thread_names()
        payload = {
            "schema_version": 1,
            "generated_epoch": now,
            "evm_drainer_alive": "sibot-legacy-backlog-drainer" in names,
            "evm_history_provider_available": _provider_status(app),
            "solana_threads": {
                "discovery": "sibot-solana-discovery" in names,
                "history": "sibot-solana-history" in names,
                "leaders": "sibot-solana-leaders" in names,
            },
            "solana_selector_age_seconds": _selector_age_seconds(now),
            "solana_threads_restarted": list(launched),
            "solana_selector_refreshed": bool(selector_refreshed),
            "direct_auto": _recent_auto_summary(app, now=now),
            "safety_gates_unchanged": True,
            "trading_actions_submitted_by_supervisor": 0,
        }
        _BRIDGE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _BRIDGE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, _BRIDGE)
    except Exception as exc:
        print("[trading-worker-liveness] bridge", type(exc).__name__, str(exc)[:220])


def _supervisor(app) -> None:
    time.sleep(INITIAL_DELAY_SECONDS)
    while True:
        launched: list[str] = []
        refreshed = False
        try:
            _ensure_evm_drainer_live(app)
        except Exception as exc:
            print("[trading-worker-liveness] evm", type(exc).__name__, str(exc)[:220])
        try:
            launched = _ensure_solana_threads(app)
            refreshed = _refresh_solana_selector_if_stale(app)
        except Exception as exc:
            print("[trading-worker-liveness] solana", type(exc).__name__, str(exc)[:220])
        _write_bridge(app, launched, refreshed)
        time.sleep(CHECK_SECONDS)


def _start(app) -> None:
    global _STARTED
    with _START_LOCK:
        if _STARTED:
            return
        threading.Thread(
            target=_supervisor,
            args=(app,),
            daemon=True,
            name="trading-worker-liveness-supervisor",
        ).start()
        _STARTED = True
        print("[trading-worker-liveness] supervisor enabled safety_gates_unchanged=true")


def _app_with_trading_worker_liveness():
    app = _PREV_APP()
    _start(app)
    return app


def install() -> None:
    # These two substitutions are read/recovery helpers only. Crucially, the
    # original drainer start-once function remains untouched for startup callers.
    _evm._background_candidate = _evm_background_candidate_no_starvation
    _evm.status_for_chain = _status_with_provider
    _cli._app = _app_with_trading_worker_liveness


install()
