# Unified context search evaluation, October 2026

## Recommendation

Prefer one small `context_search` tool that searches memories and previous conversations by
default, with optional `scope` and `session_id`. Retain `session_read` for missing conversation
details and keep memory mutations and overview pagination outside the search contract.

The tested local Qwen handled this contract without argument errors and used fewer tool calls.
The result supports implementation of this candidate, not a claim of optimal or universally
reliable behavior. Better conversation retrieval also helped without merging the tools.

## Review fixes

All five follow-up findings were addressed:

- Explicit conversation text search now gives the original query to PostgreSQL's web-search
  parser, retaining dotted IP addresses when embeddings are disabled. Automatic conversation
  retrieval keeps its existing query construction.
- Source permissions are checked before requesting additional approval. An actual
  `USER_DENIED` memory refusal no longer cancels an independently authorized conversation search.
- Conversation discovery again includes owner-filtered title matches. The title query is
  shared with authenticated global search; a title-only hit returns reading arguments rather
  than claiming that a message passage matched.
- The retention fixture requires `47 Tage`, not the unrelated number `47`. The runner's
  self-check rejects a correct-looking answer supported only by numbered filler text.
- Prototype schemas, copied search/session handlers, and obsolete variant branches were
  removed. The runner shrank from **563 to 408 lines**. Both its live path and `--check`
  now dispatch through the same production handlers.

Post-review validation passed: **5,293 backend tests**, with **101 skipped and one expected
failure**; **15 PostgreSQL/Infinity integration tests**; the native evaluator self-check;
backend checks including the strict documentation build; Ruff for the runner; and shell
syntax validation for `scripts/eval.sh`. Regression coverage includes exact IP lookup without embeddings, title-only
discovery without exposing another owner's session, and the real approval-refusal result.

The live model figures below predate these fixes. No additional Qwen live comparison was run
for the review fixes; the recorded evaluation artifacts were preserved unchanged.

## Implementation follow-up

The candidate is now implemented. A follow-up ran on **2026-10-16, 18:38:02–18:40:14 UTC**
against the working tree, using the same Qwen model, requested effort, fixtures, and two
repetitions per scenario. The current runner defaults to this implemented-tool evaluation.
The remaining sections below retain the original prototype comparison as historical evidence.

The follow-up offered production tool schemas and dispatched selected search and session
calls through `tool_registry.execute`, including native argument validation, source
availability checks, SQL retrieval, and result serialization. Permission
callbacks supplied read approval; unrelated actions remained refused. It was not a persisted
full-agent-loop or approval-UI evaluation. Runtime policy and write-repair paths were covered
separately by the backend tests.

| Metric | Implemented tools |
| --- | ---: |
| Successful recall/abstention trajectories | **16/16** |
| Tool calls | **25** |
| Argument validation errors | **0** |
| Model requests | **39** |
| Median tool calls | **1** |
| Median trajectory seconds | **7.518** |
| Total evaluation seconds | **132.053** |

All fourteen known-answer trials completed with the correct supported value. For the two
genuinely unknown-address trials, one stopped with a clarification and the other acknowledged
the missing address in its final answer. Both explicit session searches used `session_id`
correctly. The tool-result-only address was recovered through `context_search` followed by
the real `session_read` in both repetitions. No unnecessary known-answer question occurred.
Incidental answer wording and advice were not scored; this is not a general answer-quality
guarantee or a controlled latency comparison with the earlier prototype.

Backend application source hashes remained unchanged during this live follow-up. All eight
fixture transactions were rolled back, and the independent audit found no persisted synthetic
owners. Raw evidence is in `data/context-search-20261016-implemented.json` and its matching
log. A subsequent runner-only import-format adjustment did not change application behavior.

Implementation validation:

- Complete backend suite: **5,293 passed, 100 skipped, one expected failure**. The first full
  run exposed a prompt-budget overrun; the new hints were shortened and the original budget
  retained before the passing full run.
- The existing two-case memory-mode runtime test was then extended to verify explicit
  conversation search with disabled Memory; both cases passed without another application change.
- PostgreSQL/Infinity RAG integration suite: **14 passed**, including scoped multi-passage search.
- Frontend suite: **1,316 passed**; frontend lint and production build passed.
- Backend check, runner Ruff check, and diff whitespace review passed.

## Setup and preservation

- Date: 2026-10-16; application baseline `f8bcc83` (`Refine file routing and selective task delegation`).
- The working tree was clean at the start. Existing application commits were retained.
- Model: `qwen3.8-27b-mxfp4`, configured provider **R9700**, OpenAI-compatible transport.
- Server-reported model root: `amd/Qwen3.8-27B-Quark-AWQ-MXFP4`; vLLM **0.30.0**;
  maximum context **65,536** tokens. The GPU was identified by the configured provider, not
  independently inventoried in this evaluation.
- Requested reasoning: **medium**, thinking enabled. The application supplied no temperature
  override; server sampling defaults, RNG seeds, GPU contention, and prefix-cache state were
  not controlled. No global model settings were changed.
- Serial live requests through `./scripts/eval.sh context-search` and the configured provider
  adapter. The complete general system prompt and a focused set of real distractor/tool schemas
  were offered, rather than the installation's entire integration catalog.
- Fixtures used synthetic owners, memories, sessions, messages, and embeddings in development
  PostgreSQL. Each scenario ran inside a transaction that was rolled back. Fixture rows were
  invisible to other connections; there was no cross-trial conversational learning.
- All eight scenario transactions were rolled back. An independent database query found
  **zero persisted `context-eval-*` owners**. Backend application source hashes matched before
  and after both live runs. No real user session, memory, credential, or permission was edited.

## Compared contracts

| Variant | Search tools | Conversation retrieval |
| --- | --- | --- |
| `separate_current` | Existing `memory_retrieve` and `session_search` | Current literal substring behavior; latest matching message per session |
| `separate_hybrid` | Same tools; `session_search` gains optional `session_id` | Existing hybrid episode retrieval globally; evaluation-only semantic passage search within a session |
| `unified_hybrid` | Replace both search tools with `context_search` | Same conversation backend as `separate_hybrid`, plus memory retrieval in the default call |

The unified schema requires only `query`. Optional `scope` is `all` (default), `memories`, or
`sessions`; a supplied `session_id` restricts the call to that conversation. Combining a
session ID with `scope=memories` is invalid. Optional `limit` defaults to three results per
source. Results keep `memories` and `sessions` separate and include session reading arguments.

Candidate prompt references were adapted to the offered names. Both hybrid variants clarify
that an already supplied session need not be rediscovered, but may be searched internally.
The unified variant also removes memory-only `mode`, `purpose`, category, and cursor choices
from ordinary search. Consequently this comparison measures a complete simplified search
contract, not the isolated effect of a name change or one optional parameter.

Memory queries used the actual `MemoryRetrievalService`; global hybrid conversation queries
used `EpisodeRetrievalService` with a 0.35 semantic-distance cutoff. Scoped queries returned
multiple real indexed message chunks ordered by embedding distance. Current literal matching,
bounded session reads, and authorization feedback were evaluation implementations. The full
agent loop, approval UI, and production execution of the proposed tool were not exercised.
Unrelated tool actions returned controlled refusal results and never executed.

## Cases and scoring

Eight cases, two repetitions per variant: **48 trajectories**. Variant order was reversed in
the second repetition. Each trajectory allowed six model responses and at most 180 seconds;
individual provider requests were capped at 90 seconds and 4,096 output tokens. Parallel tool
calls counted individually. The suite had a 30-minute outer limit.

1. A filename-date preference available only in durable memory.
2. A NAS address available only in a previous user message.
3. A project directory in an English conversation, requested in German.
4. A port decision deep in a 163-message session, with an initial reference near its beginning.
5. An explicit request to search a known 143-message session for a backup retention limit.
6. Two similarly named devices with different addresses.
7. An address genuinely absent from both sources.
8. An address present only in a historical tool result, adjacent to searchable conversation text.

Known-answer success required a completed final response containing the expected value,
supporting tool-returned evidence, and no intervening user question. Merely retrieving the
answer without finishing did not pass. The two-device case additionally checked that the
first returned URL belongs to the requested device. The absent-answer case required an
honest unresolved outcome without an invented URL. Final answers were manually reviewed;
an independent audit verified that the retention evidence contained actual days, rather than
matching a numbered filler message. Incidental advice, exact wording, localization, and all
other general prompt obligations were not comprehensively scored.

## Verified results

The reported comparison ran **16:33:17–16:44:57 UTC**, taking **700.107 seconds**. All 48
trajectories reached a recorded terminal evaluation outcome, with **166 model requests**:
45 final answers, two user-question stops, and one model-round-limit stop. There were no
provider timeouts or errors. One question concerned genuinely missing information;
the other was an unnecessary repeat request for an address present in a prior conversation.

| Metric | Separate current | Separate hybrid | Unified hybrid |
| --- | ---: | ---: | ---: |
| Successful recall/abstention trajectories | 14/16 | 15/16 | **16/16** |
| Total tool calls | 66 | 46 | **37** |
| Median tool calls per trajectory | 4.5 | 3 | **2** |
| Argument validation errors | 8 | 10 | **0** |
| Model requests | 62 | 55 | **49** |
| Median wall seconds per trajectory | 13.287 | 13.241 | 12.851 |

The unified contract used approximately **44% fewer tool calls** than the current contract,
and approximately **20% fewer** than separate hybrid search. Median latency differences were
small and do not establish a dependable speed advantage.

| Scenario | Separate current | Separate hybrid | Unified hybrid |
| --- | ---: | ---: | ---: |
| Memory only | 2/2 | 2/2 | 2/2 |
| Conversation only | 0/2 | 2/2 | 2/2 |
| Cross-language directory | 2/2 | 2/2 | 2/2 |
| Long session, natural recall | 2/2 | 1/2 | 2/2 |
| Explicit known-session search | 2/2 | 2/2 | 2/2 |
| Similar devices | 2/2 | 2/2 | 2/2 |
| Genuinely unknown | 2/2 | 2/2 | 2/2 |
| Tool-result-only address | 2/2 | 2/2 | 2/2 |

### What changed in the trajectories

- **Concrete premature question reproduced:** current search asked for the NAS address after
  unsuccessful memory/literal queries. In the other repetition it eventually retrieved the
  address, but exhausted the six-response budget without delivering a final answer. Both hybrid
  variants answered correctly in both repetitions.
- **Simpler arguments mattered:** all eight current-contract argument errors were invalid
  `purpose` values: Qwen supplied an explanatory sentence instead of `context` or `write`.
  Separate hybrid had nine such errors and one question-batch error. Field-specific host-like
  feedback allowed repairs. No invalid `scope`, session ID, or other argument occurred with
  the unified contract in this sample.
- **Scoped search was usable, not automatic:** each hybrid variant made three correctly scoped
  searches: both explicit-session trials and one of the two natural long-session trials.
  In the other natural trial, Qwen still paginated. Offering `session_id` does not guarantee
  that the model chooses the shortest route.
- **Existing behavioral weakness remains:** separate hybrid once read only the initial page
  of the long session and incorrectly concluded that no port had been discussed. Search
  capability does not itself guarantee that the agent continues researching.
- **Tool-only knowledge was recoverable indirectly:** no variant indexed tool-result contents.
  The surrounding searchable Helios conversation led to `session_read`, which returned the
  address. Unified search used two calls per repetition, compared with five for current
  search. This does not prove recovery when no surrounding searchable conversation exists.

## Pilot and limitations

A preceding 42-trajectory pilot is retained in `data/context-search-20261016.json` but excluded
from the tables. It exposed evaluator fidelity issues: argument errors initially omitted
`error.field`, memory category filters were not applied, and the long-session fixture fit
inside one maximum-size read. The pilot's device scorer also incorrectly rejected correctly
labeled comparisons mentioning the other device. These issues were corrected before the
verified run; the explicit scoped-search case was added. Pilot and verified results must not
be pooled, and the final runner is not a byte-identical reproduction of the pilot runner.

The corpus is deliberately small and mostly sparse. Two samples per scenario do not estimate
a production success rate, and no statistical significance or globally optimal contract is
claimed. There was no comparison of alternative tool names, `scope` enum spellings, large
mixed-topic corpora, cloud models, unavailable-source permissions, malicious history, stale
conflicts, or live device connectivity. Server defaults and caching were not reset between
trials. Tool prototypes and adapted prompts were not deployed into MatoLoop.

The scoped prototype uses semantic search only; exact-identifier lexical matching and the
existing ownership, permission, trust, and result-sanitization contracts remain implementation
requirements. The evaluation is not evidence that those boundaries are already implemented
for a merged production tool.

## Next implementation decision

Implement the tested small search surface: `context_search(query, scope=all, session_id?)`,
separate source arrays, bounded matching passages, and ordinary `session_read` for omitted
details. Keep ordinary search free of memory-write preparation fields. Clarify the difference
between rediscovering a session and searching inside it. Preserve the existing source-specific
permission checks; do not add a host gate pretending to know whether the answer already exists.

## Reproduction and artifacts

These commands now exercise the implemented tools. The older prototype trajectories and
schemas remain in the recorded raw files; their live figures are not rerun by the current default.

```bash
./scripts/eval.sh context-search --check
./scripts/eval.sh context-search --output /workspace/data/context-search-new-run.json
```

- Runner: `scripts/context-search-eval.py`; entry point added to `scripts/eval.sh`.
- Verified raw evidence: `data/context-search-20261016-verified.json` and matching `.log`.
  Includes fixtures, initial messages, schema sets, every model/tool round, usage, source
  hashes, server metadata, timings, scoring, and rollback completion.
- Independent review helpers: `data/context-search-summary-20261016.py` and
  `data/context-search-audit-20261016.py` (ignored local artifacts).
- Validation: runner retrieval/schema/rollback self-check passed, Ruff passed for the runner,
  and `bash -n scripts/eval.sh` passed. No shared application prompt or schema was changed;
  application tests and UI checks were not required for this evaluation-only change.
