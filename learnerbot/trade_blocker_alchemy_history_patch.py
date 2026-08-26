from __future__ import annotations

import json
import sys
import time
from contextlib import closing
from pathlib import Path

from . import sibot as _sibot
# Install whole-wallet transaction/receipt context fairness only after the base
# Alchemy, rate-limit, internal-trace, retry and trace-progress layers exist.
# This bounds the stage that previously let one high-activity BSC wallet occupy
# the single history worker before later EVM chains received a turn.
from . import sibot_alchemy_context_progress_patch as _context_progress  # noqa: F401
# Install the orphaned legacy-error fallback only after every Alchemy progress
# queue layer has composed its final selector. Ranked/progress work therefore
# always has priority over backlog cleanup.
from . import sibot_legacy_error_sweep_patch as _legacy_sweep  # noqa: F401
# Modern EVM leader wallets frequently trade the canonical wrapped-native asset
# rather than raw ETH/BNB/POL. Reconstruct those proven swaps as history evidence;
# this changes evidence coverage only, never LIVE execution or quality floors.
from . import sibot_wrapped_base_history_patch as _wrapped_base_history  # noqa: F401
# Keep public Top-20 research unchanged while applying the strict leader gates to
# a broader reconstructed pool before scarce LIVE leader slots are filled.
from . import sibot_broader_qualified_leader_patch as _broader_leaders  # noqa: F401
from . import sibot_alchemy_trace_progress_patch as _trace_progress
from . import telegram_trade_blocker_health_patch as _health

_PREV_SNAPSHOT = _health._snapshot
_PREV_BUILD_REPORT = _health.build_report


def _providers(app):
    out = {}
    try:
        chains = _health.load_chains(app, enabled_only=True)
    except Exception:
        return out
    for chain in chains:
        if str(getattr(chain, "type", "EVM") or "EVM").upper() != "EVM":
            continue
        provider_fn = getattr(_sibot, "history_provider", None)
        provider = str(provider_fn(app, chain) if callable(provider_fn) else "MISSING").upper()
        out[str(chain.slug)] = provider
    return out


def _verified_endpoint_pool_active() -> bool:
    """Accept only the audited endpoint-pool wrapper around trace/progressive history.

    Do not import the endpoint-pool module here: this health module must verify the
    already-composed runtime, not change composition merely by checking it.
    """
    active = _sibot.refresh_wallet_history
    if getattr(active, "__module__", "") != "learnerbot.sibot_alchemy_endpoint_pool_patch":
        return False
    if getattr(active, "__name__", "") != "refresh_wallet_history_with_endpoint_pool":
        return False
    module = sys.modules.get("learnerbot.sibot_alchemy_endpoint_pool_patch")
    if module is None:
        return False
    return getattr(module, "_PREV_REFRESH", None) is _trace_progress.refresh_wallet_history


def _assert_alchemy_runtime() -> None:
    """Fail startup unless the final history path remains progressive Alchemy.

    A bounded endpoint-pool wrapper is allowed only when its captured inner function
    is exactly the audited trace/progressive refresher. Arbitrary wrappers or legacy
    history implementations remain rejected.
    """
    if _sibot.refresh_wallet_history is _trace_progress.refresh_wallet_history:
        return
    if _verified_endpoint_pool_active():
        return
    active = getattr(_sibot.refresh_wallet_history, "__module__", "unknown")
    raise RuntimeError(
        "EVM history runtime invariant failed: final refresh is not the Alchemy "
        f"trace/progressive path or its verified endpoint-pool wrapper (active={active})"
    )


def _provider_error_truth(app, chain) -> dict:
    """Separate every stale Etherscan-origin row from current Alchemy failures.

    Pre-Alchemy history can contain several different Etherscan strings: missing
    key, invalid key, NOTOK, or chain-plan access errors. None is a current provider
    failure once the runtime is pinned to Alchemy. Keep those rows visible as
    migration backlog while only non-Etherscan failures count as active errors.
    """
    out = {"legacy": 0, "current": 0, "dominant": ""}
    try:
        with closing(_sibot.connect(app)) as conn:
            row = conn.execute(
                """SELECT
                       SUM(CASE WHEN lower(COALESCE(error,'')) LIKE '%etherscan%' THEN 1 ELSE 0 END) legacy,
                       SUM(CASE WHEN COALESCE(error,'')<>'' AND lower(error) NOT LIKE '%etherscan%' THEN 1 ELSE 0 END) current
                   FROM wallet_history_status WHERE chain_id=?""",
                (int(chain.chain_id),),
            ).fetchone()
            if row:
                out["legacy"] = int(row["legacy"] or 0)
                out["current"] = int(row["current"] or 0)
            dominant = conn.execute(
                """SELECT error,COUNT(*) n FROM wallet_history_status
                   WHERE chain_id=?
                     AND COALESCE(error,'')<>''
                     AND lower(error) NOT LIKE '%etherscan%'
                   GROUP BY error ORDER BY n DESC LIMIT 1""",
                (int(chain.chain_id),),
            ).fetchone()
            if dominant:
                out["dominant"] = str(dominant["error"] or "")[:180]
    except Exception as exc:
        out["dominant"] = f"{type(exc).__name__}: {str(exc)[:130]}"
    return out


def _snapshot(app, tid):
    result = _PREV_SNAPSHOT(app, tid)
    providers = _providers(app)
    ready = bool(providers) and all(value == "ALCHEMY" for value in providers.values())
    result["evm_history_providers"] = providers
    result["evm_history_ready"] = ready
    # Legacy field retained for compatibility only. Runtime provider readiness is
    # evm_history_ready/evm_history_providers and is exclusively Alchemy-based.
    result["etherscan_configured"] = bool(str(getattr(app, "etherscan_api_key", "") or "").strip())

    try:
        for chain in _health.load_chains(app, enabled_only=True):
            if str(getattr(chain, "type", "EVM") or "EVM").upper() != "EVM":
                continue
            slug = str(chain.slug)
            row = (result.get("evm") or {}).get(slug)
            if not isinstance(row, dict):
                continue
            truth = _provider_error_truth(app, chain)
            row["legacy_errors"] = truth["legacy"]
            row["current_provider_errors"] = truth["current"]
            row["errors"] = truth["current"]
            if truth["dominant"]:
                row["dominant"] = truth["dominant"]
            elif truth["legacy"]:
                row["dominant"] = (
                    f"legacy Etherscan history backlog: {truth['legacy']} wallet(s) queued for Alchemy refresh"
                )
            else:
                row["dominant"] = ""
    except Exception:
        pass
    return result


def build_report(app, tid) -> str:
    if not hasattr(app, "csv_dir"):
        return _PREV_BUILD_REPORT(app, tid)

    text = _PREV_BUILD_REPORT(app, tid)
    providers = _providers(app)
    ready = bool(providers) and all(value == "ALCHEMY" for value in providers.values())
    if ready:
        replacement = "🟢 EVM history provider: <b>ALCHEMY RPC</b>"
    else:
        missing = ", ".join(slug.upper() for slug, provider in providers.items() if provider != "ALCHEMY") or "enabled EVM chains"
        replacement = f"🔴 Alchemy history endpoint missing: <b>{missing}</b>"
    text = text.replace("🟢 EVM history dependency: <b>Etherscan key configured</b>", replacement)
    text = text.replace("🔴 EVM history dependency: <b>ETHERSCAN_API_KEY MISSING</b>", replacement)
    text = text.replace("\n   SiBot cannot verify EVM leader histories until the VPS secret is configured.", "")
    return text


def _publish_startup_health(app):
    masters = [
        str(user.get("telegram_id") or "")
        for user in _health.all_users(app.csv_dir, enabled_only=True)
        if str(user.get("role") or "").upper() == "MASTER" and str(user.get("telegram_id") or "")
    ]
    tid = masters[0] if masters else ""
    snapshot = _snapshot(app, tid) if tid else {
        "generated_epoch": int(time.time()),
        "polygon_focus": bool(_health._polygon.focus_enabled(app)),
        "evm_history_providers": _providers(app),
    }
    providers = snapshot.get("evm_history_providers") or _providers(app)
    ready = bool(providers) and all(provider == "ALCHEMY" for provider in providers.values())
    safe = {
        "generated_epoch": snapshot.get("generated_epoch"),
        "evm_history_ready": ready,
        "evm_history_provider": "ALCHEMY" if ready else "MISSING",
        "evm_history_providers": providers,
        "etherscan_configured": bool(str(getattr(app, "etherscan_api_key", "") or "").strip()),
        "polygon_focus": snapshot.get("polygon_focus"),
        "platform_auto": snapshot.get("platform_auto"),
        "platform_live": snapshot.get("platform_live"),
        "evm": snapshot.get("evm", {}),
        "fast_market": snapshot.get("fast_market", {}),
    }
    path = Path(app.data_dir) / "trade_blocker_health.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(safe, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(
        "[trade-blocker-health] evm_history=%s polygon_focus=%s"
        % ("ALCHEMY" if ready else "MISSING", safe["polygon_focus"])
    )
    if not ready and masters and app.telegram_bot_token:
        marker = Path(app.data_dir) / ".evm_history_dependency_warning_epoch"
        last = _health._epoch(marker.read_text(encoding="utf-8").strip()) if marker.exists() else 0
        if int(time.time()) - last >= 12 * 3600:
            missing = ", ".join(slug.upper() for slug, provider in providers.items() if provider != "ALCHEMY") or "enabled EVM chains"
            for master_tid in masters:
                try:
                    _health.send_message(
                        app.telegram_bot_token,
                        master_tid,
                        "🔴 <b>EVM SiBot Alchemy history blocked</b>\n"
                        f"No complete Alchemy HTTP endpoint is configured for <b>{missing}</b> in "
                        "<code>rpc_endpoints.csv</code>. Use <code>/whynotrade</code> for the funnel.",
                        parse_mode="HTML",
                        protect_content=True,
                    )
                except Exception:
                    pass
            marker.write_text(str(int(time.time())) + "\n", encoding="utf-8")

    _health._maybe_alert_platform_gate_off(app, masters, safe)


def install():
    if getattr(_health, "_alchemy_history_health_patch_installed", False):
        return
    _assert_alchemy_runtime()
    _health._snapshot = _snapshot
    _health.build_report = build_report
    _health._publish_startup_health = _publish_startup_health
    _health._alchemy_history_health_patch_installed = True
    active = (
        "endpoint_pool>trace_progress"
        if _verified_endpoint_pool_active()
        else "trace_progress"
    )
    print(f"[sibot-alchemy-runtime] final_refresh={active} legacy_etherscan_runtime=false")


install()
