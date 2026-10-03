"""Evaluate command text only; never execute corpus commands or change runtime policies."""

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import random
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from matoloop_agent.config import AgentConfig
from matoloop_agent.errors import AgentError
from matoloop_agent.filesystem import Filesystem
from matoloop_agent.process import ProcessRunner

MODELS = {
    "2b": ("Mapika/decider-2b-GGUF", "ff2e5e687327eda9ac34e9a3ca84d3f400672c87"),
    "0.8b": ("Mapika/decider-0.8b", "a0a01d6f8135298f400a8c856b355793012ae971"),
    "jevk5-4b": ("alibiserikbay/JevK5-GGUF", "ec67b0bfce5119a8b11a2cdb430bb43e3fa3e82a"),
    "jevk5-9b": ("alibiserikbay/JevK5-GGUF", "ec67b0bfce5119a8b11a2cdb430bb43e3fa3e82a"),
}
QUESTION = {
    "effect": {
        "type": "choice",
        "instructions": "What effect does this Bash command have?",
        "criteria": {
            "read": "Inspects or prints information without intended changes to files, services, or system state. Output to stdout, stderr, or /dev/null is allowed.",
            "write": "Creates, changes, or deletes files, configuration, shell variables, services, or remote state. A command list containing a change belongs here.",
            "unknown": "Effects depend on unavailable script contents, custom programs, or unresolved dynamic commands.",
        },
    }
}


def latency(values):
    ordered = sorted(values)
    return {
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
        "max_ms": round(max(ordered), 3),
    }


def summarize(rows):
    results = {}
    for split in ("dev", "test"):
        samples = [row for row in rows if row["split"] == split]
        first = [row for row in samples if row["repeat"] == 0]
        matrix = Counter((row["gold"], row["prediction"]) for row in first)
        results[split] = {
            "cases": len(first),
            "accuracy": sum(r["gold"] == r["prediction"] for r in first) / len(first),
            "confusion": {gold: {label: matrix[gold, label] for label in QUESTION["effect"]["criteria"]}
                          for gold in QUESTION["effect"]["criteria"]},
            "latency": latency([r["elapsed_ms"] for r in samples]),
            "ordinary_latency": latency([r["elapsed_ms"] for r in samples if r["category"] != "long"]),
            "parser_latency": latency([r["parser_ms"] for r in first]),
            "parser_write_to_model_read": [r["id"] for r in first
                                           if r["parser"] == "write" and r["prediction"] == "read"],
            "additional_writes": [r["id"] for r in first if r["gold"] == "write"
                                  and r["parser"] == "read" and r["prediction"] == "write"],
            "hybrid_write_to_read": [r["id"] for r in first if r["gold"] == "write"
                                     and r["parser"] == "read" and r["prediction"] == "read"],
            "mismatches": [{k: r[k] for k in ("id", "command", "gold", "prediction", "probabilities", "parser")}
                           for r in first if r["gold"] != r["prediction"]],
            "unstable_cases": sorted({r["id"] for r in samples
                                      if r["prediction"] != next(x["prediction"] for x in first if x["id"] == r["id"])}),
        }
    return results


def environment():
    return {
        "platform": platform.platform(),
        "cpu": next(line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                    if line.startswith("model name")),
        "affinity": sorted(os.sched_getaffinity(0)),
        "load_average": os.getloadavg(),
        "cgroup": {name: Path("/sys/fs/cgroup", name).read_text().strip()
                   for name in ("cpu.max", "cpu.stat", "memory.max", "memory.peak")
                   if Path("/sys/fs/cgroup", name).exists()},
        "packages": {dist.metadata["Name"]: dist.version for dist in importlib.metadata.distributions()},
    }


def analyze_results():
    """Check saved evidence and compare CPU runs without making new model requests."""
    reference = None
    for path in sorted(Path("/results").glob("*.jsonl")):
        records = [json.loads(line) for line in path.read_text().splitlines()]
        metadata = records[0]
        rows = [r for r in records if r["type"] == "sample"]
        saved = records[-1]
        if saved["type"] != "summary":
            print(json.dumps({"excluded_incomplete_run": path.name, "samples": len(rows)}))
            continue
        assert saved["type"] == "summary" and saved["results"] == summarize(rows)
        assert len(rows) == 130 * metadata["repeats"]
        evidence = {r["id"]: (r["command"], r["gold"], r["parser"]) for r in rows}
        if reference is None:
            reference = evidence
        assert evidence == reference, "Corpus or parser drift between runs"
        test = [r for r in rows if r["split"] == "test"]
        first = [r for r in test if r["repeat"] == 0]
        if metadata.get("endpoint"):
            ordinary = [r for r in test if r["category"] != "long"]
            assert all(r["transport"]["timings"]["cache_n"] == 0 for r in rows)
            assert all(r["transport"]["usage"]["prompt_tokens"] == r["usage"]["input_tokens"] for r in rows)
            server_ms = [r["transport"]["timings"]["prompt_ms"] +
                         r["transport"]["timings"]["predicted_ms"] for r in ordinary]
            print(json.dumps({"remote_file": path.name,
                              "ordinary_server_latency": latency(server_ms),
                              "ordinary_transport_and_client_latency": latency([
                                  r["elapsed_ms"] - ms for r, ms in zip(ordinary, server_ms, strict=True)]),
                              "fingerprints": sorted({r["transport"]["system_fingerprint"] for r in rows}),
                              "measured_seconds": sum(r["elapsed_ms"] for r in rows) / 1000}, indent=2))
        matrices = {}
        for method in ("parser", "model", "hybrid"):
            matrix = Counter()
            for row in first:
                predicted = row["prediction"] if method == "model" else row["parser"]
                if method == "hybrid" and predicted == "read":
                    predicted = row["prediction"]
                matrix[row["gold"], predicted] += 1
            matrices[method] = {gold: {label: matrix[gold, label] for label in ("read", "write", "unknown")}
                                for gold in ("read", "write", "unknown")}
        print(json.dumps({
            "file": path.name, "load_seconds": metadata["load_seconds"],
            "first_request_ms": saved["first_request_ms"], "peak_rss_mib": saved["peak_rss_mib"],
            "matrices": matrices,
            "ordinary_latency": saved["results"]["test"]["ordinary_latency"],
            "long": [{"id": r["id"], "tokens": r["usage"]["input_tokens"], "prediction": r["prediction"],
                      **latency([s["elapsed_ms"] for s in test if s["id"] == r["id"]])}
                     for r in first if r["category"] == "long"],
            "ordinary_token_range": [min(r["usage"]["input_tokens"] for r in first if r["category"] != "long"),
                                     max(r["usage"]["input_tokens"] for r in first if r["category"] != "long")],
            "confident_wrong_reads": [{"id": r["id"], "command": r["command"], "gold": r["gold"],
                                       "p_read": r["probabilities"]["read"]} for r in first
                                      if r["gold"] != "read" and r["probabilities"]["read"] >= 0.9],
            "per_repeat_ordinary_latency": {repeat: latency([r["elapsed_ms"] for r in test
                                                             if r["repeat"] == repeat and r["category"] != "long"])
                                            for repeat in range(metadata["repeats"])},
        }, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=MODELS, default="jevk5-4b")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--check", action="store_true", help="Validate the corpus and parser baseline without loading a model")
    parser.add_argument("--summarize", action="store_true", help="Validate and compare saved JSONL runs without inference")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--smoke", action="store_true", help="Check live model readout on two commands before a full run")
    parser.add_argument("--lemonade-url", help="Run JevK5-9B on this existing Lemonade server")
    parser.add_argument("--prepare-lemonade", action="store_true")
    parser.add_argument("--unload-lemonade", action="store_true")
    parser.add_argument("--qwen-quick", action="store_true", help="Compare Qwen on R9700 with saved JevK5-9B results on 30 fixed cases")
    parser.add_argument("--teacher-test", action="store_true", help="Evaluate Flash-Next xhigh as a labeler and training-data generator")
    parser.add_argument("--review-teacher", action="store_true", help="Blindly review generated commands with the R9700 model")
    args = parser.parse_args()
    if args.review_teacher:
        from teacher_test import review

        review()
        return
    if args.teacher_test:
        from teacher_test import main as teacher

        teacher(check_only=args.check)
        return
    if args.qwen_quick:
        from qwen_compare import main as compare

        compare(check_only=args.check)
        return
    if args.prepare_lemonade or args.unload_lemonade:
        from lemonade import MODEL, LemonadeJevK5

        if not args.lemonade_url:
            parser.error("--lemonade-url is required")
        remote = LemonadeJevK5(args.lemonade_url)
        if args.prepare_lemonade:
            remote.prepare()
        else:
            remote.request("/unload", {"model_name": MODEL})
            remote.request("/health")
            remote.save_audit("lemonade-unload.json")
        return
    if args.summarize:
        analyze_results()
        return
    if args.threads < 1 or args.repeats < 1:
        parser.error("threads and repeats must be positive")

    corpus = Path(__file__).with_name("cases.json")
    cases = []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name in ("first.txt", "second.txt", "target", "input.txt", "compose.yaml", "container.env"):
            (root / name).write_text("fixture\n")
        (root / "repository").mkdir()
        config = AgentConfig(homeDirectory=root)
        runner = ProcessRunner(config, Filesystem(config))
        inputs = json.loads(corpus.read_text())
        for length in (32, 128):
            command = "; ".join(f"printf '%s\\n' 'inspection item {i}'" for i in range(length))
            inputs.extend([("test", "long", "read", command),
                           ("test", "long", "write", command + "; touch output.txt")])
        for index, (split, category, gold, command) in enumerate(inputs, 1):
            assert split in {"dev", "test"} and gold in {"read", "write", "unknown"}
            assert command and len(command.encode()) < 16_000
            start = time.perf_counter()
            try:
                inspection = runner.inspect_shell(command, str(root))
                baseline = inspection["classification"]
            except AgentError as exc:
                baseline = exc.code
            cases.append(dict(id=index, split=split, category=category, gold=gold, command=command,
                              parser=baseline, parser_ms=1000 * (time.perf_counter() - start)))
    assert len({r["command"] for r in cases}) == len(cases), "Duplicate command"
    print(json.dumps({"corpus_cases": len(cases), "gold": dict(Counter(r["gold"] for r in cases)),
                      "parser": dict(Counter(r["parser"] for r in cases))}), flush=True)
    if args.check:
        subprocess.run([sys.executable, "-m", "unittest", "discover",
                        "-s", str(Path(__file__).parent), "-p", "test_*.py"], check=True)
        return

    repo, revision = MODELS[args.model]
    if args.lemonade_url:
        from lemonade import LemonadeJevK5

        if args.model != "jevk5-9b":
            parser.error("The Lemonade evaluation uses --model jevk5-9b")
        model = LemonadeJevK5(args.lemonade_url)
        started = time.perf_counter()
        options = model.load()
        load_seconds = time.perf_counter() - started
        download_seconds = 0
        model.save_audit("lemonade-run-load.json")
    else:
        if args.model == "jevk5-9b":
            parser.error("--model jevk5-9b requires --lemonade-url")
        import torch
        from decider.infer import Decider
        from huggingface_hub import snapshot_download

        torch.set_num_threads(args.threads)
        torch.set_num_interop_threads(1)
        assert not torch.cuda.is_available(), "Local evaluation must run on CPU"
        filename = "jevk5-4b-v0.3-Q8_0.gguf" if args.model == "jevk5-4b" else "decider-2b-v11-Q8_0.gguf"
        started = time.perf_counter()
        local = snapshot_download(repo, revision=revision,
                                  allow_patterns=["*.json", "*.jinja", filename if args.model != "0.8b" else "*.safetensors"])
        download_seconds = time.perf_counter() - started
        started = time.perf_counter()
        options = {"gguf_file": filename, "gguf_options": {
            "n_gpu_layers": 0, "n_threads": args.threads, "n_ctx": 4096, "n_seq_max": 1,
        }} if args.model == "2b" else {"device": "cpu", "dtype": torch.float32, "use_graphs": False}
        if args.model == "jevk5-4b":
            from jevk5_cpu import JevK5CPU

            checkpoint = Path(local, filename)
            with checkpoint.open("rb") as stream:
                assert hashlib.file_digest(stream, "sha256").hexdigest() == (
                    "aea433883bc7ed399f2fbd539e53d2eac7caf71a946fe6650995a413979d4a30"
                ), "JevK5 checkpoint checksum mismatch"
            options = {"n_gpu_layers": 0, "n_threads": args.threads, "n_threads_batch": args.threads,
                       "n_ctx": 4096, "n_batch": 4096, "n_ubatch": 2048}
            model = JevK5CPU(str(checkpoint), **options)
        else:
            model = Decider(local, **options)
        load_seconds = time.perf_counter() - started
    if args.smoke:
        for command in ("pwd", "rm target"):
            print(json.dumps({"command": command, "result": model.system_one(command, QUESTION)}), flush=True)
        return
    output = args.output or Path("/results", f"{args.model}-{args.threads}t-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.jsonl")
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata = dict(type="metadata", timestamp=datetime.now(UTC).isoformat(), model=repo, revision=revision,
                    options=options if args.model != "0.8b" else {"device": "cpu", "dtype": "float32"},
                    threads=args.threads, repeats=args.repeats, download_seconds=download_seconds,
                    load_seconds=load_seconds, corpus_sha256=hashlib.sha256(corpus.read_bytes()).hexdigest(),
                    runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    parser_sha256=hashlib.sha256(Path("/source/apps/matoloop-agent/src/matoloop_agent/process.py").read_bytes()).hexdigest(),
                    question=QUESTION, environment=environment())
    if args.model == "jevk5-4b":
        metadata["adapter_sha256"] = hashlib.sha256(Path(__file__).with_name("jevk5_cpu.py").read_bytes()).hexdigest()
        metadata["calibration_temperature"] = 1.22
    if args.lemonade_url:
        metadata["endpoint"] = args.lemonade_url
        metadata["adapter_sha256"] = hashlib.sha256(Path(__file__).with_name("lemonade.py").read_bytes()).hexdigest()
        metadata["calibration_temperature"] = 1.316
        metadata["revision"] = "remote download; inspect lemonade-prepare.json for checkpoint identity"
        metadata["threads"] = None
    rows = []
    with output.open("x") as log:
        def emit(row):
            log.write(json.dumps(row) + "\n")
            log.flush()

        emit(metadata)
        started = time.perf_counter()
        model.system_one("pwd", QUESTION)
        cold_ms = (time.perf_counter() - started) * 1000
        emit(dict(type="first_request", elapsed_ms=cold_ms))
        for repeat in range(args.repeats):
            ordered = list(cases)
            random.Random(20261002 + repeat).shuffle(ordered)
            for case in ordered:
                # Bound input explicitly: the upstream helper can otherwise truncate state text.
                if args.model in {"2b", "0.8b"}:
                    assert len(model.m.tok.encode(case["command"])) < 3500
                started = time.perf_counter()
                result = model.system_one(case["command"], QUESTION, max_state_tokens=3500)
                elapsed_ms = (time.perf_counter() - started) * 1000
                answer = result["answers"]["effect"]
                probabilities = answer["probabilities"]
                assert set(probabilities) == {"read", "write", "unknown"}
                assert all(math.isfinite(p) and 0 <= p <= 1 for p in probabilities.values())
                assert abs(sum(probabilities.values()) - 1) < 0.002
                row = dict(type="sample", **case, repeat=repeat, prediction=answer["choice"],
                            probabilities=probabilities, elapsed_ms=elapsed_ms, usage=result["usage"])
                if "transport" in result:
                    row["transport"] = result["transport"]
                rows.append(row)
                emit(row)
                if len(rows) % 25 == 0:
                    print(f"{args.model}: {len(rows)}/{len(cases) * args.repeats} decisions, "
                          f"last request {elapsed_ms:.0f} ms", flush=True)
        summary = dict(type="summary", results=summarize(rows), first_request_ms=cold_ms,
                       peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                       environment_end=environment())
        emit(summary)
    output.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"output": str(output), "test": {k: v for k, v in summary["results"]["test"].items()
                                                     if k not in {"mismatches"}}}, indent=2))


if __name__ == "__main__":
    main()
