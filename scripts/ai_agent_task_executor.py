from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

PROTOCOL = "ws-bus-v2"
SAFE_ACTIONS = {
    "READ_FILE",
    "LIST_FILES",
    "SEARCH_CODE",
    "RUN_TESTS",
    "PY_COMPILE",
    "GIT_STATUS",
    "GIT_DIFF",
}
PROTECTED_ACTIONS = {
    "WRITE_FILE",
    "EDIT_FILE",
    "APPLY_PATCH",
    "COMMIT",
    "PUSH",
    "MERGE",
    "DEPLOY",
    "RESTART",
    "TRADE",
    "SET_LIVE",
    "SET_ARMED",
    "CHANGE_RISK",
    "CHANGE_CAPITAL",
    "WALLET",
    "SIGN",
    "READ_SECRET",
}
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".ini",
    ".cfg", ".sh", ".sql", ".csv", ".js", ".ts", ".tsx", ".jsx",
}
BLOCKED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore"}
BLOCKED_PARTS = {".git", ".ssh", "secrets", "credentials", "__pycache__"}
BLOCKED_NAME_RE = re.compile(
    r"(?i)(?:^|[._-])(private[_-]?key|mnemonic|seed(?:[_-]?phrase)?|credentials?|secret(?:s)?)(?:$|[._-])"
)
MAX_READ_CHARS = 64_000
MAX_SEARCH_RESULTS = 50
MAX_LIST_RESULTS = 200


class TaskError(ValueError):
    pass


def _repo_root() -> Path:
    configured = str(os.environ.get("AI_AGENT_TASK_REPO") or "").strip()
    if configured:
        root = Path(configured).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parents[1]
    if not root.is_dir():
        raise TaskError("task repository root is unavailable")
    return root


def _normalise_action(value: Any) -> str:
    action = str(value or "").strip().upper().replace("-", "_")
    if not action:
        raise TaskError("task action is required")
    return action


def _is_sensitive_relative(rel: Path) -> bool:
    parts = {part.lower() for part in rel.parts}
    name = rel.name.lower()
    if any(part.startswith(".env") for part in rel.parts):
        return True
    if parts & BLOCKED_PARTS:
        return True
    if rel.suffix.lower() in BLOCKED_SUFFIXES:
        return True
    if BLOCKED_NAME_RE.search(name):
        return True
    return False


def _safe_path(root: Path, raw: Any, *, must_exist: bool = True) -> tuple[Path, Path]:
    value = str(raw or "").strip().replace("\\", "/")
    if not value or value.startswith("/"):
        raise TaskError("path must be repository-relative")
    rel = Path(value)
    if ".." in rel.parts or _is_sensitive_relative(rel):
        raise TaskError("path is outside the safe task scope")
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise TaskError("path escapes repository root") from exc
    if must_exist and not path.exists():
        raise TaskError(f"path does not exist: {value}")
    return path, rel


def parse_task_envelope(body: str) -> dict[str, Any] | None:
    raw = str(body or "").strip()
    if not raw.startswith("{"):
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    if str(value.get("protocol") or "").strip().lower() != PROTOCOL:
        return None
    if str(value.get("kind") or value.get("type") or "").strip().lower() != "task":
        return None
    action = _normalise_action(value.get("action"))
    args = value.get("args") or {}
    if not isinstance(args, dict):
        raise TaskError("task args must be a JSON object")
    instruction = str(value.get("instruction") or "").strip()
    return {
        "protocol": PROTOCOL,
        "kind": "task",
        "action": action,
        "args": args,
        "instruction": instruction[:4000],
    }


def build_task_envelope(action: str, args: dict[str, Any] | None = None, instruction: str = "") -> str:
    payload = {
        "protocol": PROTOCOL,
        "kind": "task",
        "action": _normalise_action(action),
        "args": args or {},
        "instruction": str(instruction or "")[:4000],
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _result(status: str, action: str, summary: str, *, evidence: dict[str, Any] | None = None, error: str = "") -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "kind": "task_result",
        "status": status,
        "action": action,
        "summary": str(summary or "")[:3000],
        "evidence": evidence or {},
        "error": str(error or "")[:1600],
    }


def _read_file(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    path, rel = _safe_path(root, args.get("path"))
    if not path.is_file():
        raise TaskError("READ_FILE requires a file")
    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > MAX_READ_CHARS
    return {
        "path": rel.as_posix(),
        "content": text[:MAX_READ_CHARS],
        "truncated": truncated,
        "size_bytes": path.stat().st_size,
    }


def _list_files(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    start_raw = args.get("path") or "."
    if str(start_raw).strip() in {"", "."}:
        start, rel_start = root, Path(".")
    else:
        start, rel_start = _safe_path(root, start_raw)
    if not start.is_dir():
        raise TaskError("LIST_FILES requires a directory")
    rows: list[str] = []
    for path in sorted(start.rglob("*")):
        if len(rows) >= MAX_LIST_RESULTS:
            break
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root)
        if _is_sensitive_relative(rel):
            continue
        rows.append(rel.as_posix())
    return {
        "root": rel_start.as_posix(),
        "files": rows,
        "truncated": len(rows) >= MAX_LIST_RESULTS,
    }


def _search_code(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise TaskError("SEARCH_CODE requires query")
    if len(query) > 300:
        raise TaskError("SEARCH_CODE query is too long")
    start_raw = args.get("path") or "."
    if str(start_raw).strip() in {"", "."}:
        start = root
    else:
        start, _ = _safe_path(root, start_raw)
    if not start.is_dir():
        raise TaskError("SEARCH_CODE path must be a directory")
    needle = query.lower()
    matches: list[dict[str, Any]] = []
    for path in sorted(start.rglob("*")):
        if len(matches) >= MAX_SEARCH_RESULTS:
            break
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        rel = path.relative_to(root)
        if _is_sensitive_relative(rel):
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if needle in line.lower():
                    matches.append({
                        "path": rel.as_posix(),
                        "line": line_no,
                        "text": line[:500],
                    })
                    if len(matches) >= MAX_SEARCH_RESULTS:
                        break
        except OSError:
            continue
    return {"query": query, "matches": matches, "truncated": len(matches) >= MAX_SEARCH_RESULTS}


def _test_target(root: Path, raw: Any) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    if not value or value.startswith("-") or " " in value:
        raise TaskError("invalid pytest target")
    path_part = value.split("::", 1)[0]
    path, rel = _safe_path(root, path_part)
    if not path.is_file() or rel.suffix != ".py" or not rel.as_posix().startswith("tests/"):
        raise TaskError("RUN_TESTS targets must be existing Python files under tests/")
    suffix = value[len(path_part):]
    if suffix and not suffix.startswith("::"):
        raise TaskError("invalid pytest node id")
    return rel.as_posix() + suffix


def _run_tests(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    raw_targets = args.get("targets") or []
    if not isinstance(raw_targets, list) or not raw_targets or len(raw_targets) > 12:
        raise TaskError("RUN_TESTS requires 1-12 test targets")
    targets = [_test_target(root, item) for item in raw_targets]
    timeout = max(10, min(int(args.get("timeout_seconds") or 180), 300))
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets]
    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env={
                **os.environ,
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": cmd,
            "returncode": 124,
            "stdout": str(exc.stdout or "")[-6000:],
            "stderr": ("timeout after %ss\n" % timeout) + str(exc.stderr or "")[-3000:],
        }
    return {
        "command": cmd,
        "returncode": int(proc.returncode),
        "stdout": str(proc.stdout or "")[-8000:],
        "stderr": str(proc.stderr or "")[-4000:],
    }


def _py_compile(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    raw_paths = args.get("paths") or []
    if not isinstance(raw_paths, list) or not raw_paths or len(raw_paths) > 20:
        raise TaskError("PY_COMPILE requires 1-20 Python paths")
    compiled: list[str] = []
    for raw in raw_paths:
        path, rel = _safe_path(root, raw)
        if not path.is_file() or path.suffix != ".py":
            raise TaskError("PY_COMPILE accepts Python files only")
        source = path.read_text(encoding="utf-8", errors="strict")
        compile(source, rel.as_posix(), "exec")
        compiled.append(rel.as_posix())
    return {"compiled": compiled}


def _git_status(root: Path) -> dict[str, Any]:
    proc = subprocess.run(
        ["git", "status", "--short", "--branch"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    return {"returncode": int(proc.returncode), "stdout": proc.stdout[-8000:], "stderr": proc.stderr[-2000:]}


def _git_diff(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    raw_paths = args.get("paths") or []
    if raw_paths and (not isinstance(raw_paths, list) or len(raw_paths) > 20):
        raise TaskError("GIT_DIFF paths must be a list of at most 20 entries")
    paths: list[str] = []
    for raw in raw_paths:
        _, rel = _safe_path(root, raw)
        paths.append(rel.as_posix())
    cmd = ["git", "diff", "--"]
    cmd.extend(paths)
    proc = subprocess.run(cmd, cwd=root, text=True, capture_output=True, timeout=30, check=False)
    return {
        "command": cmd,
        "returncode": int(proc.returncode),
        "stdout": proc.stdout[-12000:],
        "stderr": proc.stderr[-2000:],
    }


def execute_task(task: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    action = _normalise_action(task.get("action"))
    args = task.get("args") or {}
    if not isinstance(args, dict):
        return _result("REJECTED", action, "Task arguments must be a JSON object.", error="invalid args")
    if action in PROTECTED_ACTIONS:
        return _result(
            "BLOCKED",
            action,
            "Protected action was not executed. Route repository mutation, deployment, trading, risk, wallet, signing or secrets work through the existing trusted approval path.",
            evidence={"protected": True},
        )
    if action not in SAFE_ACTIONS:
        return _result("REJECTED", action, f"Unsupported task action: {action}", error="unsupported action")
    repo = (root or _repo_root()).resolve()
    try:
        if action == "READ_FILE":
            evidence = _read_file(repo, args)
        elif action == "LIST_FILES":
            evidence = _list_files(repo, args)
        elif action == "SEARCH_CODE":
            evidence = _search_code(repo, args)
        elif action == "RUN_TESTS":
            evidence = _run_tests(repo, args)
            if int(evidence.get("returncode") or 0) != 0:
                return _result("FAILED", action, "Tests completed with failures.", evidence=evidence, error="pytest returned non-zero")
        elif action == "PY_COMPILE":
            evidence = _py_compile(repo, args)
        elif action == "GIT_STATUS":
            evidence = _git_status(repo)
            if int(evidence.get("returncode") or 0) != 0:
                return _result("FAILED", action, "git status failed.", evidence=evidence, error=evidence.get("stderr", ""))
        elif action == "GIT_DIFF":
            evidence = _git_diff(repo, args)
            if int(evidence.get("returncode") or 0) != 0:
                return _result("FAILED", action, "git diff failed.", evidence=evidence, error=evidence.get("stderr", ""))
        else:  # pragma: no cover - SAFE_ACTIONS is exhaustive
            return _result("REJECTED", action, "Unsupported task action.", error="unsupported action")
        return _result("COMPLETED", action, f"{action} completed.", evidence=evidence)
    except (TaskError, OSError, UnicodeError, ValueError, SyntaxError) as exc:
        return _result("FAILED", action, f"{action} failed.", error=f"{type(exc).__name__}: {exc}")
    except subprocess.TimeoutExpired as exc:
        return _result("FAILED", action, f"{action} timed out.", error=f"TimeoutExpired: {exc}")
