# Required tool reasons: Qwen runtime evaluation, September 2026

## Decision

Keep `_reason` required for model-generated permission-bearing tool calls. Retain the
provider-neutral history, validation, and retry corrections evaluated here. The general
system prompt was not changed. This is a targeted reliability result, not a claim that
Qwen always follows the schema or that optional reasons have been experimentally ruled out.

The corrected candidate completed **12/12 tasks**, versus **9/12** on the baseline.
All three baseline follow-up tasks failed after reasons disappeared from persisted tool
history; all three candidate follow-ups completed. In a candidate legacy-history run,
Qwen still omitted two reasons, then supplied them after the ordinary error feedback.
Both corrected calls executed and the task completed. Recovery therefore improved in
a real model trajectory, not only in a mocked regression test.

## Setup and source identity

- Date: 2026-09-22. Baseline: 11:59:41–12:04:08 UTC; candidate: 12:06:32–12:11:06 UTC.
- Baseline commit: `e53dcd6c41dbd2d8f8939b081a2ceab4daf52703`.
- Candidate: that commit plus the uncommitted changes in `agent/runtime.py`,
  `agent/tool_registry.py`, and `prompt_eval.py`. Tests and this report are additional
  verification artifacts, not model-facing changes.
- Endpoint: `http://192.168.1.102:8000/v1`; OpenAI-compatible transport to vLLM.
- Exact model ID: `qwen3.8-27b-mxfp4`; server model root:
  `amd/Qwen3.8-27B-Quark-AWQ-MXFP4`.
- Configured context: 65,536 tokens, matching the live model catalog. Older discovery
  metadata in MatoLoop still reported 8,192, but `use_discovered_context_window=false`.
- Session reasoning effort: `medium`. Every captured runtime model-start event reports
  the expected model and effort. The adapter maps this to `reasoning_effort=medium`
  and `chat_template_kwargs.enable_thinking=true`. No independent HTTP-wire capture
  or verification of vLLM's interpretation of the effort level was performed.
- Existing development stack through `scripts/dev.sh`, normal session/message API,
  persisted runs, real filesystem and shell execution on the connected development
  agent. The existing configured provider/model was used without changing defaults.
- The General profile was used for every task, including the coding task. The separate
  Qwen-specific Build-profile instructions were therefore not the treatment.
- Memory and web search were disabled per session. Both arms offered **56 tools** in
  every recorded model request, rather than a reduced two-tool benchmark.
- Three serial repetitions of each of four scenarios per arm: 24 task trajectories,
  36 persisted runs in total. Baseline ran first; candidate ran second.
- Test files were isolated under `data/home/reason-eval-20260922/`, corresponding to
  `/workspace/reason-eval-20260922/` on the development agent.

The source hashes remained unchanged during the candidate evaluation and were verified
again after collecting the final evidence:

| Source | Baseline SHA256 | Candidate SHA256 |
| --- | --- | --- |
| `agent/runtime.py` | `377b7274fd703d7827fab1bf51374a55c0fde1d217b5b24c68f90debd04fc0e0` | `6469622c75e715d006647551e8d4c4466441b74bccc090c90a9e364d41e1ac3a` |
| `agent/tool_registry.py` | `061508e0ce1b497278e21ba93147529fab828eb9416edf12728bdfa380059eea` | `4dfc2e70532c748918ebc2291c503913570737b705063357cad91cefc3651021` |
| `agent/context.py` | `42c772c49c0a1c917c1f92b79f52662d5bd1ceda441806df2f3f16e3ab75a111` | unchanged |
| `prompt_eval.py` | `5084971c0731ed7e0c6bb41059b960225118946943379fd84563af727fcedb92` | `1d81a5679727de2c5ddf8c50bc696f111092914068a5707111ddcb05d58454b6` |

## Scenarios and acceptance checks

1. **Fresh:** read two INI files, execute `pwd`, and report both services and data paths.
   Check the requested values, completed run, and actual successful shell output.
2. **Follow-up:** complete the fresh task, change one INI file in the harness, then ask
   the model to read it again and execute `pwd`. Check the new value and execution.
3. **Legacy:** create a session, seed twelve historical user/read/result triples with
   reasons absent, then perform the fresh task. Historical results are synthetic;
   current reads, approvals, and shell calls are real. Normal context projection may
   compact historical result bodies, so this is not a 64K-context stress test.
4. **Coding:** inspect a small Python greeting function and its existing test, change
   only the function's greeting, and run `python test_greet.py`. Require model-run
   `PASS greet` output, an unchanged test file, and a separately executed passing test.

The harness answered only approvals belonging to its own runs. Its command allowlist
contained `pwd`, `python test_greet.py`, and `python3 test_greet.py`; filesystem approvals
were limited to the evaluation paths. There were 44 approved requests on the baseline
and 48 on the candidate. This includes the actual approval/reason flow, with automated
decisions instead of human response latency. No evaluation run remained active at the
final inspection.

## Results

Times are median wall seconds per complete scenario, including setup, polling, and
automated approvals. Follow-up and legacy scenarios each include two persisted runs.

| Scenario | Baseline passed | Candidate passed | Baseline median | Candidate median |
| --- | ---: | ---: | ---: | ---: |
| Fresh | 3/3 | 3/3 | 13.77 s | 15.44 s |
| Follow-up | 0/3 | 3/3 | 25.56 s, failed | 29.18 s, completed |
| Legacy history | 3/3 | 3/3 | 20.43 s | 21.52 s |
| Coding and test | 3/3 | 3/3 | 23.52 s | 24.48 s |
| **Total** | **9/12** | **12/12** | | |

| Runtime metric | Baseline | Candidate |
| --- | ---: | ---: |
| Model requests | 49 | 45 |
| Tool calls, including rejected calls | 69 | 54 |
| `tool.invalidArguments` results | 11 | 2 |
| `agent.repeatedToolCall` results | 7 | 0 |
| `file.pathNotFound` results | 6 | 2 |
| Run failures | 3 | 0 |

The reason omissions remained observable in the next model request for 11 baseline
calls and two candidate calls. This is stronger evidence than simply inspecting stored
baseline calls: the baseline intentionally removed reasons from storage. Six terminal
baseline calls have only persisted arguments available and are not counted as proven
model omissions. All 54 candidate calls have next-request argument evidence.

### Observed repair

Candidate legacy repetition 3, run `b9e22b4f-b878-4740-a8ed-83e390f2fcb2`:

1. Qwen called `read` for both INI files without `_reason`; `shell_run` had a reason.
2. Both reads returned `tool.invalidArguments`, field `_reason`; `pwd` succeeded.
3. Qwen retried the same paths with concise German reasons. The runtime accepted the
   corrections and both reads succeeded.
4. The final answer contained the correct service names, paths, and working directory.

The source-only correction to retry identity is independently necessary: on the baseline,
the regression test that adds only the missing reason receives `agent.repeatedToolCall`.

## Implementation contract

- Persist supplied reasons alongside the existing redacted tool arguments. Historical
  records are not rewritten and missing reasons are not invented.
- Describe the required reason directly in every relevant schema, with length bounds.
- Reuse argument validation in both model execution and prompt evaluation. Reject
  missing reasons for model calls and reject explicit null, blank, non-string, or
  overlong reasons. Host-internal validation can still omit the model-only field.
- Preserve `_reason` when identifying failed argument-validation calls, allowing real
  repairs. Continue excluding it from execution-failure and successful-research identity,
  so rewording the explanation does not retry the same failed action or repeat research.
- Keep the original failed-call identity when a duplicate is rejected; otherwise the
  duplicate error would itself prevent a later valid repair.
- Consume the existing repeat-repair allowance once per model response, not once per
  member of a batch. The loop remains bounded and permissions are evaluated normally.

## Costs and limitations

- The observed improvement is for the combined correction, not an isolated prompt
  effect. There was no ablation of persistence versus schema wording versus retry logic.
- Three repetitions per scenario are a targeted regression experiment, not statistical
  proof of general reliability. Sequential baseline/candidate order does not control
  provider sampling, caching, or background load.
- Estimated input sizes increased from 14,559–17,164 to 16,591–19,167 tokens, primarily
  from explicit per-tool schema guidance and bounds. This is approximately 2K extra
  estimated input tokens; it is not a measured tokenizer count. No speed improvement
  is claimed. In particular, failed baseline tasks cannot be treated as faster successes.
- Reasons are structurally more reliable, but language compliance is not perfect:
  candidate coding repetition 1 used English reasons in five calls, and another reason
  mixed German and English. Those explanations were relevant, but did not fully follow
  the user's-language instruction. Task success does not imply complete prompt compliance.
- Two candidate path errors remained and were repaired. These were separate from the
  reason contract and were not hidden from the results.
- The original production Homelab session was not replayed against live infrastructure.
  No deployment, DNS modification, or production-stack operation was part of the test.
- An initial runner pilot failed because it referenced the old approval attribute
  `details` instead of `payload`. That run was cancelled and excluded before the 24
  measured trajectories. Its separate evidence was preserved.

## Verification and artifacts

The new contract suite has 13 cases: 11 failed and two passed before the implementation;
all pass afterward. It covers real registry validation inside the runtime loop, metadata
repair, repeated batches, execution-failure protection, persisted history, and evaluator
parity. Existing semantic evaluator fixtures were updated to supply the required field.

Final targeted backend verification: **894 tests passed** across reason contracts,
runtime budgets, execution tools, runtime events, prompt evaluation, required research
runtime, tool contracts, and context contracts. `scripts/dev.sh check backend` passed.
These are software tests, not additional model-evaluation samples.

Ignored local evidence:

- `data/test_reason_eval_20260922.py`: one-off API runner and evidence extraction.
- `data/reason-eval-20260922-{baseline,candidate}-main.json`: original run exports.
- `data/reason-eval-20260922-{baseline,candidate}-main-verified.json`: exports refreshed
  from persisted messages and diagnostics, including argument provenance and approvals.
- `data/reason-eval-20260922-summary.json`: aggregate counts and cleanup/source checks.
- `data/reason-eval-{baseline,candidate}-main.log`: serial execution logs.
- `data/reason-regression-before-fixed-fixture.log`: pre-fix regression evidence.
- `data/reason-backend-tests.log` and `data/reason-backend-check.log`: validation logs.

No model-specific workaround, optional-reason fallback, new dependency, migration, or
permanent evaluation framework was introduced. The recommendation is to retain the
small contract correction and the required field, while acknowledging the remaining
language-quality limitation.
