"""A fixed 30-case, two-repeat comparison; no corpus commands are executed."""

import hashlib
import json
import math
import random
import time
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from jevk5.prompt import decision_options, messages

from evaluate import QUESTION, latency

URL = "http://192.168.1.102:8000"
MODEL = "qwen3.8-27b-mxfp4"
# Ten cases per class, chosen before Qwen inference. Includes known JevK5 failures
# and ordinary controls: this is a diagnostic subset, not an unbiased holdout.
IDS = [21, 25, 28, 31, 39, 42, 50, 51, 54, 125,
       61, 63, 77, 83, 93, 98, 99, 122, 124, 130,
       101, 103, 104, 106, 107, 110, 111, 112, 119, 120]


def request(path, body=None):
    req = urllib.request.Request(
        URL + path, data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        return json.load(response)


def score(command, salt, letter_ids):
    question = QUESTION["effect"]
    body = {
        "model": MODEL,
        "messages": messages(command, question["instructions"],
                             [text for _, text in decision_options(question)]),
        "chat_template_kwargs": {"enable_thinking": False},
        "temperature": 0, "max_tokens": 1, "logprobs": True,
        "logprob_token_ids": letter_ids, "cache_salt": salt,
        "return_token_ids": True, "stream": False,
    }
    started = time.perf_counter()
    response = request("/v1/chat/completions", body)
    elapsed_ms = 1000 * (time.perf_counter() - started)
    assert response["model"] == MODEL
    choice = response["choices"][0]
    assert not choice["message"].get("reasoning"), "Thinking was not disabled"
    top = choice["logprobs"]["content"][0]["top_logprobs"]
    seen = {item["token"]: item["logprob"] for item in top}
    assert all(letter in seen and math.isfinite(seen[letter]) for letter in "ABC")
    values = [seen[letter] for letter in "ABC"]
    weights = [math.exp(value - max(values)) for value in values]
    # Qwen is not the calibrated JevK5 checkpoint. Use uncalibrated option softmax.
    probabilities = {key: weight / sum(weights)
                     for key, weight in zip(question["criteria"], weights, strict=True)}
    assert response["usage"]["completion_tokens"] == 1
    assert response["usage"].get("completion_tokens_details", {}).get("reasoning_tokens", 0) == 0
    return {
        "prediction": max(probabilities, key=probabilities.get),
        "probabilities": probabilities, "elapsed_ms": elapsed_ms,
        "usage": response["usage"], "transport": response, "request": body,
    }


def summarize(samples):
    first = [row for row in samples if row["repeat"] == 0]
    assert len(first) == 30 and len(samples) == 60
    assert {(r["id"], r["repeat"]) for r in samples} == {(i, n) for i in IDS for n in (0, 1)}
    matrix = Counter((r["gold"], r["prediction"]) for r in first)
    return {
        "correct": sum(r["gold"] == r["prediction"] for r in first),
        "cases": len(first),
        "confusion": {gold: {label: matrix[gold, label] for label in QUESTION["effect"]["criteria"]}
                      for gold in QUESTION["effect"]["criteria"]},
        "ordinary_latency": latency([r["elapsed_ms"] for r in samples if r["category"] != "long"]),
        "all_latency": latency([r["elapsed_ms"] for r in samples]),
        "long_latency": latency([r["elapsed_ms"] for r in samples if r["category"] == "long"]),
        "unstable": [r["id"] for r in first if any(s["prediction"] != r["prediction"]
                                                   for s in samples if s["id"] == r["id"])],
        "mismatches": [{k: r[k] for k in ("id", "command", "gold", "prediction", "probabilities")}
                       for r in first if r["gold"] != r["prediction"]],
    }


def main(check_only=False):
    source = Path("/results/lemonade-jevk5-9b-q8-vulkan.jsonl")
    records = [json.loads(line) for line in source.read_text().splitlines()]
    assert records[-1]["type"] == "summary" and records[0]["model"] == "alibiserikbay/JevK5-GGUF"
    assert records[0]["options"]["health"]["model_loaded"] == "Shell-Eval-JevK5-9B-Q8"
    assert records[0]["question"] == QUESTION
    baseline = [r for r in records if r["type"] == "sample" and r["id"] in IDS and r["repeat"] < 2]
    cases = sorted((r for r in baseline if r["repeat"] == 0), key=lambda r: r["id"])
    assert len(cases) == len(set(IDS)) == 30
    assert Counter(r["gold"] for r in cases) == {"read": 10, "write": 10, "unknown": 10}

    directory = Path("/results/qwen-vs-jevk5")
    output = directory / "qwen-quick.jsonl"
    if check_only:
        saved = [json.loads(line) for line in output.read_text().splitlines()]
        assert saved[0]["case_ids"] == IDS
        assert saved[0]["baseline_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        rows = [r for r in saved if r["type"] == "sample"]
        assert saved[-1]["qwen"] == summarize(rows)
        assert saved[-1]["jevk5_9b"] == summarize(baseline)
        assert len({r["request"]["cache_salt"] for r in rows}) == 60
        for row in rows:
            original = next(r for r in cases if r["id"] == row["id"])
            assert (row["command"], row["gold"]) == (original["command"], original["gold"])
            assert row["request"]["chat_template_kwargs"] == {"enable_thinking": False}
            assert row["transport"]["usage"]["completion_tokens"] == 1
        print(json.dumps({"verified": True, "cases": 30, "qwen_samples": len(rows),
                          "jevk5_samples": len(baseline),
                          "paired_cases": [{"id": r["id"], "command": r["command"], "gold": r["gold"],
                                            "qwen": r["prediction"],
                                            "jevk5": next(b["prediction"] for b in baseline if b["id"] == r["id"])}
                                           for r in rows if r["repeat"] == 0]}, indent=2))
        return

    catalog = request("/v1/models")
    assert any(m["id"] == MODEL for m in catalog["data"])
    properties = request("/openapi.json")["components"]["schemas"]["ChatCompletionRequest"]["properties"]
    assert "cache_salt" in properties and "logprob_token_ids" in properties
    letter_ids = []
    for letter in "ABC":
        tokens = request("/tokenize", {"model": MODEL, "prompt": letter, "add_special_tokens": False})["tokens"]
        assert len(tokens) == 1
        letter_ids.extend(tokens)
    timestamp = datetime.now(UTC).isoformat()
    directory.mkdir(exist_ok=True)
    samples = []
    with output.open("x") as log:
        def emit(record):
            log.write(json.dumps(record) + "\n")
            log.flush()

        emit({"type": "metadata", "timestamp": timestamp, "endpoint": URL,
              "model": MODEL, "catalog": catalog, "server_version": request("/version"),
              "case_ids": IDS, "repeats": 2, "question": QUESTION,
              "letter_token_ids": letter_ids, "calibration_temperature": 1.0,
              "baseline": source.name, "baseline_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "cache_policy": "Unique vLLM cache_salt per request, including repeats"})
        warm = score("pwd", f"shell-quick-{timestamp}-warmup", letter_ids)
        emit({"type": "warmup", **warm})
        print(f"Warm-up: {warm['prediction']}, {warm['elapsed_ms']:.0f} ms", flush=True)
        for repeat in (0, 1):
            ordered = list(cases)
            random.Random(20261003 + repeat).shuffle(ordered)
            for case in ordered:
                result = score(case["command"], f"shell-quick-{timestamp}-{repeat}-{case['id']}", letter_ids)
                row = {k: case[k] for k in ("id", "command", "gold", "category", "parser")}
                row.update(type="sample", repeat=repeat, **result)
                samples.append(row)
                emit(row)
                print(f"{repeat + 1}/2 case {case['id']}: {row['prediction']} "
                      f"(expected {row['gold']}), {row['elapsed_ms']:.0f} ms", flush=True)
        comparison = {"type": "summary", "qwen": summarize(samples), "jevk5_9b": summarize(baseline),
                      "qwen_measured_seconds": sum(r["elapsed_ms"] for r in samples) / 1000,
                      "fingerprints": sorted({r["transport"]["system_fingerprint"] for r in samples})}
        emit(comparison)
    output.with_suffix(".summary.json").write_text(json.dumps(comparison, indent=2) + "\n")
    print(json.dumps(comparison, indent=2), flush=True)
