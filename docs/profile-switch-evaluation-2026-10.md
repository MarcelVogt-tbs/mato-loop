# Generic agent-profile switch reminder: bounded evaluation

## Result

**The proposed reminder is not ready as a general solution.** GPT-6 correctly
explained the historical Plan restriction in both candidate trials, but still
incorrectly retracted the historical statement in both custom-profile trials.
The current custom review restriction was respected in both variants.

The candidate preserved the prepared conversation prefix after the switch and
added only 49–51 input tokens in the tested switched fixtures. However, actual
Codex cache hits were intermittent and less frequent for the candidate. These
measurements do not establish cache neutrality or a causal cache regression.

**Qwen could not be evaluated:** all calls to the configured R9700 provider failed
with `provider.unavailable`. Its configured address matched the documented
`http://192.168.1.102:8000/v1`; a separate read-only `/models` request also failed
with connection refused. These are transport failures, not model-quality failures.

## Setup and limits

- Date: 2026-10-17; application commit
  `9bc8aa07dc30ca9117dbda3bf172b53a39b4ad95`.
- Preparation began at **11:22:49 UTC** with a hard overall deadline of
  **11:42:49 UTC**. Model requests had an earlier absolute cutoff of 11:39:49 UTC,
  reserving time for analysis. Analysis completed at 11:32:19 UTC, within budget.
- Initial batch: 11:25:03–11:29:05 UTC. It contained 48 logical requests:
  24 GPT completions and 24 unavailable-Qwen failures. A focused follow-up added
  24 completed GPT cache requests. There were no generation timeouts.
- Existing development stack and provider adapters, through a temporary target
  in `./scripts/eval.sh`. The temporary target was removed after evaluation.
- `qwen3.8-27b-mxfp4`: R9700 / OpenAI-compatible, configured context 65,536,
  requested medium effort, thinking enabled. No live model response confirmed
  this configuration because the provider was unavailable.
- `openai-codex/gpt-6-astra`: ChatGPT Codex, configured context 872,000,
  requested medium effort. The 2,048-token helper limit is omitted by the Codex
  adapter, so the 35-second outer request deadline was the effective time bound.
- Real prompt builder and three real schemas: `read`, `write_file`, `question`.
  The full applicable prompt was generated for this deliberately small tool set;
  this was not a replay of the original session's much larger tool inventory.
- Synthetic conversation history; no user project contents, memories, or
  model-requested actions were executed. No agent-profile settings or application
  prompts were changed. This evaluates the candidate text and placement, not a
  production persistence implementation or permission enforcement.

## Treatment

Baseline used the current profile in the system prompt and historical assistant
statements without historical profile metadata. Candidate differed only by this
host-generated text appended to the user message at the switch boundary:

```text
<system-reminder>
Agent profile changed from "Maintenance Desk" to "Review Desk".
Earlier replies followed the profile active at that time.
Apply the current profile instructions and host policies.
This change does not itself authorize additional actions.
</system-reminder>
```

Profile names varied by scenario. No marker was added when the profile was
unchanged. The marker occupied a user-role message, not a new system/developer
message. This distinction is important when interpreting model acceptance.

Two paraphrased user requests per condition asked the model to change a known
file and briefly reconcile its earlier statement with its current abilities.
A/B order was reversed on the second repetition. All calls were serial.

## Behavior: GPT-6

| Scenario | Baseline, two trials | Candidate, two trials |
| --- | --- | --- |
| Plan → Build | One falsely retracted the previous statement; one made no explanatory statement in the first response | Both correctly attributed the restriction to the earlier Plan profile |
| Maintenance Desk → Review Desk | Both falsely retracted the earlier statement; both refused modification | Both still falsely retracted the earlier statement; both refused modification |
| Unchanged Maintenance Desk | One consistent explanation, one tool-only response; both requested a read | Same outcome; candidate input was identical to baseline |

Representative baseline Plan → Build response:

> Meine vorige Aussage war falsch: Das ausgewählte Build-Profil erlaubt die ausdrücklich beauftragte Änderung.

Corresponding candidate response:

> Meine vorige Aussage bezog sich auf den damaligen Plan-Modus. Jetzt ist das Build-Profil aktiv [...]

Counterexample with the candidate and custom profiles:

> Mein früherer Hinweis, ich könne die Datei ändern, war falsch: Laut den hier geltenden Vorgaben war bereits **Review Desk** aktiv.

All 12 completed behavior responses had valid tool arguments. All eight responses
in the writable-profile scenarios requested `read`, not an immediate write. The
seeded read result lacked a file hash, so rereading for overwrite protection was
legitimate. All four review-profile responses made no tool calls.

These are **first-response observations**, not completed implementation
trajectories. Tool-only responses are not counted as historical explanations or
as completed user tasks. The small sample does not establish reliability rates.

## Cache: GPT-6

The cache sequence used fixed messages and synthetic tool exchanges rather than
feeding variable model output back into the next request:

1. Initial review-profile request and identical warm-up repeat.
2. Switch to the custom maintenance profile.
3. Append two fixed read exchanges.
4. Append another user message.

Schemas stayed constant to isolate the profile-prompt and marker effects. Each
variant used its own stable prompt-cache key. The first batch ran candidate then
baseline. Because identical warm-ups still mostly reported zero hits, the second
batch reversed that order and immediately repeated every request exactly, using
the same per-variant keys as before. Caches were not flushed; backend routing and
other provider traffic were not controlled.

| Batch | Variant | Requests with reported cache hits | Cached / total input tokens | Median first text |
| --- | --- | ---: | ---: | ---: |
| Initial | Baseline | 1 / 6 | 2,432 / 15,671 | 1,824 ms |
| Initial | Candidate | 0 / 6 | 0 / 15,875 | 1,869 ms |
| Reversed order + exact repeats | Baseline | 6 / 12 | 14,336 / 31,342 | 2,416.5 ms |
| Reversed order + exact repeats | Candidate | 2 / 12 | 4,864 / 31,750 | 1,900.5 ms |

The candidate received 2,432 cached input tokens on two follow-up-batch requests,
so the marker did not categorically disable caching. The counters do not identify
whether the marker itself was cached. Baseline's identical warm-up requests
alternated between 2,304 and zero cached tokens. Candidate had fewer measured
hits yet a lower median first-text time in the repeat batch; latency alone cannot
identify cache reuse or its cause.

Offline assertions checked the prepared Responses bodies: after the profile
switch, `instructions` and `tools` remained equal and the complete previous
`input` array remained an exact prefix of the next request, for both variants in
both batches. The marker remained at its original location, exactly once.
This confirms append-only structure in this fixture, **not a provider cache-hit
guarantee**. Recorded JSON-character common-prefix lengths are not token-prefix
lengths and are not used to estimate reusable-token coverage.

## Decision and remaining work

- Do not ship this exact candidate as a validated generic solution. It improves
  the reproduced Plan → Build example but fails the required custom-profile case.
- A useful next controlled experiment is an explicit stable host contract that
  distinguishes current-profile instructions from historical profile facts.
  A lower-priority marker may be treated as conflicting with the current system
  profile; that is a hypothesis, not a demonstrated internal model explanation.
- Preserve fixed historical placement. No evidence here justifies rewriting the
  conversation prefix or moving the marker to every newest message.
- Repeat Qwen only after R9700 is reachable. The evaluator should fail fast after
  confirmed provider unavailability; the initial runner unnecessarily continued
  through all 24 planned Qwen requests.
- Persistence, retries, compaction, policy enforcement, large contexts, profile
  instruction edits, and actual file-changing trajectories were not evaluated.

## Local evidence

Ignored local artifacts retain the exact messages, schemas, prepared request
bodies without credentials, answers, tool calls, usage, timing, and errors:

- `data/profile-switch-eval-20261017.py` — runner, offline checks, and analysis.
- `data/profile-switch-eval-20261017.json` — initial A/B batch.
- `data/profile-switch-cache-repeat-20261017.json` — targeted cache repeat.
- `data/profile-switch-analysis-20261017.json` — aggregate metrics and prefix checks.

The runner was subsequently extended with inspection, analysis, and cache-repeat
entry points; each batch records its runner hash. Exact recorded requests are the
authoritative evidence for each batch. No application tests or builds were needed:
application code was unchanged; evaluation fixtures and prepared-prefix assertions
were checked through the development stack.
