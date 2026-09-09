# Qwen3.8 Bounded Evaluation, September 2026

**Completed, 2026-09-08.** All ten trials are analyzed: eight originals, one owner-isolated provenance repeat, and one fresh coding-medium repeat configured for up to 600 seconds. Evaluation and isolated-stack cleanup finished within the 60-minute overall budget, approximately 20:16-21:08 UTC.

The fresh medium coding repeat delivered all four files in 122.815 seconds, before the original 180-second cap, with 84/85 independent checks passing. This revises any general interpretation of medium as unable to produce useful work: its two samples varied substantially. The longer timeout was not model-visible and did not explain the improvement. GPT passed 85/85 checks, while both complete implementations had host-denied model test attempts.

## Scope and Reproducibility

- Frozen application HEAD: `15865a3fd7c03e2cf507ea7404203db4995e4eb4`, branch `eval/qwen38-bounded-20260908`.
- Isolated worktree: `/tmp/opencode/matoloop-qwen38-eval`; Compose project `matoloop-qwen38-eval-20260908`. Development operations used `scripts/dev.sh`, not production Compose.
- Separate database, backend, execution agent, volumes, synthetic owner, and agent home. No frontend or embedding service was started. Memory, web search, automatic dreaming, and title generation were disabled for these trials.
- Qwen: `Qwen3.8-27B-GGUF-UD-Q4_K_XL`, Lemonade transport, 65,536-token configured context. Control: `openai-codex/gpt-6-astra`, Codex transport, 272,000-token context and medium effort.
- Main model configuration and main runtime were untouched. The authorized cloud access credential was securely transferred into isolated encrypted storage; no refresh token was copied and no OAuth refresh was performed. No credential values are included here.
- No harness, prompt-contract, provider, or shell-policy fix was applied. A temporary backend image line in `docker-compose.dev.yaml` reused an existing image during setup and was removed after shutdown. Only this report remains as a non-ignored worktree addition. No commit, merge, or release was made.
- Runnable evaluation and checking scripts, fixtures, archive manifest, configuration provenance, generated projects, and raw evidence are under ignored `data/`. See `data/SETUP.md`, `bounded_eval.py`, `check_coding.py`, `scenarios.json`, `eval-models.json`, `results/`, and `home/`.

Each original trial had a 180-second submission deadline. Provenance asked for the difference between recovering an SVG from prior conversation text and accessing two unavailable project documents. Its preceding messages, including a failed glob result and a reconstruction claim, were **seeded history**, not a replay of those earlier operations. The coding task required four small standard-library files: inventory parsing/aggregation, a UTF-8 JSON CLI, unittest tests, and an English README. It explicitly required small incremental writes, early verification, and actual unittest execution. No original full-application implementation or UI evaluation was performed within this cap.

Evidence comes from persisted `session.json`, bounded `result.json`, `events.jsonl`, per-request maximum reasoning snapshots, generated files, and independent `checks.json`. Timings below are seconds from runner start; active duration is the persisted runtime measurement. Reasoning characters sum the maximum captured text per request, not cumulative SSE updates or token counts.

## Original Trials

| Trial | Terminal state | Wall / active seconds | Live tool calls | Reasoning chars | Actual outcome |
| --- | --- | ---: | ---: | ---: | --- |
| Provenance medium | Cancelled, deadline | 180.776 / 69.870 | 3 | 9,291 | Explanation then unanswered question; no terminal final answer |
| Provenance low | Completed | 82.666 / 82.139 | 2 | 14,628 | Central distinction present, but contaminated; exclude comparative quality |
| Provenance none | Completed | 17.309 / 16.774 | 0 | 0 observed | Invented attachment-based provenance; central distinction missed |
| Provenance GPT control | Completed | 10.271 / 9.807 | 0 | 0 observed | Correct, qualified explanation from supplied history |
| Coding medium | Cancelled, deadline | 180.659 / 179.977 | 5 | 29,588 | Only inventory module; no model-executed tests |
| Coding low | Cancelled, deadline | 180.675 / 180.000 | 3 | 28,819 | No generated deliverables |
| Coding none | Cancelled, deadline | 180.648 / 34.456 | 5 | 0 observed | Inventory module; denied smoke check then unanswered question |
| Coding GPT control | Completed | 131.870 / 131.274 | 10 | 0 observed | All files; test attempt denied; honest final verification caveat |

No original run has terminal state `failed`. Four were deadline-cancelled, with interrupted final messages. Task failure, tool error, and cancellation are different outcomes. A runner `exit_code: 0` means its cleanup succeeded, not that the task passed. Interrupted question results must not be counted as successful user decisions merely because a coarse result counter does so.

### Provenance and Contamination

Medium reread a stored directory result and listed current files by 32.182 seconds, then created a question at 70.380 seconds. Approximately 110 seconds were spent waiting for input, not generating. Its explanation described current accessibility but omitted the seeded transcript-reconstruction distinction. No SVG source or project-document content was newly retrieved.

Original low reread the stored listing and searched session history. The search returned the earlier medium trial's assistant answer. Its final included transcript reconstruction, but that sample is not independent and must be excluded from comparative quality conclusions. None used no tools but substituted an unsupported attachment chronology. GPT used no tools and explicitly attributed the reconstruction account to the earlier answer; possible project content in history was presented conditionally, not claimed as discovered. GPT had no recorded cross-trial retrieval, prior session episodes, or durable memory references.

Before the repeat, the operator moved the eight original sessions to a separate inactive synthetic archive owner. The archive script and manifest preserve the original rows and exports rather than deleting trials. This was evaluation-data isolation, not a harness change.

| Isolated repeat | Wall / active seconds | Requests / tools | Reasoning chars | Search result |
| --- | ---: | ---: | ---: | --- |
| `provenance-isolated-low` | 80.636 / 80.317 | 2 / 2 | 13,428 | Empty results |

The repeat used identical prompt, scenario, and configuration hashes to original low. `session_tool_result_read` retrieved only its own seeded listing; `session_search` returned `results: []` (`session.json:322`). Both calls finished by 44.932 seconds. Provider calls took 44.734 and 35.507 seconds; 625 reasoning-update events were captured. No earlier-trial content entered through these tool results.

The repeat's final is grounded about the current listing and empty search, but **still misses the central provenance distinction**: it explains that the SVG was already in accessible storage rather than explaining reconstruction from historical source text. It also describes reading/providing the SVG without a new content-read or provision action in this run. Use this repeat, not the contaminated first low answer, when judging low-effort provenance quality. One repeat does not establish that contamination caused the original answer difference.

### Coding Progress and Verification

Medium read the directory and instructions, read the protected note, checked the Python version, then wrote `inventory.py` at **177.935 seconds**. The first three provider calls consumed 177.580 seconds; request 3 alone took 126.552 seconds. Repeated implementation, CLI, test, and error-handling drafting occurred before the first write despite the incremental-work instruction.

Low finished its three reads by 8.593 seconds, then remained in request 3 until cancellation, capturing 28,504 reasoning characters in that request. It made no write or shell call. None read instructions and the directory, wrote inventory at **27.225 seconds**, attempted a smoke check denied at 30.361 seconds, and created a question at 34.594 seconds. Approximately 145.5 seconds then elapsed waiting for input. There was no executed smoke test or unittest suite.

GPT read instructions first, listed the directory, wrote inventory at **25.298 seconds**, wrote initial tests, and attempted `python -m unittest -v` at 56.920 seconds. After denial it continued: wrote the CLI, read and extended tests, and wrote the README. All four files existed by 123.434 seconds. Its final correctly reported 16 test methods and zero tests actually executed during the model run. It completed useful work without falsely claiming verification or waiting on an unnecessary question.

The existing independent checker ran only inside the isolated execution agent. Reports were captured programmatically into each completed coding directory's `checks.json`; generated code was not repaired. Its 85 outcomes include file presence, protected-file preservation, import, 36 library cases, CLI behavior, and generated unittest execution.

| Coding trial | Required files present | Passed / failed / not run | Checker exit | Generated tests executed independently |
| --- | --- | ---: | ---: | --- |
| Medium | Inventory only | 42 / 43 / 0 | 1 | 0; discovery exit 5 |
| Low | None | 4 / 45 / 36 | 1 | 0; discovery exit 5 |
| None | Inventory only | 42 / 43 / 0 | 1 | 0; discovery exit 5 |
| GPT | All four | 85 / 0 / 0 | 0 | 16 passed, zero skipped; exit 0 |
| Fresh medium repeat, 600-second cap | All four | 84 / 1 / 0 | 1 | 33 run: 32 passed, 1 error; exit 1 |

No checker timed out. Both protected files remained unchanged in all five projects. Original medium and none passed all exercised library checks but did not deliver complete applications. GPT's completed application passed all checker outcomes, including controlled German CLI errors. The fresh medium repeat passed every independent functional check, but its generated suite contained a test-code error detailed below. Independent success does not retroactively satisfy the instruction that the model itself execute tests. English internal `ValueError` messages alone are not a German UI-contract violation; the user-facing CLI boundary is the relevant requirement. None had no CLI to evaluate.

## Shell Denial Diagnosis

Read-only effective-policy inspection confirmed Build has automatic shell read/write for both private and public models. The execution agent permits home shell and automatic home filesystem access, but disables external shell and denies external filesystem access. **That strict isolated-agent configuration is an evaluation confounder**, not evidence that Build's private/public selection was wrong.

Inspection of the exact recorded commands was reproduced inside the isolated agent without executing them. Agent references below identify `apps/matoloop-agent/src/matoloop_agent/process.py`; backend references are relative to `apps/backend/src/mato_loop_backend/`.

| Command | Inspection | Cause in frozen agent source |
| --- | --- | --- |
| None's multiline `python3 -c` smoke check | Incomplete, read classification, empty serialized command list | Newlines inside an argument trigger the control-character extraction restriction, `process.py:1076-1087` |
| GPT's `python -m unittest -v` | Incomplete, read classification, nonexistent local `unittest` script target | `_path_operands` mistakes the module for a script, `process.py:736-740`; missing required target marks analysis incomplete, `1027-1029` |
| Medium's `python3 --version` | Complete, read classification, no targets | Neither problematic argument form nor missing inferred script target |

In backend `agent/permissions.py:1150-1158`, incomplete analysis adds the external zone, then the external-shell prohibition denies the request. Build's workspace auto allowance is evaluated later at `1175-1176`. Thus both commands received `permission.denied` before Python ran. GPT exposes a concrete module-operand inspection defect; none exposes the multiline serialization restriction. These are command-analysis findings, not a critique of the documented shell security tradeoff, and no boundary was relaxed.

Neither denial is a Python syntax failure or an output-truncated tool batch. None's four completed provider responses ended in `tool_calls` with 72, 749, 148, and 166 output tokens against roughly 50-52k transmitted limits. No output-limit/truncated-batch event explains these original coding outcomes; medium/low were deadline-interrupted, and none was waiting for input.

## Transport and Measurement Caveats

- No actual HTTP request-body capture was made. Session settings plus frozen `agent/runtime.py:2415`, `providers/service.py:575-652`, and `providers/openai_compatible.py:251-278` derive Qwen medium/low effort and `chat_template_kwargs.enable_thinking=true`; none disables thinking and omits effort. This does not prove upstream interpretation of each option.
- Recorded transmitted output limits were 53,312/52,975 for provenance medium and 52,361/52,026/51,765 for completed coding-medium requests. The 2,048-token reserve was **not** an output cap. Codex metrics report `transmittedMaxOutputTokens=null`; the cloud transport omits that generation option.
- Read-only Lemonade 11.7.0 health metadata reported llama.cpp/Vulkan, configured context 65,536, `preserve_thinking=true`, temperature 1.0, top-k 20, top-p 0.95, min-p 0, and parallel 1. These are server settings, not wire capture. Direct template metadata was unavailable.
- Recorded before/after health snapshots showed Qwen loaded and idle. These endpoints also gated cloud controls but do not measure cloud contention. The snapshots do not establish absence of intervening foreign load; no observed delay is attributed to foreign contention.
- Cache conditions differed: first-request cached inputs for original provenance low/none were 7,527/0; coding low/none 6,825/0; isolated provenance low 6,020. There was no controlled cache reset. Sequential single samples cannot support a general speed ranking or causal effort comparison.
- Cloud reasoning text was absent from captured events, while coding usage reported 31 reasoning tokens in one request. Qwen text-character totals and cloud reasoning visibility are not comparable measures of internal work.
- The compact coding task does not establish capability on a larger application, UI behavior, or long-term persistence. Session/run persistence supplied evidence here; it does not prove completion reliability across retries or longer budgets.

## Completed Fresh Medium Repeat

`coding-extended-medium` completed at approximately 21:01:57 UTC, using **122.815 seconds wall / 122.478 active**, with no question-wait interval. It used eight requests and eight tool calls: directory/instructions/note reads, four file writes, then one denied shell command. First writes completed at **21.180 seconds** (inventory), **30.678** (CLI), **77.022** (tests), and **91.599** (README). It attempted verification only after all four files, not immediately after the first write.

Captured reasoning totaled **4,780 characters**, with 81 update events, versus 29,588 characters in original medium. Provider-reported output tokens per request were **95, 68, 801, 410, 2,796, 564, 54, 1,177**, totaling **5,965**. Summed input usage was 89,646 tokens, including 82,351 cached input tokens across requests; these sums include repeated context, not unique task text. Reasoning token counts were not separately reported. Requests 1-7 ended with `tool_calls`, and request 8 with `stop`; no truncated batch or output-limit error was observed.

The exact command was `python3 --version && python3 -m unittest -v 2>&1`, denied at **94.461 seconds**. Read-only inspection reproduced an incomplete result with a nonexistent local `unittest` script target, the same Python module-operand defect as GPT. Descriptor redirection was not the cause. The entire command was denied before execution, including its version check. The final honestly said tests had not run and made no passed-test claim; it requested permission in text but did not create a blocking question.

Independent checking saved `data/results/coding-extended-medium/checks.json`: **84 passed, 1 failed, 0 timeout, 0 not_run**, exit 1. All four files exist, protected files are preserved, and the library and CLI checks pass. The sole failed outcome is `generated_unittest`: **33 tests ran, 32 passed, one errored**, with no skips. A diagnostic rerun identified `test_inventory.py:28`, where `test_valid_items` constructs a set of sets (`{set(i) for i in items}`), raising `TypeError: unhashable type: 'set'`. This is a generated test runtime error, not a syntax error or demonstrated application failure. The model's final count of **26** tests was inaccurate. No output was fixed.

The repeat has identical user-prompt and scenario hashes to original medium, but a different scenario name, session/directory path, and configuration hash due to the path. First-request cached input was 6,825 tokens versus zero in original medium; cache and sampling were not controlled. The timeout is an operator-side runner deadline, not model-visible prompt content. Because completion occurred **before 180 seconds**, this is **not evidence that increasing the timeout improved completion**. The observed change is consistent with run-to-run stochastic variation and changed context/cache conditions; their individual effects cannot be isolated. Medium showed both prolonged prewriting and substantial incremental output, so neither the original failure nor this partial validation success establishes a global profile ranking.

Per-run cleanup reported success and before/after Qwen health was idle. All three isolated service containers and their network were stopped and removed with `scripts/dev.sh down` before 21:08 UTC. Named volumes and the worktree were preserved. Final database inspection found six completed and four cancelled runs, two expired questions, no approval requests, and all ten titles skipped. No evaluation run or decision remained pending. Main-stack services remained running; no main-stack restart was performed by the evaluator.

## Independent Glob Reproduction

After model trials, `data/check_glob.py` called the unchanged `RootFileSearch` against the existing synthetic session containing only `logo.svg`. The matching pattern `*.svg` returned that file; both `**/*.md` and `*.md` raised `tool.executionFailed` rather than returning empty matches. This read-only diagnostic independently reproduces the implementation defect in `filesystem_search.py:75-76`; it is separate from the seeded historical error used in the provenance scenario. Evidence is in `data/glob-reproduction.json`. No fix was applied.

## Scoped Next Steps

1. Repeat the same small task under matched budgets, cache conditions, and owner isolation before increasing scope. Separate persistence of useful incremental output, test correctness, and time spent waiting for a decision; the fresh medium result does not demonstrate a benefit from a larger timeout.
2. Correct the independently reproduced shared-session glob no-match handling with a focused regression check. The model-trial fixture used a seeded error; the separate diagnostic, not that fixture, confirms the defect.
3. Add focused diagnostics for Python `-m` operand analysis and multiline command extraction, then evaluate policy behavior without broadly enabling external shell. Keep any subsequent fix separate from this frozen baseline.
4. Repeat owner-isolated, task-matched trials before selecting a Qwen low/none profile or changing defaults globally. Less reasoning did not guarantee provenance correctness; lower effort did not guarantee earlier coding output. The incremental-write instruction was already present and was not sufficient by itself.
