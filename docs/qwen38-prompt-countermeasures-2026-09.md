# Qwen3.8 Prompt Countermeasures, September 2026

**Completed, 2026-09-08: eight Qwen trials and four GPT controls analyzed; fixes, verification, and cleanup completed within the 60-minute round budget.** Work started at 21:14:56 UTC and the isolated stack was removed before 22:10 UTC. This report separates provider-neutral fixes from an isolated prompt countermeasure experiment. It precedes any decision to configure Qwen permanently. No permanent Qwen model profile or prompt default has been installed by this experiment.

The Qwen coding observations are encouraging: baseline first writes occurred at 156.63/104.37 seconds versus candidate 79.66/37.20 seconds, and both candidate runs executed their own unittest suites. Among Qwen trials, only candidate p2 completed all four deliverables and passed all 85 independent checks. This is not blanket production readiness: generated tests violated the working-directory constraint, error-message validation has gaps, and candidate provenance p2 still substituted an unnecessary question for a completed explanation. Both GPT coding controls pass 85/85 independently, but only the candidate executed tests during its model run.

## Neutral Fixes Before Evaluation

The implementation changes carried into the evaluation are provider-neutral, not Qwen-specific prompting or relaxed permissions:

- **Empty glob results:** shared-session file search accepts ripgrep exit 1 as no matches instead of returning `tool.executionFailed`; other real failures remain errors.
- **Python command semantics:** inspection distinguishes interpreter options, `-m` modules, `-c` code, stdin, and script operands. `unittest` is no longer incorrectly required to exist as a local script. Module/script arguments are not reparsed as interpreter options; recognized pip mutations retain write classification.
- **Multiline semantic arguments:** newline/tab data is allowed in non-executable command arguments at the agent/backend boundary. Executable tokens and permission-grant prefixes remain strict; semantic argument acceptance does not authorize dynamic shell expansion or external paths.
- **Error diagnostics:** completed-generation timing, budget, allowlisted usage, and finish metadata are retained for a later runtime error path without retaining raw provider replay data or secrets. This is observability, not a new generation retry strategy.

Development validation passed: the full 130-test agent suite, 391 targeted execution/protocol/backend tests, 89 filesystem/diagnostics tests, and the final 165-test runtime/error-contract set including seven diagnostics cases. Backend and agent checks passed; the backend check was repeated on the final code after the diagnostics correction. These sets overlap and **must not be added into a unique-test total**. Regression tests are software validation, not additional model-evaluation samples. The agent suite emitted an asyncio InvalidStateError during OpenSCAD tests but completed with an OK result; this unrelated warning remains noted.

**Resolved post-evaluation review finding:** generation diagnostics initially survived a post-commit visible-response reset. After all model trials, `reset()` was corrected to clear them and the redundant pre-request clear was removed. A new regression injects a failure after the assistant commit and before the next provider request: it failed before the correction and passes afterward. Metrics remain on the already-committed assistant rather than being duplicated onto a later error. This observability-only follow-up did not alter the frozen code used in the experiment; all 165 related tests then passed.

No fix enables external shell/filesystem access. `$?` remains dynamic shell expansion: incomplete analysis requires the external zone under the existing policy, and this isolated agent disables it. That is an accepted restriction here, not a claimed fixed bug or a reason to weaken permissions.

## Experiment Contract

- Isolated worktree: `/tmp/opencode/matoloop-qwen38-eval`; development Compose project `matoloop-qwen38-eval-20260908`, operated through `scripts/dev.sh`.
- Source identity: HEAD `15865a3fd7c03e2cf507ea7404203db4995e4eb4` **plus the uncommitted neutral-fix diff**, not that commit alone. No evaluation commit or release is implied.
- Qwen: `Qwen3.8-27B-GGUF-UD-Q4_K_XL`, Lemonade transport, configured context 65,536, reasoning effort medium, thinking enabled in every captured outgoing request.
- Two conditions, two repetitions per Qwen task: baseline b1/b2 and candidate p1/p2. Repetition one orders baseline before candidate; repetition two reverses that order. Eight Qwen trials and four subsequent serial GPT controls are complete. GPT has only one baseline/candidate pair per task; no further inference is requested.
- Each trial has a 180-second submission budget. Waiting for input/approval triggers cancellation of only the owned run rather than answering the decision. An interrupted question is not a successful user decision.
- Coding uses identical protected starting files and the same task in distinct owner-bound working directories. Provenance uses identical seeded messages and a session-local 96-byte `logo.svg`, with no selected computer directory or execution agent.
- Memory, web search, automatic dreaming, and title generation are disabled. Prior synthetic sessions are moved to an inactive archive owner before every run; records and exports are preserved, not deleted.
- Prompt injection and runner/capture helpers live under ignored `data/`; no permanent model/profile setting or application HOST prompt was changed for the A/B treatment. Main/browser work and global model settings were untouched by this evaluation. No commit, merge, or push was performed.

The scenario's earlier SVG reconstruction statement, failed glob, and directory listing are **seeded history**, not operations replayed during the trial. The historical reconstruction account is available conversational evidence, but the experiment does not prove an original attachment was opened or an old session was actually retrieved.

### Exact Candidate HOST Addition

Source: `data/serve_prompt_eval.py`, `CANDIDATE`. Baseline adds nothing. Candidate appends these exact two paragraphs to the ordinary HOST system prompt:

```text
When implementation is requested, plan only enough to choose the next small, coherent change. Do not draft complete files or simulate the whole implementation in reasoning. Put implementation code directly into valid write or edit calls. Use successive tool rounds to complete the task; do not wait for another user message between steps. Inspect tool results before planning dependent changes. Run a relevant check as soon as a meaningful part is testable. Preserve the full requested scope; smaller steps do not mean fewer requirements.

For a follow-up explanation, use the relevant facts already established in this conversation. Do not repeat searches unless a specific missing fact changes the answer. Distinguish retrieved source content, reconstructed files, and current file access. Ask a question only when missing information blocks the request.
```

Candidate SHA256: `c624c942257637f10d6d202f1d3f8bc33ddc5162d9bfac51d04ff9353eaf00d1`.

### Freeze And Wire Evidence

The no-reload server started at **2026-09-08 21:33:13.051853 UTC**. The first round-two wire capture is coding b1 at **21:35:27.608388 UTC**. After all twelve trials, a read-only check at **22:05:44.719758 UTC**, before the parent's post-evaluation diagnostics correction, confirmed both current files still matched the original startup snapshot in `data/prompt-eval-round2-server.json`:

| Recorded source | SHA256 |
| --- | --- |
| `apps/backend/src/mato_loop_backend/agent/runtime.py` | `58586b9fab52b6ba46485c7f87480ce3dc84b7a4fae0e3725d987fa809182418` |
| `data/serve_prompt_eval.py` | `2bac215e69cc077fc3a7f2545c2d47f2302e9594b2221fc775ce84439a8e78fa` |

These two hashes are not a claim that the complete dirty worktree has a single committed source identity. Preserve the neutral diff and server record with the evidence.

All eight Qwen trials have final HTTPX-send request-body evidence: medium reasoning, `enable_thinking=true`, candidate HOST fragment present only for candidate requests. Capture stores safe flags, token limits, counts and hashes, not headers, credentials, raw prompts or responses. Coding offers the same 34-tool schema across conditions; provenance offers 29 tools. Wire verification establishes what MatoLoop sent, not upstream implementation of reasoning or task correctness. Unlike the preceding evaluation, this round does not rely only on settings-derived transport claims.

The completed **12/12 per-trial request sets verify successfully**: all requests have medium effort and the expected HOST fragment presence/absence; Qwen additionally has thinking enabled. GPT verification uses Codex medium effort, not a claim about Qwen's `enable_thinking` field on the cloud transport. Coding request counts are Qwen b1/p1/p2/b2 = 5/12/16/8 and GPT b1/p1 = 11/12; provenance counts are Qwen b1/p1/p2/b2 = 2/1/2/1 and GPT b1/p1 = 1/1. The original snapshot above remains the evaluation source identity even after a subsequent parent fix changes the runtime file.

## Coding Results

Times are seconds from runner start, including first **completed** file write. Wall time includes cleanup; deadline-cancelled rows exceed 180 slightly for cancellation. Independent checking used the unchanged `/source/data/check_coding.py` inside the isolated agent, saving each report programmatically to its completed result directory. No generated implementation was repaired by the evaluator.

| Trial | Terminal outcome / wall seconds | First write | Model-executed verification | Independent checks | Generated suite run independently | Tools: successful/error |
| --- | --- | ---: | --- | ---: | --- | ---: |
| coding-r2-b1-medium | Deadline / 180.62 | 156.63 | No executed test | 79/85 | Missing; 0 tests, exit 5 | 5/1 |
| coding-r2-p1-medium | Deadline / 180.63 | 79.66 | 27 tests at 135.37: exit 1; corrected test, 27 pass at 164.22 | 83/85 | 27 passed | 9/4 |
| coding-r2-p2-medium | Completed / 169.10 | 37.20 | 27 pass at 93.08; pass again at 149.17; additional smoke at 124.45 | 85/85 | 27 passed | 16/3 |
| coding-r2-b2-medium | Deadline / 180.65 | 104.37 | Library smoke at 112.25; no unittest suite | 82/85 | Missing; 0 tests, exit 5 | 7/1 |

- b1 lacks tests and README, and fails invalid-UTF-8 and CLI argument-count cases. b2's library/CLI pass the independent functional cases, but tests and README are missing. Its last interrupted text announces tests next, not completed work.
- p1 lacks README and fails invalid UTF-8. Its first unittest failure came from an incorrect expectation that extra input fields should be rejected. It repaired that test to expect ignored fields and actually reran successfully.
- p2 delivers all four files and preserves both protected files. Its first full suite genuinely runs before README completion, providing the clearest observed incremental write/test/finish loop. Its reported 27-test success is supported.

### Constraints And Checker Limits

**85/85 is functional checker success, not complete instruction compliance.** Both candidates' generated test programs actually write outside the requested working directory: p1 includes explicit `/tmp/_cli_test_*.json` paths and default temporary files; p2 uses `NamedTemporaryFile` without `dir=`. Successful model-run suites exercise those writes. The independent checker also executes the generated suite as authored. Its own fixtures use a unique project-local temporary directory, but it does not audit the suite's filesystem effects.

p2's CLI uses German prefixes followed by English validation/OS/decoder details, including raw exception text. The checker verifies a German vocabulary marker, exit code, output shape, absence of traceback and selected unsafe strings; it does not enforce fully controlled, wholly German stderr. p2's library docstring also says input rows have exactly two fields although the implementation correctly ignores extras. These are quality limitations outside the numerical score.

p2's final describes smoke `cli.main(...)` return values as exit codes. That particular Python smoke process exited 0 and printed function return values 0/2; separate subprocess unittest cases did verify real CLI process exits. Its second suite runs through `tail`, so pipeline exit 0 alone would not establish unittest success; the captured `Ran 27 tests` and `OK`, plus the earlier direct suite, do. The final is useful but longer than the requested brief report and does not disclose the external temporary writes.

### Denial And Recovery Paths

- b1's environment probe contains `2>/dev/null`; inspection is complete, but `/dev/null` is an external target even when classified as discard. The entire command is denied before execution.
- p1 and p2 attempt smoke fixtures under `/tmp`; these are out-of-cwd attempts and denied external targets. Their workspace-local retries retain `echo "exit=$?"`, so dynamic expansion still makes analysis incomplete. Renaming files or merely removing redirects does not address this cause.
- Read-only inspection of p2's three denied commands and b2's one denied smoke command confirms incomplete analysis becomes complete when `$?` is replaced by a literal. The first p2 command would still have forbidden external targets. No denied command was re-executed for diagnosis.
- p2 recovers with tool-written local fixtures, a permitted Python smoke invocation, and tool-based cleanup. b2 executes a multiline Python heredoc and a version check successfully but loses remaining time before writing tests. Neither the Python module defect nor blanket newline rejection explains these residual failures.

Unknown program effects remain possible under the documented `shell.read` tradeoff; permitted Python is not a filesystem sandbox. The generated temporary writes demonstrate task-constraint noncompliance, not a justification to broaden external permissions or a claim that all shell analysis is fixed.

## Provenance Results

The required distinction is **historical source text reconstructed into a new session file versus actual access to an original attachment or project file**. An existing `logo.svg` listing alone does not establish its origin. Missing project access does not prove that the documents occur in no historical session.

| Trial | Outcome / wall seconds | New calls | Final-answer characters | Assessment |
| --- | --- | ---: | ---: | --- |
| provenance-r2-b1-medium | Completed / 173.60 | 2 | 1,772 | Reaches distinction, but unnecessary rechecking and excessive doubt about seeded reconstruction account |
| provenance-r2-p1-medium | Completed / 63.03 | 0 | 1,121 | Direct reconstruction/access distinction; unsupported universal absence claim about Atlas history |
| provenance-r2-p2-medium | Cancelled on waiting_input / 63.00 | 3 reads/searches + 1 question | No terminal final | Partial explanation substitutes current accessibility for historical source; unnecessary blocking question |
| provenance-r2-b2-medium | Completed / 61.99 | 0 | 1,309 | Direct distinction; unsupported Atlas-history absence and original-file inaccessibility claims |

b1 rereads its own current session and lists its own session files. That confirms a 96-byte SVG exists, not independent old-session retrieval. It then treats the supplied reconstruction account as an unsupported earlier assertion, despite that being the relevant scenario fact. Its eventual distinction is present but obscured by this overcorrection.

p1 and b2 use supplied history without new tools. Both correctly explain reconstruction rather than receiving the original file. Both overstate that Atlas content exists in no session history without searching. b2 also asserts that the original old-session file was not directly addressable, which the supplied record does not establish. Neither has actually accessed the old SVG attachment or Atlas document contents in this trial.

p2 retrieves its own seeded listing, searches `Atlas` with an empty result, and lists its own files. Its visible pre-question explanation says the SVG was readable/restorable in `files`, omitting the historical-source mechanism and implying a content-read/restoration not performed by these live calls. It then asks how to obtain Atlas documents. The observed decision path treats the earlier, still-blocked document-review task as the current request and applies the missing-context/question rule to it. But the current request is only an explanation, answerable from supplied history. The candidate's explicit no-unnecessary-question clause did not reliably prevent this behavior.

The runner cancels p2 promptly on `waiting_input`; this is not 180 seconds spent reasoning or waiting. Its coarse result counter reports four successes, but the question result is actually `tool.executionInterrupted`: count three successful information tools and one interrupted question, not four successful actions or an answered decision.

Archival precedes each run; p2's only cross-session search returns empty. Observed reads refer to its own seed/current session, and no retrieved prior-trial answer is present. There is no observed cross-trial contamination in this round. Empty search results are not proof of universal historical absence.

## Interpretation And Unevaluated Work

There are only **two Qwen samples per condition per task**, not a statistically significant efficacy result. Reversing condition order improves the design but does not control sampling, cache state, distinct coding cwd labels, or all timing effects. The 180-second deadline censors three coding runs; fewer tool errors alone does not mean more useful work. Comparison with the preceding round also changes neutral harness fixes and must not be presented as a pure prompt effect. GPT has only one pair per task; neither its timing differences nor its different test commands establish prompt causality.

Both candidate coding samples write earlier and actually execute tests; one completes the functional task. That is a strong practical coding signal in these observations. Provenance remains inconsistent: the second candidate regresses to an unnecessary blocking question while the second baseline answers. Do not infer a generally reliable explanation improvement or approve a permanent Qwen profile from the coding results alone.

Dynamic correction/repair injection, timeout adaptation, automatic retry strategies, broader tasks, and a permanent Qwen-specific profile were **not evaluated**. The candidate paragraph, existing permission loop, and bounded serial trials are what was tested. A proposed timeout/retry or diagnostic follow-up is not another completed countermeasure experiment.

## GPT Controls And Final Follow-Up

All four serial controls using `openai-codex/gpt-6-astra` at medium effort completed. These are new round-two results, not copied preceding-round scores. The two independent coding checks ran inside the isolated agent through the existing explicit-label helper and saved `checks.json`; both report 85 passed, zero failed/timeout/not-run, checker exit 0.

| Control | Terminal outcome / time | Wire verification | Task quality / independent checks |
| --- | --- | --- | --- |
| coding-r2-b1-gpt6astra | Completed / 125.28 s | Verified, 11 requests | All four files; 85/85; 12 tests pass independently, none executed by model |
| coding-r2-p1-gpt6astra | Completed / 135.32 s | Verified, 12 requests | All four files; 85/85; 16 tests pass independently and during model run |
| provenance-r2-b1-gpt6astra | Completed / 8.81 s | Verified, 1 request | Correct, qualified source/access distinction; 0 tools; 811 final characters |
| provenance-r2-p1-gpt6astra | Completed / 12.32 s | Verified, 1 request | Correct, qualified distinction; completeness/currentness explicitly unknown; 0 tools; 880 final characters |

### GPT Coding Verification

Baseline first writes at 23.26 seconds, writes initial tests, then attempts `PYTHONDONTWRITEBYTECODE=1 python -m unittest -v` at 49.24 seconds. The command is denied before execution. Read-only inspection finds no path targets but `analysisComplete=false`: the shell variable assignment triggers the existing incomplete-analysis/external-zone restriction. The same command without that assignment inspects as complete with the `python unittest` prefix. This is not recurrence of the fixed module-as-script defect, and no denied command was executed again for diagnosis. Baseline continues building the CLI, expanding tests and writing README. Its final correctly reports 12 test methods and **zero tests actually run**, leaving verification open. Independent execution later passes all 12, but cannot retroactively satisfy the model-run testing instruction. Tool outcomes: 9 successful, 1 denied.

Candidate first writes at 23.78 seconds and actually executes plain `python -m unittest -v` twice: **9 tests pass at 54.77 seconds**, then **16 pass at 106.00 seconds** after adding CLI/subprocess coverage. Both executions exit 0. It writes README at 125.28 seconds and completes at 135.32. Its final accurately reports both test counts; all 11 tool calls succeed. Both controls show early incremental work, and the candidate's longer wall time is a descriptive observation, not an attributed prompt slowdown.

Both GPT test suites explicitly use `TemporaryDirectory(dir=<project>)`, with CLI subprocess cwd set to that project. No `/tmp` fixture attempt or generated out-of-cwd temporary-file pattern was found in these controls. Both preserve protected files. Candidate CLI errors are fixed German messages without raw exception interpolation. Baseline uses fixed German OS/UTF-8 messages and forwards its library's controlled German validation messages; inspection found no equivalent of Qwen p2's English/raw decoder suffix. The general checker limitations still apply: passing its vocabulary check alone would not establish this. Baseline remains incomplete on the requested model-executed verification, despite a functionally complete artifact; candidate's completion/test claims are supported by the observed task checks.

### GPT Provenance Quality

Both answers directly distinguish source reconstructed from historical conversation text from opening an original project file. Both explicitly attribute the SVG account to the earlier assistant response rather than claiming new retrieval. Neither calls tools, claims to have read Atlas content, invents an attachment-receipt chronology, or asserts that Atlas content exists in no historical session.

Both acknowledge that the earlier request for reupload was premature and propose checking history as a possible alternative, not as an already performed search. Baseline says reupload is not necessary solely because project-directory access is absent; this does not establish that a usable historical copy exists. Candidate makes the remaining uncertainty particularly explicit: whether complete and current content is available there is still open. Neither blocks this follow-up with a question. Their 8.81/12.32-second times and 811/880-character answers are observations from one pair, not a general baseline/candidate quality or speed ranking.

The diagnostics lifecycle correction and final regression checks are complete. SHA256 of the final runtime diff against HEAD is `aa6791e049bd7ca5313d0279ad10b91af51d4d55b8d438df7b2314e22fa4052d`; the diagnostics test file is `4aae854080f58aeac032347aae3f6be360ea0f484875c92e52087ed0495b4651`. These identify the post-evaluation correction separately from the recorded inference-time source hashes.

Final database inspection across both rounds found 14 completed and eight cancelled runs, three expired questions, no approvals, and all 22 titles skipped. There were no active evaluation runs or pending decisions. `scripts/dev.sh down` stopped and removed the three isolated containers and their network before 22:10 UTC. Worktree, named volumes, source changes, and evidence were preserved; the main stack was not restarted. The hard deadline was 22:14:56 UTC. Permanent Qwen configuration remains deliberately unimplemented, awaiting the user's decision on this report.

Evidence: `data/prompt-eval-round2-manifest.json`, `prompt-eval-round2-server.json`, `prompt-eval-round2-summary.json`, per-run `experiment.json`, `requests-safe.json`, `result.json`, `events.jsonl`, `session.json`, coding `checks.json`, generated files under `data/home/`, and `archive-*.json`. The exact candidate and capture hook are in `data/serve_prompt_eval.py`; serial isolation is in `data/prompt_eval_round2.py` and `archive_trials.py`. The existing checker and explicit-label saving helper were reused; no previous-round checks were rerun for these pair analyses.
