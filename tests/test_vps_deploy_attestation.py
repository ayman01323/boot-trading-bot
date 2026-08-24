from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "vps_deploy_attestation.py"
    spec = importlib.util.spec_from_file_location("vps_deploy_attestation", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_status_parser_fails_closed():
    mod = _module()
    assert mod._status_ok("service active (running)") is True
    assert mod._status_ok("Active: active (running) since Mon 2026-08-24") is True
    assert mod._status_ok("service inactive (dead)") is False
    assert mod._status_ok("Active: activating (start)") is False
    assert mod._status_ok("Active: deactivating (stop-sigterm)") is False
    assert mod._status_ok("Unit learnerbot.service could not be found.") is False
    assert mod._status_ok("FAILED") is False
    assert mod._status_ok("") is False


def test_attestation_detects_exact_git_sha_and_code_commands(tmp_path, monkeypatch):
    mod = _module()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    (repo / "x.txt").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "x.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "x"], check=True)
    sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

    # The attestation script is loaded from the current checkout, which remains on
    # Python's import path while this temporary git repository is used solely to prove
    # exact SHA matching. Therefore the current checkout's AI command definitions are
    # expected to be visible and verifiable at code level in this unit test.
    status = tmp_path / "status.txt"
    status.write_text("service active (running)\n")
    out = mod.build_attestation(repo, sha, status)
    assert out["sha_match"] is True
    assert out["service_status_ok"] is True
    assert out["ai_master_commands_code_ok"] is True
    assert out["deployment_attested"] is True
