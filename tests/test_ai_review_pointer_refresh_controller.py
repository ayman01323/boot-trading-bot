from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pointer_refresh_dispatches_canonical_producers():
    text = (ROOT / ".github/workflows/ai-review-pointer-refresh.yml").read_text()
    assert "weekly-three-agent-bug-audit.yml" in text
    assert "hourly-three-agent-strategy-cycle.yml" in text
    assert "gh workflow run three-agent-strategy-review.yml" not in text


def test_clean_strategy_trigger_uses_canonical_cycle_producer():
    text = (ROOT / ".github/workflows/ai-strategy-review-trigger.yml").read_text()
    assert "hourly-three-agent-strategy-cycle.yml" in text
    assert "gh workflow run three-agent-strategy-review.yml" not in text


def test_strategy_cycle_contract_matches_downstream_agents():
    producer = (ROOT / ".github/workflows/hourly-three-agent-strategy-cycle.yml").read_text()
    retry = (ROOT / ".github/workflows/six-agent-health-retry.yml").read_text()
    deepseek = (ROOT / ".github/workflows/deepseek-fifth-strategy-agent.yml").read_text()

    assert 'HOUR_KEY="$(date -u +\'%Y%m%d%H\')"' in producer
    assert 'CYCLE_ID="${SOURCE_SHA:0:12}-${HOUR_KEY}-${EVIDENCE_SHA:0:8}"' in producer
    canonical_regex = "^[0-9a-f]{12}-[0-9]{10}-[0-9a-f]{8}$"
    assert canonical_regex in retry
    assert canonical_regex in deepseek
