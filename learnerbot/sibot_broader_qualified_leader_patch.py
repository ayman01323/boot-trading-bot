from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

from . import sibot as _sibot
from . import sibot_profit_guard_patch as _guard

_PREV_REFRESH = _sibot.refresh_rankings
_BRIDGE = Path("/var/tmp/boot/evm_leader_selector.json")
_BRIDGE_LOCK = threading.Lock()


def _broad_candidates(app, chain_id: int, cfg: dict) -> list[str]:
    lookback = max(1, min(365, _sibot._int(cfg.get("lookback_days"), 60)))
    cutoff = int(time.time()) - lookback * 86400
    cap = max(20, min(1000, _sibot._int(cfg.get("leader_selection_candidate_cap"), 500)))
    with closing(_sibot.connect(app)) as conn:
        rows = conn.execute(
            """SELECT lower(wallet) wallet,
                      SUM(CAST(net_native AS REAL)) net,
                      COUNT(*) closed
               FROM wallet_trades
               WHERE chain_id=? AND sell_ts>=?
               GROUP BY lower(wallet)
               HAVING SUM(CAST(net_native AS REAL)) > 0
               ORDER BY net DESC, closed DESC
               LIMIT ?""",
            (int(chain_id), cutoff, cap),
        ).fetchall()
    return [str(row["wallet"] or "").lower() for row in rows if str(row["wallet"] or "").strip()]


def _write_bridge(chain, pool: int, qualified: int, selected: int, failures: Counter) -> None:
    row = {
        "chain_id": int(chain.chain_id),
        "chain_slug": str(chain.slug),
        "pool": int(pool),
        "qualified": int(qualified),
        "selected": int(selected),
        "first_failure_counts": dict(sorted((str(k), int(v)) for k, v in failures.items())),
        "generated_epoch": int(time.time()),
        "thresholds_unchanged": True,
    }
    try:
        with _BRIDGE_LOCK:
            payload = {"schema_version": 1, "chains": {}}
            if _BRIDGE.exists():
                try:
                    existing = json.loads(_BRIDGE.read_text(encoding="utf-8"))
                    if isinstance(existing, dict):
                        payload.update(existing)
                except Exception:
                    pass
            payload["schema_version"] = 1
            payload.setdefault("chains", {})[str(chain.slug)] = row
            payload["generated_epoch"] = int(time.time())
            _BRIDGE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _BRIDGE.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.chmod(tmp, 0o644)
            os.replace(tmp, _BRIDGE)
    except Exception:
        pass


def refresh_rankings(app, telegram_id, chain):
    """Preserve public Top-20, then run strict quality gates over a broader pool.

    The previous composition took the public Top-20 first and only then applied the
    stricter PF/win-rate/drawdown/recent gates. Twenty failures therefore produced
    zero leaders even if wallet #21 or later met every existing safety threshold.
    """
    result = _PREV_REFRESH(app, telegram_id, chain)
    cfg = _sibot.user_settings(app, telegram_id, chain.chain_id)
    recent_n = max(5, _sibot._int(cfg.get("recent_trade_window"), 20))

    try:
        wallets = _broad_candidates(app, int(chain.chain_id), cfg)
    except Exception as exc:
        print(
            f"[sibot-broader-qualified:{chain.slug}] pool_failed={type(exc).__name__}: {str(exc)[:160]}"
        )
        return result

    safe = []
    failures: Counter = Counter()
    for wallet in wallets:
        try:
            metrics = _guard.quality_metrics(
                app,
                int(chain.chain_id),
                wallet,
                cfg.get("lookback_days", 60),
                recent_n,
            )
            ok, reason = _guard._leader_quality_ok(metrics, cfg)
        except Exception as exc:
            failures[f"metrics unavailable: {type(exc).__name__}"] += 1
            continue
        if not ok:
            failures[str(reason or "quality gate failed")] += 1
            continue
        safe.append((wallet, metrics, _guard._quality_score(metrics, cfg)))

    safe.sort(
        key=lambda item: (
            item[2],
            _sibot._dec(item[1].get("net")),
            _sibot._dec(item[1].get("profit_factor")),
            _sibot._dec(item[1].get("recent_profit_factor")),
        ),
        reverse=True,
    )

    now = int(time.time())
    nleaders = max(1, min(10, _sibot._int(cfg.get("leaders_per_chain"), 3)))
    selected = safe[:nleaders]
    with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
        old = {
            str(row["wallet"]).lower(): int(row["selected_at"] or now)
            for row in conn.execute(
                "SELECT wallet,selected_at FROM leaders WHERE telegram_id=? AND chain_id=?",
                (str(telegram_id), int(chain.chain_id)),
            ).fetchall()
        }
        conn.execute(
            "DELETE FROM leaders WHERE telegram_id=? AND chain_id=?",
            (str(telegram_id), int(chain.chain_id)),
        )
        for rank, (wallet, metrics, _score) in enumerate(selected, 1):
            conn.execute(
                """INSERT INTO leaders(
                       telegram_id,chain_id,chain_slug,rank,wallet,net_profit_native,
                       win_rate,closed_trades,selected_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(telegram_id),
                    int(chain.chain_id),
                    str(chain.slug),
                    rank,
                    wallet,
                    str(metrics["net"]),
                    float(metrics["win_rate"]),
                    int(metrics["closed"]),
                    old.get(wallet, now),
                    now,
                ),
            )
        conn.commit()

    _sibot.export_rankings(app)
    _write_bridge(chain, len(wallets), len(safe), len(selected), failures)
    print(
        f"[sibot-broader-qualified:{chain.slug}] pool={len(wallets)} "
        f"qualified={len(safe)} selected={len(selected)} thresholds=unchanged"
    )
    return result


def install() -> None:
    if getattr(_sibot, "_broader_qualified_leader_patch_installed", False):
        return
    _sibot.refresh_rankings = refresh_rankings
    _sibot._broader_qualified_leader_patch_installed = True
    print("[sibot-broader-qualified] all_evm=true top20_preserved=true thresholds=unchanged")


install()
