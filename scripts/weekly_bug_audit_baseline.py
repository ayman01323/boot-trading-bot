from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable


TEXT_EXTENSIONS = {".py", ".yml", ".yaml", ".json", ".toml", ".md", ".txt", ".ini", ".cfg", ".csv"}
PATTERNS = {
    "broad_exception_pass": re.compile(r"except\s+Exception(?:\s+as\s+\w+)?\s*:\s*(?:#.*\n\s*)?pass\b", re.S),
    "bare_except": re.compile(r"(?m)^\s*except\s*:\s*$"),
    "shell_true": re.compile(r"shell\s*=\s*True"),
    "tls_verify_false": re.compile(r"verify\s*=\s*False"),
    "dynamic_eval": re.compile(r"\beval\s*\("),
    "dynamic_exec": re.compile(r"\bexec\s*\("),
    "todo_fixme": re.compile(r"\b(?:TODO|FIXME|HACK|XXX)\b", re.I),
}


def _git_lines(*args: str) -> list[str]:
    proc = subprocess.run(["git", *args], text=True, capture_output=True, check=True)
    return [line for line in proc.stdout.splitlines() if line.strip()]


def tracked_files(root: Path) -> list[Path]:
    out = []
    for rel in _git_lines("ls-files"):
        path = root / rel
        if path.is_file():
            out.append(path)
    return out


def _safe_text(path: Path, limit: int = 2_000_000) -> str:
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def python_ast_findings(root: Path, files: Iterable[Path]) -> dict:
    syntax_errors = []
    duplicate_top_level = []
    broad_handlers = []
    python_files = 0
    for path in files:
        if path.suffix != ".py":
            continue
        python_files += 1
        rel = path.relative_to(root).as_posix()
        text = _safe_text(path)
        if not text:
            continue
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError as exc:
            syntax_errors.append({"file": rel, "line": int(exc.lineno or 0), "message": str(exc.msg or "syntax error")[:200]})
            continue
        names = Counter()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names[node.name] += 1
        for name, count in names.items():
            if count > 1:
                duplicate_top_level.append({"file": rel, "symbol": name, "count": count})
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            broad = node.type is None or (
                isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}
            )
            if broad:
                broad_handlers.append({"file": rel, "line": int(getattr(node, "lineno", 0) or 0), "bare": node.type is None})
    return {
        "python_files": python_files,
        "syntax_errors": syntax_errors[:200],
        "duplicate_top_level_symbols": duplicate_top_level[:200],
        "broad_exception_handlers": broad_handlers[:500],
    }


def pattern_findings(root: Path, files: Iterable[Path]) -> dict:
    by_pattern: dict[str, list[dict]] = {name: [] for name in PATTERNS}
    for path in files:
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        rel = path.relative_to(root).as_posix()
        # Do not inspect tracked files that are likely to contain generated/runtime data.
        if rel.startswith(("data/", "runtime/", "bot_db/")):
            continue
        text = _safe_text(path)
        if not text:
            continue
        for name, pattern in PATTERNS.items():
            count = len(pattern.findall(text))
            if count:
                by_pattern[name].append({"file": rel, "count": count})
    return {name: rows[:200] for name, rows in by_pattern.items()}


def workflow_inventory(root: Path, files: Iterable[Path]) -> dict:
    workflows = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        if not rel.startswith(".github/workflows/") or path.suffix.lower() not in {".yml", ".yaml", ".md"}:
            continue
        text = _safe_text(path)
        workflows.append({
            "file": rel,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "has_schedule": bool(re.search(r"(?m)^\s*schedule\s*:", text)),
            "has_workflow_dispatch": "workflow_dispatch" in text,
            "mentions_secrets": "secrets." in text,
            "mentions_deploy": bool(re.search(r"deploy|systemctl|self-hosted", text, re.I)),
        })
    return {"count": len(workflows), "workflows": workflows}


def _read_log(path: str | None, max_chars: int = 16_000) -> dict:
    if not path:
        return {"available": False}
    p = Path(path)
    if not p.exists():
        return {"available": False, "path": str(p)}
    text = p.read_text(encoding="utf-8", errors="replace")
    # Test logs can contain source snippets or addresses. Keep only a bounded tail.
    return {
        "available": True,
        "path": str(p),
        "bytes": len(text.encode("utf-8", errors="ignore")),
        "tail": text[-max_chars:],
    }


def build(root: Path, *, compile_log: str | None, pytest_log: str | None, compile_rc: int, pytest_rc: int) -> dict:
    files = tracked_files(root)
    sizes = sorted(
        ({"file": p.relative_to(root).as_posix(), "bytes": p.stat().st_size} for p in files),
        key=lambda x: x["bytes"],
        reverse=True,
    )
    extensions = Counter((p.suffix.lower() or "<none>") for p in files)
    commit = _git_lines("rev-parse", "HEAD")[0]
    branch = _git_lines("rev-parse", "--abbrev-ref", "HEAD")[0]
    return {
        "schema_version": 1,
        "scope": "FULL_REPOSITORY_BUG_AUDIT_BASELINE",
        "source_commit": commit,
        "source_branch": branch,
        "tracked_file_count": len(files),
        "tracked_bytes": sum(p.stat().st_size for p in files),
        "extensions": dict(extensions),
        "largest_tracked_files": sizes[:50],
        "python_ast": python_ast_findings(root, files),
        "pattern_scan": pattern_findings(root, files),
        "workflow_inventory": workflow_inventory(root, files),
        "test_baseline": {
            "compile_return_code": int(compile_rc),
            "pytest_return_code": int(pytest_rc),
            "compile": _read_log(compile_log),
            "pytest": _read_log(pytest_log),
        },
        "privacy": {
            "tracked_files_only": True,
            "source_contents_not_embedded_except_bounded_test_log_tail": True,
            "secret_values_intentionally_not_collected": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument("--compile-log")
    parser.add_argument("--pytest-log")
    parser.add_argument("--compile-rc", type=int, default=0)
    parser.add_argument("--pytest-rc", type=int, default=0)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    payload = build(
        root,
        compile_log=args.compile_log,
        pytest_log=args.pytest_log,
        compile_rc=args.compile_rc,
        pytest_rc=args.pytest_rc,
    )
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "source_commit": payload["source_commit"],
        "tracked_file_count": payload["tracked_file_count"],
        "compile_rc": payload["test_baseline"]["compile_return_code"],
        "pytest_rc": payload["test_baseline"]["pytest_return_code"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
