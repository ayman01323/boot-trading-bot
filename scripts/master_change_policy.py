from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
from pathlib import Path

from learnerbot import ai_cost_grok_patch as _grok_cost  # noqa: F401
from learnerbot.ai_cost_router import ALL_ADVISERS, master_change_route

# Schema-v1 evidence predates Grok. Keep those already-created requests bound to
# the original four-adviser contract instead of retroactively requiring a fifth
# adviser. Schema-v2 cost-routed requests use the current ALL_ADVISERS set.
LEGACY_ADVISERS = ("claude", "gemini", "deepseek", "copilot")

GOVERNANCE_FILES = frozenset({
    ".github/workflows/ai-cost-router-ci.yml",
    ".github/workflows/gpt-master-change-implement.yml",
    ".github/workflows/publish-ai-master-control.yml",
    ".github/workflows/master-change-council-protected-deploy.yml",
    "learnerbot/master_change_council.py",
    "learnerbot/master_change_cost_router_patch.py",
    "learnerbot/strategy_factory_council_transport_patch.py",
    "learnerbot/ai_cost_router.py",
    "learnerbot/ai_cost_grok_patch.py",
    "learnerbot/ai_cost_provider_patch.py",
    "learnerbot/grok_provider.py",
    "learnerbot/telegram_grok_council_patch.py",
    "learnerbot/telegram_master_change_patch.py",
    "learnerbot/ai_agent_ws_runtime_patch.py",
    "scripts/ai_agent_ws_bus.py",
    "scripts/ai_agent_ws_bus_grok.py",
    "scripts/ai_agent_ws_worker.py",
    "scripts/ai_agent_ws_send.py",
    "scripts/strategy_factory_transport.py",
    "scripts/strategy_factory_mcp_core.py",
    "scripts/strategy_factory_mcp_bridge.py",
    "scripts/master_change_policy.py",
    "tests/test_master_change_council.py",
    "tests/test_ai_cost_router.py",
    "tests/test_grok_sixth_agent.py",
    "tests/test_strategy_factory_transport.py",
    "tests/test_strategy_factory_mcp_bridge.py",
})


def normalise_path(value: object) -> str:
    path = str(value or "").replace("\\", "/").strip().lstrip("/")
    if not path or "*" in path or ".." in path.split("/") or len(path) > 300:
        raise ValueError(f"invalid repository path: {value!r}")
    return path


def load_request(path: str | Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("request evidence must be a JSON object")
    return value


def _required_advisers(evidence: dict) -> tuple[str, ...]:
    schema = int(evidence.get("schema_version") or 1)
    if schema < 2:
        return LEGACY_ADVISERS
    if not evidence.get("cost_route"):
        return tuple(ALL_ADVISERS)

    expected = master_change_route(
        str(evidence.get("request") or ""),
        hard_protected_reasons=list(evidence.get("hard_protected_reasons") or []),
        protected_reasons=list(evidence.get("protected_reasons") or []),
    )
    supplied = evidence.get("cost_route") or {}
    supplied_level = int(supplied.get("level") if supplied.get("level") is not None else -1)
    if supplied_level != int(expected["level"]):
        raise ValueError(f"cost route level mismatch: supplied={supplied_level} expected={expected['level']}")

    required = tuple(str(x) for x in (evidence.get("required_advisers") or supplied.get("advisers") or []))
    expected_required = tuple(str(x) for x in expected.get("advisers") or [])
    if required != expected_required:
        raise ValueError(
            "cost route adviser mismatch: supplied=" + ",".join(required) + " expected=" + ",".join(expected_required)
        )
    if any(name not in ALL_ADVISERS for name in required):
        raise ValueError("cost route contains an unsupported adviser")
    return required


def validate_request(evidence: dict, *, request_id: str, nonce: int, current_sha: str) -> list[str]:
    if evidence.get("request_id") != request_id:
        raise ValueError("request_id mismatch")
    if int(evidence.get("implementation_nonce") or 0) != int(nonce):
        raise ValueError("implementation nonce mismatch")
    if not evidence.get("implementation_allowed"):
        raise ValueError("implementation is not authorised by the published deterministic gate")
    if evidence.get("hard_protected_reasons"):
        raise ValueError("hard-protected request cannot be implemented by this lane")
    if not evidence.get("all_advisers_replied"):
        raise ValueError("all required adviser replies are required")

    required = _required_advisers(evidence)
    if not required:
        raise ValueError("repository change must have at least one required adviser")
    advisers = evidence.get("advisers") or {}
    for name in required:
        row = advisers.get(name) or {}
        rc = int(row.get("provider_rc") if row.get("provider_rc") is not None else 1)
        if not row.get("acknowledged") or rc != 0 or not str(row.get("reply") or "").strip():
            raise ValueError(f"{name} required adviser did not complete successfully")

    decision = evidence.get("gpt_decision") or {}
    if str(decision.get("action") or "").upper() != "IMPLEMENT":
        raise ValueError("GPT final decision is not IMPLEMENT")
    raw_allowed = decision.get("allowed_files") or []
    if not raw_allowed or len(raw_allowed) > 20:
        raise ValueError("invalid allowed_files")
    allowed: list[str] = []
    for value in raw_allowed:
        path = normalise_path(value)
        if path not in allowed:
            allowed.append(path)
    forbidden = sorted(set(allowed) & GOVERNANCE_FILES)
    if forbidden:
        raise ValueError(
            "MASTER council cannot authorise modification of its own governance/transport files: "
            + ", ".join(forbidden)
        )
    source = str(evidence.get("source_sha") or "")
    if source != current_sha:
        raise ValueError(
            f"stale council evidence: request source={source} current main={current_sha}; "
            "submit/retry against current main"
        )
    return allowed


def git_changed_paths(repo: str | Path = ".") -> list[str]:
    raw = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True)
    changed: list[str] = []
    for line in raw.splitlines():
        path = line[3:].strip().replace("\\", "/")
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            changed.append(path)
    return changed


def validate_changed_paths(changed: list[str], allowed: list[str]) -> list[str]:
    if not changed:
        raise ValueError("GPT produced no tracked change")
    allowed_set = set(allowed)
    outside = [path for path in changed if path not in allowed_set]
    if outside:
        raise ValueError("GPT changed paths outside the adjudicated allow-list: " + ", ".join(outside))
    hard: list[str] = []
    for path in changed:
        low = path.lower()
        if path in GOVERNANCE_FILES:
            hard.append(path)
        if low.startswith(".env") or "/.env" in low or "private_key" in low or "mnemonic" in low or "seed_phrase" in low:
            hard.append(path)
        if low == "scripts/install_github_deployer.sh":
            hard.append(path)
    if hard:
        raise ValueError("Hard-protected paths cannot be changed by the MASTER council lane: " + ", ".join(sorted(set(hard))))
    return changed


def _safe_auto_merge_path(path: str) -> bool:
    if path in GOVERNANCE_FILES:
        return False
    low = path.lower()
    name = pathlib.PurePosixPath(path).name.lower()
    if low.startswith("docs/") or low.endswith(".md"):
        return True
    if low.startswith("tests/"):
        return True
    if low.startswith("learnerbot/telegram_"):
        return True
    if low.startswith("learnerbot/") and ("report" in name or "status" in name):
        return True
    if low.startswith("scripts/") and ("report" in name or "status" in name):
        return True
    return False


def auto_merge_eligible(evidence: dict, changed: list[str]) -> bool:
    decision = evidence.get("gpt_decision") or {}
    non_test = [path for path in changed if not path.lower().startswith("tests/")]
    return bool(
        evidence.get("auto_merge_allowed")
        and str(decision.get("risk_class") or "").upper() == "LOW"
        and not evidence.get("protected_reasons")
        and non_test
        and changed
        and all(_safe_auto_merge_path(path) for path in changed)
    )


def _current_sha(repo: str | Path = ".") -> str:
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic policy gate for GPT MASTER changes")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("validate-request")
    p.add_argument("--evidence", required=True)
    p.add_argument("--request-id", required=True)
    p.add_argument("--nonce", required=True, type=int)
    p.add_argument("--allowed-out", required=True)

    p = sub.add_parser("validate-changed")
    p.add_argument("--allowed", required=True)
    p.add_argument("--changed-out", required=True)

    p = sub.add_parser("auto-merge")
    p.add_argument("--evidence", required=True)
    p.add_argument("--changed", required=True)

    args = parser.parse_args()
    if args.cmd == "validate-request":
        evidence = load_request(args.evidence)
        allowed = validate_request(evidence, request_id=args.request_id, nonce=args.nonce, current_sha=_current_sha())
        Path(args.allowed_out).write_text("\n".join(allowed) + "\n", encoding="utf-8")
        return 0
    if args.cmd == "validate-changed":
        allowed = [line.strip() for line in Path(args.allowed).read_text(encoding="utf-8").splitlines() if line.strip()]
        changed = validate_changed_paths(git_changed_paths(), allowed)
        Path(args.changed_out).write_text("\n".join(changed) + "\n", encoding="utf-8")
        return 0
    if args.cmd == "auto-merge":
        evidence = load_request(args.evidence)
        changed = [line.strip() for line in Path(args.changed).read_text(encoding="utf-8").splitlines() if line.strip()]
        print("true" if auto_merge_eligible(evidence, changed) else "false")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
