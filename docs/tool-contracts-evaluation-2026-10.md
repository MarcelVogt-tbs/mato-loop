# Tool names, reason fields, and automation permission preparation

## Result

Completed on 2026-10-07 against application commit `c010bcb786f1e5ebae09ed6a370d733716c63b4b`.
The live comparison took **801.079 seconds (13 min 21 s)** within a 1,200-second limit:
18:51:01–19:04:22 UTC. All **44 trials and 71 logical model requests** completed;
none were deadline-truncated. Application source hashes remained unchanged.

- OpenCode names did not establish a reliability advantage over the existing names.
- `reason` improved Qwen's initial permission-request structure in this small sample.
- The explicit failure-handling prompt improved the isolated unsupported-preparation case.
- Neither renaming nor the tested prompt made complete permission preparation reliable.
- No application prompt, tool name, permission policy, or model setting was changed during this evaluation.

## Setup

| Model | Transport/provider | Effective requested effort | Context |
| --- | --- | --- | --- |
| `openai-codex/gpt-6-astra` | ChatGPT Codex | medium | 872,000 |
| `qwen3.8-27b-mxfp4` | OpenAI-compatible / R9700 | medium, thinking enabled | 65,536 |

Calls used the existing development stack and configured provider adapters, serially,
through `./scripts/eval.sh tool-contracts`. The runner reused the existing prompt builder
and provider-request helper. It offered 37 real tool schemas and the full general system
prompt, with a synthetic current-session automation catalog and conversation history.
The fixture clock was frozen at the start of this run.

Tool results were simulated; no shell command, file write, automation, or approval was
actually executed or created. Displayed schemas and native argument validators were
checked before returning controlled results. Exact declared aliases were mapped back
only after validation. Undeclared names, including `functions.*`, remained errors.
Normal error codes and field paths were returned, allowing one format-repair attempt.
Discovery and disabled automation drafts could continue, up to four model rounds.
Preparatory permission requests and activation in the same batch did not count as
correctly sequenced preparation.

Each trajectory was capped at 110 seconds, additionally bounded by the common
20-minute deadline. Provider calls used a 4,096-token helper limit; the Codex transport
omits that generation option. One Codex HTTP 503 was recovered by the existing retry
path. Logical request counts therefore do not represent all HTTP attempts.

## Treatments

The naming comparison held the candidate workflow prompt constant and crossed:

1. Existing tool names versus OpenCode names.
2. `_reason` versus `reason`, with identical optionality and value constraints.

Actual renamed offered tools were `shell_run` → `bash`, `write_file` → `write`,
`edit_file` → `edit`, `web_search` → `websearch`, and `load_skill` → `skill`.
The mapping also defines `todo_write` → `todowrite`, but that tool was not offered
in these fixtures. MatoLoop-specific automation and permission tools retained their names.
Schema descriptions, system text, and seeded history used the selected names consistently.

The behavior comparison held existing names and `_reason` constant, comparing the
current application instruction with this exact candidate:

> Before creating or enabling an automation, call request_session_permissions for the known tool calls its task will need, including conditional follow-up actions when supported. Wait for the result before enabling; do not batch these steps. Previous tool success or allow_once is not a session grant. Never execute an action just to obtain permission. If preparation fails or is unsupported, resolve it or use question to ask whether the user accepts later approval waits. Until then keep the automation disabled. Only claim permissions are ready after a successful result.

## Results

### Complete setup from a user request

The user requested five-minute printer monitoring. History included a successful
one-time status query. Success here required a valid preparation result for that
status command before an enabled automation call. Each cell represents two trials;
the second used a paraphrased user request and reversed treatment order.

| Prompt | Names | Reason field | Astra successful setup | Qwen successful setup |
| --- | --- | --- | --- | --- |
| Current | Existing | `_reason` | 0/2 | 1/2 |
| Candidate | Existing | `_reason` | 0/2 | 0/2 |
| Candidate | Existing | `reason` | 0/2 | 1/2 |
| Candidate | OpenCode | `_reason` | 0/2 | 1/2 |
| Candidate | OpenCode | `reason` | 0/2 | 0/2 |

**Astra:** all ten setup trials initially supplied a namespaced nested target:
`"tool": "functions.shell_run"` or `"tool": "functions.bash"` inside
`request_session_permissions`. The actual registry requires the unqualified name.
Changing the shell tool name or reason field did not resolve this. With the candidate
prompt, Astra then asked about accepting later approval waits instead of correcting
the target name. The attempted preparation is not a successful preparation.

**Qwen:** with the candidate prompt and `_reason`, none of the four initial outer
permission requests passed structure validation. Three placed `_reason` beside
`tool` and `arguments` inside a request item; one encoded `requests` as a string.
With `reason`, all four initial outer structures were accepted without that error.
This is evidence about the envelope shape, not validity or necessity of every nested
future operation: checking stops at the first unsupported target, as in the host path.

Qwen frequently included `notify`, `silence`, or automation-management operations in
advance-permission requests. Those are not supported by the current native preflight
implementation. Several such trajectories then activated despite the returned error;
one left a disabled draft and asked the user. Shorter names do not solve this access
planning problem. Automatic access to session-local automation management also does
not make those calls supported targets of the generic preparation tool.

### Seeded preparation outcomes

These probes began after a syntactically valid preparation call with a controlled result;
they isolate the next decision from the initial argument-construction problem.

| Outcome supplied | Current prompt: Astra / Qwen | Candidate: Astra / Qwen |
| --- | --- | --- |
| `permissionsReady: true` | activated / activated | activated / activated |
| `permission.preflightUnavailable` | activated / activated | asked user / asked user |

There was one trial per model, prompt, and outcome. The candidate's benefit is visible
in this isolated case, but its natural multi-step setup failures above show that it is
not sufficient by itself. Qwen's clarification also overstated the uncertainty as an
approval being required on every run; unsupported preflight does not establish that.

### Direct file operations

Both models correctly created a file with exact content and edited an already-read
file across all four name/parameter combinations: **16/16 valid first attempts**.
This supports compatibility of `write` and `edit` in these cases, not superiority.
Omitting the optional reason was accepted. `websearch` and `skill` were offered but not
exercised, so this experiment cannot recommend those renames on behavioral evidence.

## Interpretation and limits

- `reason` is a promising simplification for Qwen's nested preparation request. The
  sample is too small to claim general reliability, and complete setup did not improve
  consistently.
- OpenCode naming is a defensible consistency choice, but this comparison does not
  justify it as the fix for permission failures.
- The next focused hypothesis is a clearer meta-tool contract: exact permitted target
  names, one valid request example, and a distinction between repairable argument/name
  errors and genuinely unsupported access. This hypothesis was not implemented or tested
  in this run.
- The workflow should avoid requesting preparation for already automatic or unsupported
  management actions. The candidate's broad reference to conditional follow-up actions
  did not prevent unnecessary targets in this sample.
- A pass measures the named tool action or permission/activation order, not the correctness
  of a real unattended automation. Generated task text and schedule semantics were not
  fully scored; examples included questionable printer-state interpretation and a Qwen
  schedule restricted to part of the requested interval.
- The simulator does not reproduce partial durable grants from a multi-target request
  that fails later. A failed overall preflight result must not be interpreted as proof
  that no individual grant was acquired.
- This was a small synthetic comparison with fixed policies, no real tool execution,
  no independent sampling/cache control, and different provider transports. No speed
  ranking or universal winning prompt follows from it.

## Artifacts

- Runner: `scripts/tool-contracts-eval.py`, invoked through `scripts/eval.sh`.
- Raw evidence: ignored `data/tool-contracts-20261007.json`, containing all initial
  prompts, four schema sets, tool calls, feedback, usage, outcomes, and source hashes.
- The evaluated application baseline was `c010bcb`; the evaluation itself changed only
  evaluator tooling and this report.

## Subsequent decision

Retain optional `_reason` to distinguish host metadata from third-party tool arguments.
Clarify permission-target names, argument structure, supported targets, and result handling
in the existing generated tool schemas, including a complete preparation example.
These follow-up description changes were checked with the backend suite, without another
live evaluation. The runner uses the checkout's current schemas; the recorded results above
describe the original baseline, not the revised descriptions.
