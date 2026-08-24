from pathlib import Path


def _workflow_text() -> str:
    return (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy-vps.yml").read_text(encoding="utf-8")


def test_deploy_workflow_never_cancels_in_progress_vps_deploy():
    text = _workflow_text()
    assert "group: boot-vps-deploy" in text
    assert "cancel-in-progress: false" in text


def test_watcher_log_marker_is_observability_not_deployment_gate():
    text = _workflow_text()
    deployment_line = next(line for line in text.splitlines() if "deployment_ok=" in line)
    assert "watcher_ok" not in deployment_line
    assert "sha_ok" in deployment_line
    assert "service_ok" in deployment_line
    assert "deploy_outcome=='success'" in deployment_line
