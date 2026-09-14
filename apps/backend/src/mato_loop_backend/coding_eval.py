from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _elapsed(start: str, end: str) -> float:
    return round(
        (datetime.fromisoformat(end).astimezone(UTC) - datetime.fromisoformat(start).astimezone(UTC)).total_seconds(),
        3,
    )


def load_arm(path: Path, label: str) -> dict[str, Any]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")

    if isinstance(value.get("runs"), list) and isinstance(value.get("sessionId"), str):
        runs = value["runs"]
        if not all(isinstance(run, dict) for run in runs):
            raise ValueError(f"{path} contains an invalid MatoLoop run list")
        metrics = {
            "elapsedSeconds": _elapsed(value["startedAt"], value["finishedAt"]),
            "requestedTurns": len(runs),
            "completedRuns": sum("finishedAt" in run for run in runs),
            "approvals": sum(len(run.get("approvals", [])) for run in runs),
        }
        identity = {
            "format": "matoloop",
            "runId": value["sessionId"],
            "model": value.get("modelId"),
            "workspace": value.get("workspace"),
        }
    elif isinstance(value.get("info"), dict) and isinstance(value.get("messages"), list):
        info = value["info"]
        times = info.get("time", {})
        if not isinstance(info.get("id"), str) or not all(
            isinstance(times.get(key), int) for key in ("created", "updated")
        ):
            raise ValueError(f"{path} contains invalid OpenCode session metadata")
        tokens = info.get("tokens", {})
        cache = tokens.get("cache", {}) if isinstance(tokens, dict) else {}
        metrics = {
            "elapsedSeconds": round((times["updated"] - times["created"]) / 1000, 3),
            "requestedTurns": sum(
                message.get("info", {}).get("role") == "user"
                for message in value["messages"]
                if isinstance(message, dict)
            ),
            "inputTokens": tokens.get("input"),
            "outputTokens": tokens.get("output"),
            "cacheReadTokens": cache.get("read"),
        }
        model = info.get("model", {})
        identity = {
            "format": "opencode",
            "runId": info["id"],
            "model": "/".join(
                str(model[key]) for key in ("providerID", "id") if model.get(key)
            ) or None,
            "workspace": info.get("directory"),
        }
    else:
        raise ValueError(f"{path} is not a supported MatoLoop or OpenCode artifact")

    return {
        "label": label,
        "source": {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()},
        **identity,
        "metrics": {key: value for key, value in metrics.items() if value is not None},
        "checks": value.get("checks") if isinstance(value.get("checks"), dict) else None,
    }


def comparison_payload(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    deltas = {
        key: round(candidate["metrics"][key] - baseline["metrics"][key], 3)
        for key in baseline["metrics"].keys() & candidate["metrics"].keys()
        if isinstance(baseline["metrics"][key], int | float)
        and isinstance(candidate["metrics"][key], int | float)
    }
    comparable_checks = baseline["checks"] is not None and candidate["checks"] is not None
    return {
        "version": 1,
        "generatedAt": datetime.now(UTC).isoformat(),
        "baseline": baseline,
        "candidate": candidate,
        "deltas": deltas,
        "qualityComparison": "available" if comparable_checks else "unknown",
    }


def markdown_report(payload: dict[str, Any]) -> str:
    baseline, candidate = payload["baseline"], payload["candidate"]
    keys = sorted(baseline["metrics"].keys() | candidate["metrics"].keys())
    rows = [
        "# Coding comparison",
        "",
        f"Baseline: `{baseline['label']}` ({baseline['format']}, `{baseline['runId']}`)",
        f"Candidate: `{candidate['label']}` ({candidate['format']}, `{candidate['runId']}`)",
        "",
        "| Metric | Baseline | Candidate | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for key in keys:
        rows.append(
            f"| {key} | {baseline['metrics'].get(key, 'n/a')} | "
            f"{candidate['metrics'].get(key, 'n/a')} | {payload['deltas'].get(key, 'n/a')} |"
        )
    rows.extend([
        "",
        "No quality winner is declared without independent checks for both arms."
        if payload["qualityComparison"] == "unknown"
        else "Independent checks are present for both arms; interpret their recorded results directly.",
        "",
        "Source artifacts are bound by SHA-256 in the JSON report.",
    ])
    return "\n".join(rows) + "\n"


def run(args: argparse.Namespace) -> int:
    payload = comparison_payload(
        load_arm(args.baseline, args.baseline_label),
        load_arm(args.candidate, args.candidate_label),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    report = args.report or args.output.with_suffix(".md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(markdown_report(payload))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Persist a comparison of existing coding-run artifacts")
    result.add_argument("--baseline", type=Path, required=True)
    result.add_argument("--candidate", type=Path, required=True)
    result.add_argument("--baseline-label", default="baseline")
    result.add_argument("--candidate-label", default="candidate")
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--report", type=Path)
    return result


def main() -> None:
    raise SystemExit(run(parser().parse_args()))


if __name__ == "__main__":
    main()
