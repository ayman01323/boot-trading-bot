from __future__ import annotations

"""Promote repeatedly-proven router/aggregator destinations for EVM HISTORY only.

Modern wallets often send swaps through universal routers or aggregators absent from
SiBot's configured execution-router set. The active reconstructor correctly rejects
unknown top-level destinations, but that can leave historical leader evidence at
zero even when the token/native flow is an otherwise strict closed spot trade.

This module never adds a venue to dex_registry.csv, live execution, signing or route
selection. It maintains a separate history-only evidence file. A destination is
promoted only after the existing strict native/wrapped-base FIFO reconstructor can
close trades through that destination for at least two independent wallets and at
least three closed matches in total. Raw wallet addresses are not persisted; only
salt-free one-way hashes are stored because the independence count, not identity, is
needed. Empty-router diagnostic replay remains unchanged.
"""

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from web3 import Web3

from . import sibot as _sibot
from . import sibot_alchemy_history_patch as _alchemy
from . import sibot_wrapped_base_history_patch as _wrapped

_PREV_RECONSTRUCT = _sibot.reconstruct_spot_trades
_PREV_STORE_SUCCESS = _alchemy._store_success
_LOCK = threading.RLock()
_PROMOTED: dict[int, set[str]] = {}
_STATE_FILE = "history_router_learning.json"
_MIN_INDEPENDENT_WALLETS = 2
_MIN_CLOSED_MATCHES = 3
_MAX_DESTINATIONS_PER_CHAIN = 256
_MAX_WALLETS_PER_DESTINATION = 64


def _state_path(app) -> Path:
    return Path(app.data_dir) / _STATE_FILE


def _read_state(app) -> dict:
    path = _state_path(app)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    return {"schema_version": 1, "chains": {}}


def _write_state(app, state: dict) -> None:
    path = _state_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _wallet_hash(chain_id: int, wallet: str) -> str:
    return hashlib.sha256(f"{int(chain_id)}:{str(wallet).lower()}".encode("utf-8")).hexdigest()[:24]


def _sync_promoted_from_state(state: dict) -> None:
    chains = state.get("chains") or {}
    for raw_chain, row in chains.items():
        try:
            chain_id = int(raw_chain)
        except Exception:
            continue
        promoted = {
            str(dest).lower()
            for dest, evidence in ((row or {}).get("destinations") or {}).items()
            if bool((evidence or {}).get("promoted")) and Web3.is_address(str(dest))
        }
        _PROMOTED[chain_id] = promoted


def _load_promoted(app) -> None:
    with _LOCK:
        _sync_promoted_from_state(_read_state(app))


def _record_destination_evidence(
    app,
    chain_id: int,
    destination: str,
    wallet: str,
    closed_matches: int,
    *,
    now_epoch: int | None = None,
) -> bool:
    """Record idempotent per-wallet evidence and return current promotion state."""
    destination = str(destination or "").lower()
    if not Web3.is_address(destination) or int(closed_matches) <= 0:
        return False
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    whash = _wallet_hash(chain_id, wallet)

    with _LOCK:
        state = _read_state(app)
        chains = state.setdefault("chains", {})
        chain = chains.setdefault(str(int(chain_id)), {"destinations": {}})
        destinations = chain.setdefault("destinations", {})
        evidence = destinations.setdefault(destination, {
            "wallets": {},
            "promoted": False,
            "promoted_epoch": 0,
            "last_seen_epoch": 0,
        })
        wallets = evidence.setdefault("wallets", {})
        previous = wallets.get(whash) or {}
        wallets[whash] = {
            "closed_matches": max(int(previous.get("closed_matches") or 0), int(closed_matches)),
            "last_seen_epoch": max(int(previous.get("last_seen_epoch") or 0), now),
        }

        if len(wallets) > _MAX_WALLETS_PER_DESTINATION:
            ordered = sorted(
                wallets.items(),
                key=lambda item: int((item[1] or {}).get("last_seen_epoch") or 0),
                reverse=True,
            )[:_MAX_WALLETS_PER_DESTINATION]
            evidence["wallets"] = wallets = dict(ordered)

        wallet_count = len(wallets)
        total_closed = sum(int((row or {}).get("closed_matches") or 0) for row in wallets.values())
        promoted = bool(evidence.get("promoted")) or (
            wallet_count >= _MIN_INDEPENDENT_WALLETS and total_closed >= _MIN_CLOSED_MATCHES
        )
        evidence["promoted"] = promoted
        evidence["promoted_epoch"] = int(evidence.get("promoted_epoch") or (now if promoted else 0))
        evidence["last_seen_epoch"] = now
        evidence["independent_wallets"] = wallet_count
        evidence["closed_matches"] = total_closed

        if len(destinations) > _MAX_DESTINATIONS_PER_CHAIN:
            ordered = sorted(
                destinations.items(),
                key=lambda item: (
                    1 if bool((item[1] or {}).get("promoted")) else 0,
                    int((item[1] or {}).get("last_seen_epoch") or 0),
                ),
                reverse=True,
            )[:_MAX_DESTINATIONS_PER_CHAIN]
            chain["destinations"] = destinations = dict(ordered)

        state["updated_epoch"] = now
        state["thresholds"] = {
            "independent_wallets": _MIN_INDEPENDENT_WALLETS,
            "closed_matches": _MIN_CLOSED_MATCHES,
        }
        state["history_only"] = True
        state["execution_router_registry_changed"] = False
        _sync_promoted_from_state(state)
        _write_state(app, state)
        return bool(promoted)


def _unknown_destinations(wallet: str, configured: set[str], normal_rows: list[dict]) -> list[str]:
    w = str(wallet or "").lower()
    out = []
    seen = set()
    for row in normal_rows:
        if not _wrapped._successful_normal(row):
            continue
        if str(row.get("from") or "").lower() != w:
            continue
        dest = str(row.get("to") or "").lower().strip()
        if not Web3.is_address(dest) or dest == w or dest in configured or dest in seen:
            continue
        seen.add(dest)
        out.append(dest)
    return out


def _learn_from_history(app, chain, wallet: str, normal, token, internal) -> dict:
    _load_promoted(app)
    configured = {str(value).lower() for value in _sibot._routers(app, chain)}
    tested = evidenced = newly_promoted = 0
    before = set(_PROMOTED.get(int(chain.chain_id), set()))

    for destination in _unknown_destinations(wallet, configured, normal):
        tested += 1
        try:
            trades, _unmatched = _PREV_RECONSTRUCT(
                wallet,
                {destination},
                normal,
                token,
                internal,
                int(chain.chain_id),
                str(chain.slug),
            )
        except Exception:
            continue
        if not trades:
            continue
        evidenced += 1
        promoted = _record_destination_evidence(
            app,
            int(chain.chain_id),
            destination,
            wallet,
            len(trades),
        )
        if promoted and destination not in before:
            newly_promoted += 1

    return {
        "tested_destinations": tested,
        "evidenced_destinations": evidenced,
        "newly_promoted": newly_promoted,
        "promoted_total": len(_PROMOTED.get(int(chain.chain_id), set())),
    }


def reconstruct_spot_trades_with_history_routers(
    wallet: str,
    routers: set[str],
    normal_rows: list[dict],
    token_rows: list[dict],
    internal_rows: list[dict],
    chain_id: int,
    chain_slug: str,
):
    # Empty router set is deliberately used by observability for all-destination
    # SHADOW replay. Preserve that meaning exactly.
    if not routers:
        return _PREV_RECONSTRUCT(
            wallet, routers, normal_rows, token_rows, internal_rows, chain_id, chain_slug
        )
    with _LOCK:
        learned = set(_PROMOTED.get(int(chain_id), set()))
    expanded = {str(value).lower() for value in routers} | learned
    return _PREV_RECONSTRUCT(
        wallet, expanded, normal_rows, token_rows, internal_rows, chain_id, chain_slug
    )


def store_success_with_history_router_learning(
    app,
    chain,
    wallet: str,
    fetched_at: int,
    normal: list[dict],
    token: list[dict],
    internal: list[dict],
    complete: bool,
):
    try:
        health = _learn_from_history(app, chain, wallet, normal, token, internal)
        if health["evidenced_destinations"] or health["promoted_total"]:
            print(
                "[evm-history-router-learning:%s] tested=%d evidenced=%d promoted_total=%d new=%d history_only=true"
                % (
                    chain.slug,
                    health["tested_destinations"],
                    health["evidenced_destinations"],
                    health["promoted_total"],
                    health["newly_promoted"],
                ),
                flush=True,
            )
    except Exception as exc:
        print(f"[evm-history-router-learning] {type(exc).__name__}: {str(exc)[:180]}", flush=True)
    return _PREV_STORE_SUCCESS(app, chain, wallet, fetched_at, normal, token, internal, complete)


def history_router_learning_health(app, chain_id: int) -> dict:
    with _LOCK:
        state = _read_state(app)
        _sync_promoted_from_state(state)
        chain = ((state.get("chains") or {}).get(str(int(chain_id))) or {})
        destinations = chain.get("destinations") or {}
        return {
            "candidates": len(destinations),
            "promoted": sum(1 for row in destinations.values() if bool((row or {}).get("promoted"))),
            "min_independent_wallets": _MIN_INDEPENDENT_WALLETS,
            "min_closed_matches": _MIN_CLOSED_MATCHES,
            "history_only": True,
            "execution_router_registry_changed": False,
        }


def install() -> None:
    if getattr(_sibot, "_history_router_learning_installed", False):
        return
    _sibot.reconstruct_spot_trades = reconstruct_spot_trades_with_history_routers
    _alchemy._store_success = store_success_with_history_router_learning
    _sibot.history_router_learning_health = history_router_learning_health
    _sibot._history_router_learning_installed = True
    print(
        "[evm-history-router-learning] installed=true promotion=2-wallets+3-closed "
        "history_only=true execution_registry_unchanged=true live_safety_unchanged=true",
        flush=True,
    )


install()
