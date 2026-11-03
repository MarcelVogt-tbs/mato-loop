# Session-summary prototype evaluation — November 2026

The initial sections record a retrieval prototype; the incremental follow-up below evaluates the later implementation candidate. These are dated measurements, not performance guarantees. The current Memory contract remains in [memory.md](memory.md).

## Question and method

Can source-linked summaries improve follow-up answers or reduce original-session reads using the configured `halogen-qwen3.8-flash-next` model on the development stack?

The evaluator uses the enabled Halo-Flash OpenAI-compatible provider through `ProviderService`, without changing its configuration. Each comparison uses the same question, production prompt builder, tool schemas, original transcript, and automatic context ceiling. The ceiling uses default Memory/runtime settings and the selected model's character estimate: 1,024 estimated tokens, or 4,096 characters for this binding. This is not an exact tokenizer count.

- **Baseline:** native hybrid passage retrieval and the recent project-session index, serialized with the production automatic-context budgeting helper.
- **Summary:** the same selected-source context plus an independently generated summary. Snippets are displaced only if necessary to stay within the same ceiling. This tests augmentation, not a summary-only replacement or summary-search ranking.
- **Read access:** native `context_search` and `session_read`, with read approvals supplied by the evaluator. Each trajectory has a fresh tool context, including its own pagination state. This is not a persistent AgentRuntime, permission-approval, or UI test.
- **Generation:** an isolated prompt sees the original conversation, not existing memories or a previous summary. Its bounded output distinguishes decisions, implementation, verification, superseded statements, and open work. The host validates the schema and cited message IDs, attaches a coverage watermark, and rejects invalid or oversized output rather than weakening validation.
- **Isolation:** synthetic owner-bound project sessions are created in rollback-only transactions. Source sessions are explicitly public for the public retrieval scope. Existing sessions, memories, Dreaming settings, and source code under `apps/` are not modified.

Three fixtures cover four states, each repeated twice:

1. A proposed port is corrected, the user confirms implementation, and a named test remains open.
2. The same source session continues with another port, user-confirmed verification, and a new open test. Its summary is regenerated from the complete original snapshot.
3. An assistant claims successful tests; the user explicitly says there is no verification evidence.
4. A quoted external message contains an address and hostile instructions, but does not establish the user's deployment state or address.

Each fixture also contains unrelated layout discussion. Follow-up answers have known exact JSON field values. Strict scoring requires a JSON response; content scoring additionally accepts a single whole-response Markdown JSON fence. Incorrect values and malformed responses remain failures. Pair order alternates between repetitions.

## Results

The final isolated run completed on November 3, 2026, from 13:40:04 to 13:47:32 UTC. Its base commit was `74fab40edd8dd90111eade760853f647e0fcadd1`; the evaluator was an uncommitted addition. Its recorded runner SHA-256, verified against the file after execution, is `cb4a5e072a284750af4919e607ff315f96991e5a19d2256ff557ee14f9252b51`.

| Measure across eight follow-up answers | Baseline | With summaries |
| --- | ---: | ---: |
| Content-correct answers | 8/8 | 8/8 |
| Correct requested fields | 26/26 | 26/26 |
| Strict JSON-format passes | 5/8 | 4/8 |
| Tool calls | 14 | 8 |
| Tool errors | 0 | 0 |
| Provider input tokens | 84,750 | 59,776 |
| Provider output tokens | 4,077 | 3,640 |
| Provider total tokens, excluding summary generation | 88,827 | 63,416 |
| Provider cached input tokens, included in input above | 34,176 | 37,312 |
| Median answer time | 9.92 s | 8.26 s |
| Total answer time | 84.66 s | 75.02 s |

All strict-format failures were whole-response Markdown JSON fences containing the correct values, not incorrect state or fabricated addresses. Content scoring preserves this distinction without treating format failures as strict passes.

All eight final summaries passed schema, citation-ID, and context-budget checks. Inspection found that they retained the requested corrections, implementation/verification distinction, and open-work identifiers. Both continued-session summaries recorded port `20563` and `RESTART-LOAD`, rather than retaining the previous port and test. The unverified assistant claim remained unverified, and the external quoted address was not adopted as the user's address. This is bounded fixture evidence, not a general factuality or injection-resistance guarantee. Summaries also retained unnecessary layout discussion.

Summaries reduced follow-up tool calls by 43% and provider total tokens by 28.6%, with unchanged content correctness. The largest read reduction was in the unverified-test fixture: both candidate answers needed no original-session read. Other candidates still read evidence or searched for newer state. The measured latency difference is descriptive, not a stable performance guarantee.

### Generation changes the cost calculation

The eight generation requests consumed another **42,783 provider tokens** (24,974 input and 17,809 output, with reasoning already included in output) and **287.14 seconds**. Per-summary generation took **20.20–52.59 seconds**, median **38.87 seconds**. No generation repair requests were made.

Counting one fresh summary for each compared answer gives **106,199 tokens** for the summary arm, versus **88,827** for the baseline: **19.6% more**, not a saving. At the observed average follow-up saving, the generation token overhead would be amortized after roughly two comparable uses of an unchanged summary. That is arithmetic over this sample, not a measured multi-query reuse result or a monetary break-even claim.

### Decision

The prototype supports evaluating **background-generated, reusable snapshots**, not generating a summary for each request. It does not demonstrate better answer quality because the baseline was already correct on every content case. Do not replace original-session retrieval or claim a general improvement from these eight comparisons.

The initial evaluation made no production Dreaming, schema, or prompt changes. At that point the remaining integration work was to persist a source-bound snapshot, regenerate it when its covered source changes, and release it only through the existing owner/project/trust and context-budget boundaries. Testing reuse and selection across multiple longer sessions remains necessary before making broader performance claims.

All three synthetic fixtures were rolled back, and the final cleanup query found **zero remaining synthetic owners**. Local scoring checks, Ruff on the new runner, shell syntax validation, and a diff review passed; no application-wide test suite or UI validation was run because application code was unchanged.

## Incremental implementation follow-up

On November 3, 2026, 14:47:54–14:51:35 UTC, `session-summary --incremental` used the production incremental prompt, schema, evidence validation, Dreaming reasoning-effort preference, and bounded summary projection. Each trajectory retained its independent tool context and the existing project-local session index. The raw artifact is `data/session-summary-qwen-flash-next-20261103-incremental.json`, runner SHA-256 `afc3f0d492834c01c9534b15b675c16410159a7bbd8e2db6284c6973528b8d9a`.

For both continued-session repetitions, generation received the previous summary and only **five new messages**, rather than the complete 28-message conversation. Both retained the new port `20563`, successful user-reported `COLD-BOOT`, and open `RESTART-LOAD` check.

| Measure across eight follow-up answers | Baseline | Incremental summaries |
| --- | ---: | ---: |
| Content-correct answers | 8/8 | 8/8 |
| Strict JSON-format passes | 7/8 | 4/8 |
| Tool calls | 11 | 7 |
| Tool errors | 0 | 0 |
| Provider total answer tokens | 78,599 | 56,635 |
| Median answer time | 8.88 s | 10.49 s |

Seven of eight generation responses produced an acceptable summary. One returned no required tool call; validation rejected it and retrieval fell back to the original snippets. All requested content remained correct, including that fallback; strict failures were Markdown JSON fences. This does not establish an eight-of-eight summary-generation success rate.

The eight generation requests consumed **24,917 tokens**, median **6.89 seconds**. Generation used zero reasoning tokens with the production Dreaming preference for no reasoning when supported; do not attribute its timing difference from the initial evaluation solely to incrementality. Including generation yields **81,552 tokens**, about **3.8% more** than baseline for one use each. Follow-up answer tokens fell by about **28%** and tool calls by **36%**, but latency did not improve in this sample. Reuse remains important.

This is a provider-level comparison using production generation contracts and serialization, not a full persistent AgentRuntime benchmark or a live retry/approval test. PostgreSQL tests separately verified incremental input, unchanged checkpoints after rejected output, owner/project/trust filtering, and exact serialized Chat Completions/Responses prefixes across tool rounds and database-loaded follow-ups. The complete backend suite with Memory PostgreSQL cases passed **5,892 tests** (52 optional tests skipped); the frontend suite passed **1,395 tests**, and its lint/build passed. The migration was generated, reviewed, and applied to the development database only.

UI interactions were checked with an isolated disposable account: the summary switch, recent-run link, desktop table, run details, original-message links, and the 390-pixel mobile card layout. DOM checks found no horizontal overflow, including the dark-mode detail view. Screenshot capture was unavailable because the browser window was not visible; this was not a pixel-level visual review. The synthetic account and its data were removed after verification. No existing account settings were changed.

## Run the current comparison

The runner now evaluates only the production incremental contracts. The prototype path and `--incremental` switch were removed; this command runs a new comparison, not an exact reproduction of either historical runner hash above.

```bash
./scripts/eval.sh session-summary --check
./scripts/eval.sh session-summary \
  --output /workspace/data/session-summary-current.json
```

The runner refuses to overwrite an existing output. Model requests may incur provider charges; `--check` makes none. The live budget defaults to 20 minutes. Raw reports remain under ignored `data/` paths and contain synthetic transcripts, schemas, prompts, summaries, citations, provider usage, tool results, timing, and cleanup status.

Two precursor reports are preserved but excluded from comparative conclusions:

- `data/session-summary-qwen-flash-next-20261103.json`: the reused fixture helper left source-session trust unset. Public automatic retrieval therefore received no source context, while the candidate received a summary. This was not a fair comparison. One generated summary also cited nonexistent message IDs and was correctly rejected.
- `data/session-summary-qwen-flash-next-20261103-public.json`: source visibility was corrected, but the evaluator reused tool contexts across trajectories. Pagination state leaked into five candidate reads and caused `tool.cursorInvalid` repairs. Its efficiency figures are not a clean comparison.

These precursor runs made additional model requests; their usage is not part of the final comparison. Their synthetic-owner cleanup checks both reported zero remaining rows. No production permission or cursor behavior was changed to accommodate the evaluator.

## Limits

This is a small synthetic comparison with one selected source session per fixture, not a statistically representative quality or load benchmark. Original transcripts fit within a small number of reads. Results do not establish behavior across many sessions, very long histories, summary-index ranking, tool-output evidence, or production workloads.

The fixture helper supplies historical source timestamps. Requests for the latest state can therefore prompt additional freshness checks even when a summary already contains the requested answer. Provider-reported cache usage and remote serving conditions also affect observed timing and token billing. Token totals are not monetary costs; input, cached-input, reasoning, and output pricing may differ.

Schema and citation-ID validation do not prove that each cited passage semantically supports its item. The current integration contract and its source, privacy, scheduling, and cache boundaries are documented in [memory.md](memory.md#session-summaries); these historical measurements do not establish their end-to-end behavior.
