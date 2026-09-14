import argparse
import json

import pytest

from mato_loop_backend.coding_eval import comparison_payload, load_arm, run


def test_comparison_persists_provenance_metrics_and_unknown_quality(tmp_path) -> None:
    baseline_path = tmp_path / "opencode.json"
    baseline_path.write_text(json.dumps({
        "info": {
            "id": "session-a", "directory": "/work/a",
            "model": {"providerID": "openai", "id": "model"},
            "time": {"created": 1_000, "updated": 6_000},
            "tokens": {"input": 100, "output": 20, "cache": {"read": 50}},
        },
        "messages": [{"info": {"role": "user"}}, {"info": {"role": "assistant"}}],
    }))
    candidate_path = tmp_path / "matoloop.json"
    candidate_path.write_text(json.dumps({
        "startedAt": "2026-09-14T10:00:00+00:00",
        "finishedAt": "2026-09-14T10:00:07+00:00",
        "sessionId": "session-b", "modelId": "model", "workspace": "/work/b",
        "runs": [{"finishedAt": "now", "approvals": [{"decision": "allow_once"}]}],
    }))

    payload = comparison_payload(
        load_arm(baseline_path, "OpenCode"), load_arm(candidate_path, "MatoLoop")
    )

    assert payload["deltas"] == {"elapsedSeconds": 2.0, "requestedTurns": 0}
    assert payload["qualityComparison"] == "unknown"
    assert len(payload["baseline"]["source"]["sha256"]) == 64
    assert payload["candidate"]["metrics"]["approvals"] == 1

    output = tmp_path / "reports/comparison.json"
    assert run(argparse.Namespace(
        baseline=baseline_path, candidate=candidate_path,
        baseline_label="OpenCode", candidate_label="MatoLoop",
        output=output, report=None,
    )) == 0
    assert json.loads(output.read_text())["candidate"]["runId"] == "session-b"
    assert "No quality winner" in output.with_suffix(".md").read_text()


def test_comparison_rejects_unknown_artifact(tmp_path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("{}")

    with pytest.raises(ValueError, match="not a supported"):
        load_arm(path, "invalid")
