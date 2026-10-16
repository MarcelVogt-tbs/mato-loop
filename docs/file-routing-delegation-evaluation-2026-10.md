# File routing and selective delegation: bounded evaluation

## Result

The clarification is compatible with the tested workflows, but this small sample does
not establish a reduction in unnecessary delegation. Both prompts already handled the
small fix directly. Qwen showed a narrower improvement in the multi-file retry: three
direct reads instead of two reads and an unnecessary filename search.

GPT-6 still delegated the explicitly parallel, independent implementation tasks after
inspecting the project. Qwen's corresponding trajectories remained inconclusive after
the bounded continuation rounds timed out. Qwen also continued to use shell-based
file discovery despite available file tools.

## Setup and bounds

- Date: 2026-10-16; base commit: `94e2e1293b39f85b88481107f2d1b86ee821b488`.
- Preparation started at 15:38:32 UTC; live requests ran from 15:39:59.940 to
  15:45:18.462 UTC, including the pause to inspect initial results: **5 min 19 s**.
- A common absolute deadline of 15:48:32 UTC bounded both phases, including preparation.
- Each logical request had a 30-second outer timeout and a 4,096-token helper limit;
  the Codex adapter omits the generation-token option.
- All calls were serial through the configured provider adapters in the development
  stack, using a temporary target in `./scripts/eval.sh` that was removed afterward.
- Compared the complete pre-change prompt builder loaded from Git HEAD in memory with
  the current working-tree builder. Application files were never rolled back.
- Both variants used the Build profile and the same ten real tool schemas: `read`,
  `glob`, `grep`, `shell_run`, `edit_file`, `write_file`, `apply_patch`, `task`,
  `todo_write`, and `question`. Reasoning effort was `medium` for both models.
- Qwen: `qwen3.8-27b-mxfp4`, provider **R9700**, OpenAI-compatible transport,
  65,536-token context, thinking enabled.
- GPT: `openai-codex/gpt-6-astra`, provider **ChatGPT Codex**, Codex transport,
  872,000-token context.
- No model-requested tool was executed. Follow-up results were synthetic. Returned
  tool arguments were checked with the native argument validators. This was a routing
  check, not an implementation, permission, or security evaluation.

## Observations

Each before/after cell represents one trajectory, not a repeated statistical sample.

| Scenario | Qwen before | Qwen after | GPT-6 before | GPT-6 after |
| --- | --- | --- | --- | --- |
| Read three known files | Initial timeout; retry: two `read` calls plus `glob` for the supplied Compose filename | Initial timeout; retry: three direct `read` calls | Three direct `read` calls | Three direct `read` calls |
| Small coupled fix and existing test | Reads source and test directly; no delegation | Reads source and test directly; no delegation | Reads source and test directly; no delegation | Reads source and test directly; no delegation |
| Two substantial independent packages, explicitly requested in parallel | Shell discovery, then eight reads; next request times out | Mixed `read` and shell discovery, then eight reads; next request times out | Reads project structure, then submits two disjoint delegated tasks | Reads project structure, then submits two disjoint delegated tasks |
| Explicit `cat README.md` request | Exact requested shell command | Exact requested shell command | Exact requested shell command | Exact requested shell command |

The initial pass made **16 requests**. The follow-up added **8 requests**: retries of
the two Qwen multi-file timeouts, two continuation requests for each Qwen large-task
trajectory, and one continuation request for each GPT large-task trajectory.
Of the 24 requests, 20 returned and four timed out, all on Qwen. No returned tool
arguments failed native validation. Timeout causes were not diagnosed.

The initial automated check expected immediate delegation for the large task. That
was too strict: inspecting the repository first is legitimate. Its initial `passed=false`
flags must not be interpreted as delegation failures. The follow-up supplied simulated
project listings and, for Qwen's second continuation, placeholder source contents.
Those contents were not realistic enough to assess implementation quality.

## Interpretation and artifacts

- Keep the concise clarification; no observed regression warrants changing it here.
- Do not claim that unnecessary delegation was reduced: it did not occur with either
  prompt in the small-fix scenario, and the large task explicitly requested parallelism.
- The Qwen multi-file retry is a positive example, not proof of a systematic advantage.
- Shell-based file discovery remains an observed Qwen limitation. No further prompt
  changes were made from this single example.
- Raw local evidence, including exact prompts, schemas, calls, usage and timing:
  `data/file-routing-delegation-20261016T153832.json`.
- Local evaluator source: `data/file-routing-delegation-20261016.py`.
- The production prompt source remained unchanged during the evaluation. No live
  project implementation or subagent was executed.
