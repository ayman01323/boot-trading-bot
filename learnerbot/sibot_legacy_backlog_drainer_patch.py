from __future__ import annotations

import sys
import threading
import time
from contextlib import closing

from . import sibot as _sibot
from . import sibot_alchemy_history_patch as _alchemy
from . import sibot_alchemy_retry_queue_patch as _retry
from .config import load_chains

"""Low-priority recovery for orphaned EVM history backlog.

The normal SiBot history worker intentionally prioritises the ranked candidate
window.  The legacy sweep guarantees old Etherscan rows eventually get a turn,
but its 15-minute-per-chain cadence is intentionally conservative and can take a
long time to clear a large inherited backlog.

This patch adds a separate *bounded* background drainer that:
- handles only errored history rows outside the current ranked candidate window;
- performs at most one extra wallet refresh globally per pacing interval;
- uses the existing serial Alchemy history lock, so it cannot burst providers in
  parallel with the normal history worker;
- yields for a full pacing interval after each completed attempt, so normal ranked
  work gets priority again before the drainer can make another attempt;
- applies account-wide exponential backoff after Alchemy rate-limit pressure;
- never changes leader quality, trading, LIVE/ARMED, capital, wallet/signing,
  liquidity, simulation, profit, or execution gates.
"""

_PREV_START_WORKERS = _sibot.start_workers
_DRAINER_STARTED = False
_DRAINER_START_LOCK = threading.Lock()

_DEFAULT_INTERVAL_SECONDS = 45
_MIN_INTERVAL_SECONDS = 30
_MAX_INTERVAL_SECONDS = 300
_DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 60
_DEFAULT_MAX_BACKOFF_SECONDS = 15 * 60
_DEFAULT_NONTRANSIENT_BACKOFF_SECONDS = 5 * 60
_DEFAULT_IDLE_POLL_SECONDS = 30
_MIN_TRANSIENT_RETRY_AGE_SECONDS = 60
_SCAN_ROWS = 250

_GLOBAL_NEXT_KEY = "legacy_backlog_drainer:global_next"
_PREFIX = "legacy_backlog_drainer"


def _setting_int(app, key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        cfg = _sibot.platform_settings(app, 0)
        value = _sibot._int(cfg.get(key), default)
    except Exception:
        value = default
    return max(minimum, min(maximum, int(value)))


def _interval_seconds(app) -> int:
    return _setting_int(
        app,
        "legacy_backlog_drainer_interval_seconds",
        _DEFAULT_INTERVAL_SECONDS,
        _MIN_INTERVAL_SECONDS,
        _MAX_INTERVAL_SECONDS,
    )


def _rate_limit_backoff_seconds(app) -> int:
    return _setting_int(
        app,
        "legacy_backlog_drainer_rate_limit_backoff_seconds",
        _DEFAULT_RATE_LIMIT_BACKOFF_SECONDS,
        30,
        10 * 60,
    )


def _max_backoff_seconds(app) -> int:
    return _setting_int(
        app,
        "legacy_backlog_drainer_max_backoff_seconds",
        _DEFAULT_MAX_BACKOFF_SECONDS,
        60,
        60 * 60,
    )


def _nontransient_backoff_seconds(app) -> int:
    return _setting_int(
        app,
        "legacy_backlog_drainer_nontransient_backoff_seconds",
        _DEFAULT_NONTRANSIENT_BACKOFF_SECONDS,
        60,
        60 * 60,
    )


def _key(chain_id: int, name: str) -> str:
    return f"{_PREFIX}:{int(chain_id)}:{name}"


def _read_state_int(app, key: str, default: int = 0) -> int:
    try:
        with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
            return _sibot._int(_sibot._state(conn, key, default), default)
    except Exception:
        return int(default)


def _read_state_text(app, key: str, default: str = "") -> str:
    try:
        with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
            return str(_sibot._state(conn, key, default) or default)
    except Exception:
        return str(default)


def _write_state(app, values: dict[str, object]) -> None:
    if not values:
        return
    with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
        conn.executemany(
            "INSERT INTO state(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [(str(key), str(value)) for key, value in values.items()],
        )
        conn.commit()


def _increment_state(app, key: str, amount: int = 1) -> int:
    with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
        current = _sibot._int(_sibot._state(conn, key, 0), 0)
        value = current + int(amount)
        conn.execute(
            "INSERT INTO state(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        conn.commit()
        return value


def _is_evm_chain(chain) -> bool:
    return str(getattr(chain, "type", "EVM") or "EVM").upper() == "EVM"


def _enabled_evm_chains(app) -> list:
    try:
        return [chain for chain in load_chains(app, enabled_only=True) if _is_evm_chain(chain)]
    except Exception:
        return []


def _ranked_wallets(app, chain) -> set[str]:
    """Return the normal ranked history window already owned by the main worker."""
    try:
        cfg = _sibot.platform_settings(app, int(chain.chain_id))
        limit = max(20, min(500, _sibot._int(cfg.get("history_candidate_wallets"), 40)))
        return {
            str(wallet or "").lower()
            for wallet in _sibot._candidate_wallets(app, chain, limit)
            if str(wallet or "").strip()
        }
    except Exception:
        return set()


def _error_kind(error: str, fetched_at: int, now_epoch: int) -> str:
    text = str(error or "")
    if _retry._legacy_etherscan_error(text):
        return "LEGACY_ETHERSCAN"
    if (
        _retry._retryable_alchemy_error(text)
        and int(fetched_at or 0) <= int(now_epoch) - _MIN_TRANSIENT_RETRY_AGE_SECONDS
    ):
        return "TRANSIENT_ALCHEMY"
    return ""


def _background_candidate(app, chain, now_epoch: int) -> tuple[str, str] | None:
    """Pick one orphaned backlog row not already owned by the ranked queue."""
    ranked = _ranked_wallets(app, chain)
    try:
        with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
            rows = conn.execute(
                """SELECT wallet,fetched_at,error
                   FROM wallet_history_status
                   WHERE chain_id=? AND COALESCE(error,'')<>''
                   ORDER BY fetched_at ASC,wallet ASC LIMIT ?""",
                (int(chain.chain_id), _SCAN_ROWS),
            ).fetchall()
    except Exception:
        return None

    # Prefer true pre-Alchemy migration backlog over current transient failures.
    for wanted_kind in ("LEGACY_ETHERSCAN", "TRANSIENT_ALCHEMY"):
        for row in rows:
            wallet = str(row["wallet"] or "").lower().strip()
            if not wallet or wallet in ranked:
                continue
            kind = _error_kind(str(row["error"] or ""), int(row["fetched_at"] or 0), now_epoch)
            if kind == wanted_kind:
                return wallet, kind
    return None


def _eligible_attempts(app, now_epoch: int, chains=None) -> list[tuple[int, int, object, str, str]]:
    eligible = []
    for chain in list(chains) if chains is not None else _enabled_evm_chains(app):
        if not _is_evm_chain(chain):
            continue
        try:
            if not _alchemy.alchemy_rpc_url(app, int(chain.chain_id)):
                continue
        except Exception:
            continue
        chain_next = _read_state_int(app, _key(chain.chain_id, "next_epoch"), 0)
        if chain_next > now_epoch:
            continue
        candidate = _background_candidate(app, chain, now_epoch)
        if not candidate:
            continue
        wallet, kind = candidate
        last_attempt = _read_state_int(app, _key(chain.chain_id, "last_attempt_epoch"), 0)
        eligible.append((last_attempt, int(chain.chain_id), chain, wallet, kind))
    return eligible


def _provider_pressure(error: str) -> bool:
    text = str(error or "")
    if _retry._retryable_alchemy_error(text):
        return True
    low = text.lower()
    return "alchemy" in low and any(
        marker in low
        for marker in (
            "http 429",
            "rpc 429",
            "rate limit",
            "compute units per second",
            "retries exhausted",
        )
    )


def _drain_once(app, *, now_epoch: int | None = None, chains=None) -> dict:
    """Perform at most one globally-paced background recovery attempt."""
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    global_next = _read_state_int(app, _GLOBAL_NEXT_KEY, 0)
    if global_next > now:
        return {"status": "BACKOFF", "next_epoch": global_next}

    attempts = _eligible_attempts(app, now, chains=chains)
    if not attempts:
        return {"status": "IDLE"}

    _, _, chain, wallet, kind = min(attempts, key=lambda item: (item[0], item[1]))
    cid = int(chain.chain_id)
    attempt_count = _increment_state(app, _key(cid, "attempts"), 1)
    _write_state(
        app,
        {
            _key(cid, "last_attempt_epoch"): now,
            _key(cid, "last_kind"): kind,
            _key(cid, "last_result"): "RUNNING",
        },
    )

    result = None
    try:
        result = _sibot.refresh_wallet_history(app, chain, wallet)
    except Exception as exc:
        result = {"wallet": wallet, "complete": False, "error": f"{type(exc).__name__}: {exc}"}

    # In production use completion time, not start time. A slow reconstruction can
    # hold the shared serial history lock for minutes; pacing from completion gives
    # the normal ranked worker a guaranteed yield window before the next drain.
    finished = int(time.time()) if now_epoch is None else now
    error = str((result or {}).get("error") or "") if isinstance(result, dict) else ""
    interval = _interval_seconds(app)

    if not error:
        successes = _increment_state(app, _key(cid, "successes"), 1)
        next_epoch = finished + interval
        _write_state(
            app,
            {
                _GLOBAL_NEXT_KEY: next_epoch,
                _key(cid, "next_epoch"): next_epoch,
                _key(cid, "consecutive_pressure"): 0,
                _key(cid, "last_result"): "SUCCESS",
                _key(cid, "last_success_epoch"): finished,
            },
        )
        return {
            "status": "SUCCESS",
            "chain": str(getattr(chain, "slug", cid)),
            "chain_id": cid,
            "wallet": wallet,
            "kind": kind,
            "attempts": attempt_count,
            "successes": successes,
            "next_epoch": next_epoch,
        }

    if _provider_pressure(error):
        pressure_count = _increment_state(app, _key(cid, "rate_limits"), 1)
        consecutive = _read_state_int(app, _key(cid, "consecutive_pressure"), 0) + 1
        base = _rate_limit_backoff_seconds(app)
        backoff = min(_max_backoff_seconds(app), base * (2 ** max(0, consecutive - 1)))
        next_epoch = finished + backoff
        _write_state(
            app,
            {
                _GLOBAL_NEXT_KEY: next_epoch,
                _key(cid, "next_epoch"): next_epoch,
                _key(cid, "consecutive_pressure"): consecutive,
                _key(cid, "last_result"): "RATE_LIMIT",
            },
        )
        return {
            "status": "RATE_LIMIT",
            "chain": str(getattr(chain, "slug", cid)),
            "chain_id": cid,
            "wallet": wallet,
            "kind": kind,
            "rate_limits": pressure_count,
            "backoff_seconds": backoff,
            "next_epoch": next_epoch,
        }

    failures = _increment_state(app, _key(cid, "failures"), 1)
    chain_next = finished + _nontransient_backoff_seconds(app)
    global_next = finished + interval
    _write_state(
        app,
        {
            _GLOBAL_NEXT_KEY: global_next,
            _key(cid, "next_epoch"): chain_next,
            _key(cid, "consecutive_pressure"): 0,
            _key(cid, "last_result"): "FAILED",
        },
    )
    return {
        "status": "FAILED",
        "chain": str(getattr(chain, "slug", cid)),
        "chain_id": cid,
        "wallet": wallet,
        "kind": kind,
        "failures": failures,
        "next_epoch": chain_next,
    }


def status_for_chain(app, chain) -> dict:
    """Return sanitised recovery telemetry for /whynotrade and diagnostics."""
    cid = int(chain.chain_id)
    legacy = 0
    transient = 0
    try:
        with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
            rows = conn.execute(
                "SELECT fetched_at,error FROM wallet_history_status "
                "WHERE chain_id=? AND COALESCE(error,'')<>''",
                (cid,),
            ).fetchall()
        now = int(time.time())
        for row in rows:
            kind = _error_kind(str(row["error"] or ""), int(row["fetched_at"] or 0), now)
            if kind == "LEGACY_ETHERSCAN":
                legacy += 1
            elif kind == "TRANSIENT_ALCHEMY":
                transient += 1
    except Exception:
        pass
    return {
        "legacy_backlog": legacy,
        "transient_backlog": transient,
        "attempts": _read_state_int(app, _key(cid, "attempts"), 0),
        "successes": _read_state_int(app, _key(cid, "successes"), 0),
        "failures": _read_state_int(app, _key(cid, "failures"), 0),
        "rate_limits": _read_state_int(app, _key(cid, "rate_limits"), 0),
        "last_attempt_epoch": _read_state_int(app, _key(cid, "last_attempt_epoch"), 0),
        "last_success_epoch": _read_state_int(app, _key(cid, "last_success_epoch"), 0),
        "next_epoch": _read_state_int(app, _key(cid, "next_epoch"), 0),
        "last_result": _read_state_text(app, _key(cid, "last_result"), "NEVER"),
    }


def _drainer_loop(app) -> None:
    while True:
        try:
            outcome = _drain_once(app)
            status = str(outcome.get("status") or "")
            if status in {"SUCCESS", "RATE_LIMIT", "FAILED"}:
                print(
                    "[sibot-legacy-drainer] status=%s chain=%s kind=%s next_epoch=%s"
                    % (
                        status,
                        outcome.get("chain", ""),
                        outcome.get("kind", ""),
                        outcome.get("next_epoch", ""),
                    )
                )
            if status == "IDLE":
                sleep_for = _DEFAULT_IDLE_POLL_SECONDS
            elif status == "BACKOFF":
                remaining = max(1, int(outcome.get("next_epoch") or 0) - int(time.time()))
                sleep_for = min(_DEFAULT_IDLE_POLL_SECONDS, remaining)
            else:
                sleep_for = 10
        except Exception as exc:
            print("[sibot-legacy-drainer]", type(exc).__name__, exc)
            sleep_for = _DEFAULT_IDLE_POLL_SECONDS
        time.sleep(max(5, int(sleep_for)))


def _is_runtime_run_command() -> bool:
    return len(sys.argv) >= 2 and str(sys.argv[1]).strip().lower() == "run"


def start_workers_with_legacy_backlog_drainer(app):
    global _DRAINER_STARTED
    result = _PREV_START_WORKERS(app)
    if not _is_runtime_run_command():
        return result
    with _DRAINER_START_LOCK:
        if _DRAINER_STARTED:
            return result
        _DRAINER_STARTED = True
        threading.Thread(
            target=_drainer_loop,
            args=(app,),
            daemon=True,
            name="sibot-legacy-backlog-drainer",
        ).start()
    print(
        "[sibot-legacy-drainer] started global_interval=%ss provider_backoff=adaptive ranked_queue_priority=true"
        % _interval_seconds(app)
    )
    return result


def install() -> None:
    if getattr(_sibot, "_legacy_backlog_drainer_patch_installed", False):
        return
    _sibot.start_workers = start_workers_with_legacy_backlog_drainer
    _sibot._legacy_backlog_drainer_patch_installed = True


install()
