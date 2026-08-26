from __future__ import annotations

import time

from . import sibot1_solana_live_bridge_patch as _bridge
from . import sibot1_trade_event_telegram_patch as _alerts
from .solana_wallet_store import SolanaWalletStore

# Final state-ordering correction for the protected SiBot 1 Solana bridge.
#
# The bridge already fail-closes LIVE ENTRY through fresh PoolCheck/RugCheck,
# reverse sellability, stress, signed simulation and execution validation. The
# bug fixed here is ordering/presentation: a candidate was claimed (and therefore
# announced as LIVE-selected) before an EXIT proved a real LIVE position existed,
# and before an ENTRY completed its fresh LIVE revalidation.
#
# This patch does not relax any LIVE gate, signer control, position cap, slippage/
# impact cap, simulation requirement, or broadcast authority.

_PREV_PROCESS_CANDIDATE = _bridge._process_candidate
_PREV_CANDIDATE_SELECTED = _alerts._candidate_selected
_PREV_LIVE_REVALIDATION = _bridge._live_entry_revalidation
_PREV_START = _bridge._start
_INSTALLED = False

_TOKEN_PROGRAMS = (
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
)
_MIGRATION_VERSION = "sibot1-solana-candidate-state-v1"

_RECON_SCHEMA = """
CREATE TABLE IF NOT EXISTS reconciliation_positions(
  telegram_id TEXT NOT NULL,
  mint TEXT NOT NULL,
  token_raw TEXT NOT NULL,
  status TEXT NOT NULL,
  source TEXT NOT NULL,
  first_seen INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(telegram_id,mint)
);
CREATE TABLE IF NOT EXISTS reconciliation_meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
"""


def _candidate_mint(candidate) -> str:
    return str(
        candidate.get("asset_out")
        or candidate.get("asset")
        or candidate.get("token")
        or ""
    ).strip()


def _wallet_address(app, tid) -> str:
    try:
        store = SolanaWalletStore(app.csv_dir, app.data_dir)
        meta = store.get_meta(tid)
        return str(meta.get("address") or "").strip()
    except Exception:
        return ""


def _parse_token_accounts(result) -> dict[str, int]:
    totals: dict[str, int] = {}
    if not isinstance(result, dict):
        return totals
    for row in result.get("value") or []:
        try:
            account = row.get("account") or {}
            info = (((account.get("data") or {}).get("parsed") or {}).get("info") or {})
            mint = str(info.get("mint") or "").strip()
            amount = int(((info.get("tokenAmount") or {}).get("amount")) or 0)
        except Exception:
            continue
        if mint and amount > 0:
            totals[mint] = totals.get(mint, 0) + amount
    return totals


def _wallet_token_balance_raw(app, tid, mint: str) -> int:
    """Read public on-chain ownership for one mint; never decrypt a signer."""
    address = _wallet_address(app, tid)
    mint = str(mint or "").strip()
    if not address or not mint:
        return 0
    result = _bridge._sol._rpc(
        app,
        "getTokenAccountsByOwner",
        [
            address,
            {"mint": mint},
            {"encoding": "jsonParsed", "commitment": "confirmed"},
        ],
    )
    return sum(_parse_token_accounts(result).values())


def _ensure_reconciliation_schema(app):
    conn = _bridge._db(app)
    try:
        conn.executescript(_RECON_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _record_reconciliation_owned(app, tid, mint: str, token_raw: int, source: str) -> None:
    token_raw = max(0, int(token_raw or 0))
    mint = str(mint or "").strip()
    if not mint or token_raw <= 0:
        return
    now = int(time.time())
    conn = _bridge._db(app)
    try:
        conn.executescript(_RECON_SCHEMA)
        conn.execute(
            """INSERT INTO reconciliation_positions(
                 telegram_id,mint,token_raw,status,source,first_seen,updated_at
               ) VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(telegram_id,mint) DO UPDATE SET
                 token_raw=excluded.token_raw,
                 status='RECONCILIATION_OWNED',
                 source=excluded.source,
                 updated_at=excluded.updated_at""",
            (
                str(tid), mint, str(token_raw), "RECONCILIATION_OWNED",
                str(source), now, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def reconciliation_row(app, tid, mint: str) -> dict | None:
    conn = _bridge._db(app)
    try:
        conn.executescript(_RECON_SCHEMA)
        row = conn.execute(
            "SELECT * FROM reconciliation_positions WHERE telegram_id=? AND mint=?",
            (str(tid), str(mint)),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _confirmed_live_position(app, tid, candidate) -> dict | None:
    pos = _bridge._position(app, tid, candidate.get("shadow_lot_id"))
    if not pos:
        return None
    try:
        if int(pos.get("token_raw") or 0) <= 0:
            return None
    except Exception:
        return None
    return pos


def _reconcile_missing_exit_position(app, tid, candidate) -> None:
    """Record wallet ownership separately; never promote it to ordinary AI EXIT."""
    mint = _candidate_mint(candidate)
    if not mint:
        return
    try:
        owned = _wallet_token_balance_raw(app, tid, mint)
    except Exception:
        # RPC uncertainty must not create ownership or authorise an EXIT.
        return
    if owned > 0:
        _record_reconciliation_owned(app, tid, mint, owned, "runtime_exit_signal")


def _process_candidate_state_aware(app, tid, candidate) -> None:
    kind = str(candidate.get("kind") or "").upper()
    if kind == "EXIT" and _bridge._candidate_age(candidate) <= _bridge.MAX_SIGNAL_AGE_SECONDS:
        try:
            ready = _bridge.readiness(app, tid)
        except Exception:
            ready = {}
        if ready.get("exit_execution_active"):
            # Position-first EXIT: no claim, no LIVE-selected alert and no exit
            # attempt unless the exact LIVE bridge lot already exists.
            if not _confirmed_live_position(app, tid, candidate):
                _reconcile_missing_exit_position(app, tid, candidate)
                return
    return _PREV_PROCESS_CANDIDATE(app, tid, candidate)


def _candidate_selected_state_aware(module, chain: str, app, tid, candidate) -> None:
    if chain != "solana" or module is not _bridge:
        return _PREV_CANDIDATE_SELECTED(module, chain, app, tid, candidate)

    kind = str(candidate.get("kind") or "").upper()
    if kind == "ENTRY":
        # A claim is not a LIVE selection. Only fresh LIVE revalidation can
        # promote the alert from SHADOW/considered state to LIVE-selected.
        if not bool(candidate.get("_live_revalidated")):
            return None
        promoted = dict(candidate)
        promoted["poolcheck_verdict"] = "LIVE_REVALIDATED"
        return _PREV_CANDIDATE_SELECTED(module, chain, app, tid, promoted)

    if kind == "EXIT":
        # The state-aware process wrapper already enforces this before claim;
        # re-check here so reporting can never get ahead of state.
        if not _confirmed_live_position(app, tid, candidate):
            return None
        confirmed = dict(candidate)
        confirmed["poolcheck_verdict"] = "POSITION_CONFIRMED"
        return _PREV_CANDIDATE_SELECTED(module, chain, app, tid, confirmed)

    return _PREV_CANDIDATE_SELECTED(module, chain, app, tid, candidate)


def _live_revalidation_with_selection(app, mint, amount_sol):
    result = _PREV_LIVE_REVALIDATION(app, mint, amount_sol)
    if bool(result[0]):
        ctx = getattr(_alerts._TLS, "solana_entry", None)
        if ctx:
            tid, candidate = ctx
            promoted = dict(candidate)
            promoted["_live_revalidated"] = True
            _candidate_selected_state_aware(_bridge, "solana", app, tid, promoted)
    return result


def _bridge_open_mints(app, tid) -> set[str]:
    conn = _bridge._db(app)
    try:
        rows = conn.execute(
            "SELECT mint FROM positions WHERE telegram_id=? AND status='OPEN'",
            (str(tid),),
        ).fetchall()
        return {str(r["mint"] or "") for r in rows if str(r["mint"] or "")}
    finally:
        conn.close()


def _migration_key(tid) -> str:
    return f"{_MIGRATION_VERSION}:{tid}"


def _migration_done(app, tid) -> bool:
    conn = _bridge._db(app)
    try:
        conn.executescript(_RECON_SCHEMA)
        row = conn.execute(
            "SELECT value FROM reconciliation_meta WHERE key=?", (_migration_key(tid),)
        ).fetchone()
        return bool(row and str(row["value"] or "") == "complete")
    finally:
        conn.close()


def _mark_migration_done(app, tid) -> None:
    now = int(time.time())
    conn = _bridge._db(app)
    try:
        conn.executescript(_RECON_SCHEMA)
        conn.execute(
            """INSERT INTO reconciliation_meta(key,value,updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (_migration_key(tid), "complete", now),
        )
        conn.commit()
    finally:
        conn.close()


def reconcile_wallet_owned_tokens(app, tid) -> bool:
    """One-time public-chain inventory; records untracked holdings as reconciliation-only."""
    if _migration_done(app, tid):
        return True
    address = _wallet_address(app, tid)
    if not address:
        return False

    totals: dict[str, int] = {}
    try:
        for program_id in _TOKEN_PROGRAMS:
            result = _bridge._sol._rpc(
                app,
                "getTokenAccountsByOwner",
                [
                    address,
                    {"programId": program_id},
                    {"encoding": "jsonParsed", "commitment": "confirmed"},
                ],
            )
            if not isinstance(result, dict):
                return False
            for mint, amount in _parse_token_accounts(result).items():
                totals[mint] = totals.get(mint, 0) + int(amount)
    except Exception:
        return False

    tracked = _bridge_open_mints(app, tid)
    for mint, amount in totals.items():
        if amount > 0 and mint not in tracked:
            _record_reconciliation_owned(app, tid, mint, amount, "pre_fix_migration")
    _mark_migration_done(app, tid)
    return True


def _start_with_reconciliation(app):
    # Complete the one-time ownership inventory before the bridge worker starts.
    # A failed/uncertain RPC leaves the migration incomplete so a later process
    # start retries; it never fabricates a LIVE position or permits an EXIT.
    try:
        controls = [r for r in _bridge._rows(_bridge._control_path(app)) if _bridge._bool(r.get("live_enabled"))]
        for ctl in controls:
            tid = str(ctl.get("telegram_id") or "")
            if tid:
                reconcile_wallet_owned_tokens(app, tid)
    except Exception as exc:
        print(f"[sibot1-solana-state-fix] reconciliation {type(exc).__name__}: {str(exc)[:200]}")
    return _PREV_START(app)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _bridge._process_candidate = _process_candidate_state_aware
    _alerts._candidate_selected = _candidate_selected_state_aware
    _bridge._live_entry_revalidation = _live_revalidation_with_selection
    _bridge._start = _start_with_reconciliation
    _INSTALLED = True
    print(
        "[sibot1-solana-state-fix] installed=true exit_position_first=true "
        "entry_live_alert_after_revalidation=true reconciliation_only=true safety_gates=unchanged"
    )


install()
