# Memory extraction prompt evaluation — 4 November 2026

## Result and recommendation

An explicit distinction between **durable knowledge about the user** and **specifications of an artifact being designed** substantially improved `qwen3.8-27b` in this bounded experiment. A short generic warning did not reliably solve the reported failure.

The initial measurements below used server-default reasoning with thinking active, not the then-production forced-off mode. They must not be treated as results for every reasoning level. The later explicit-off comparisons still produced false-positive memories; their raw artifacts are `data/memory-extraction-20261104-production-none.json` and `data/memory-extraction-20261104-production-none-holdout.json`. An additional explicitly configured medium run was interrupted and supplies no completed result. Further model evaluations were stopped at the user's request. Memory/Dreaming now has its own reasoning selector and uses the model/provider default when no supported override is set.

Recommend the tested refined boundary as a candidate for the isolated extraction prompt, not a general ban on project or automation sessions. Do not delete existing memories or change manual writes, reconciliation, retrieval, or session summaries. The production prompt was **not changed** by this evaluation.

Across the refined main comparison and separately worded follow-up cases:

| Measure | Current prompt | Refined addition |
| --- | ---: | ---: |
| Correctly empty negative responses | 40/54 | **54/54** |
| Positive responses retaining the expected information, reviewed across all candidates | 65/66 | **66/66** |
| Strict automated passes, including category and exact candidate-count expectations | 102/120 | **116/120** |

These denominators count repeated responses, **not independent facts or users**. The result supports a narrowly scoped change; it does not guarantee zero false positives or zero lost memories in other conversations or models.

## Reported failure and tested boundary

The debug MCP source investigation found two different situations:

- A real Webapp message described a lightweight review UI and said the user was unsure whether image upload was needed. The saved memory turned this into a definite personal preference against uploads. Later conversation requested uploads and manual editing. These are evolving application requirements, not evidence of a general personal working style.
- The release-watcher source was an automation instruction with notification conditions and an explicit stop after official confirmation. Its repetition came from execution, not repeated independent expressions of a personal goal.

The refined addition excludes application requirements, feature choices, implementation plans, review workflows, and automation instructions **as such**, across all categories. It explicitly preserves existing equipment and lasting setups, enduring constraints, relationships, general working styles, and long-term involvement and project purpose. One clear statement still suffices; mixed messages must keep the durable facts while omitting task details.

The exact tested text is `REFINED_ADDITION` in [`scripts/memory-extraction-eval.py`](../scripts/memory-extraction-eval.py). In the experiment it was appended to the unchanged `DREAMING_SYSTEM_PROMPT`. Moving, shortening, or merging that text into the existing prompt would be a different, unmeasured variant.

## Method

- Endpoint: `http://192.168.1.102:13306/v1`; requested model: `qwen3.8-27b`.
- Run through `./scripts/eval.sh memory-extraction`, which delegates to the development Compose backend container. No production stack or application database was used.
- Each stage made serial, paired requests in alternating variant order, with three repetitions. Both variants received identical source batches and tool schemas; only the system-prompt addition differed.
- Production message serialization, extraction tool schema, OpenAI-compatible provider transport, `DreamCandidates` validation, and existing prompt-evaluation scoring were reused. Multi-turn cases used distinct message IDs and timestamps. Evidence had to reference supplied user messages.
- Source batches used synthetic IDs and timestamps for the selected user messages, including repeated watcher instructions and synthetic assistant status replies in that case. Automation-origin metadata and separately configured `dreaming_assistant` replies were not supplied. The existing exclusion fixture therefore tested its user quotation/hypothesis, not its separately configured assistant claim.
- `tool_choice=required`, non-streaming requests, parallel tool calls disabled, and a 4,096-token output limit. Temperature, seed, and reasoning settings were left to server defaults. Repetitions were not seed-controlled.
- This measured **first-attempt extraction**, not the persistent Dreaming worker, output repairs, reconciliation, approval policies, or memory retrieval. Model tool calls were never executed.
- Raw artifacts preserve the exact prompts, fixture expectations, normalized tool calls, validation failures, usage, and input hashes. The holdout artifact additionally records the evaluator file hash. No existing memories, settings, or sessions were read or changed by the evaluator.

The main set had 28 cases: 14 negative and 14 positive/mixed. Fourteen came from existing `memory_plan_dreaming_*` fixtures. Added cases covered the observed Webapp source, a later requirement change, assertive app specifications, original and repeated launch-watcher instructions, a print watcher, tentative preference, genuine general preferences, relationships, a stable home-automation setup, long-term project involvement, and mixed durable/task information.

The separately worded follow-up set had 12 cases: four negative and eight positive/mixed. Its prompt was frozen before those cases were run. It checked unrelated dashboard/editor requirements, a temporary train alert and demo constraint, an existing Immich installation, profession, allergy, code-review preference, lasting confidentiality constraint, a multi-year personal goal, default browser mixed with a demo task, and long-standing volunteer project involvement mixed with a ticket. These cases were authored after inspecting the initial results; this was not an independent or blinded benchmark.

## Results by stage

| Stage and variant | Correct negatives | Strict positive passes | Strict total |
| --- | ---: | ---: | ---: |
| Initial: current | 34/42 | 38/42 | 72/84 |
| Initial: short addition | 35/42 | 39/42 | 74/84 |
| Refined: current | 34/42 | 39/42 | 73/84 |
| Refined: explicit boundary | **42/42** | 38/42 | **80/84** |
| Follow-up: current | 6/12 | 23/24 | 29/36 |
| Follow-up: explicit boundary | **12/12** | **24/24** | **36/36** |

Total: **408 model requests**. Each stage's baseline was rerun; the tables do not compare a new candidate only against an older cached score.

The short addition still extracted the original Webapp requirements in every repetition. It slightly helped the assertive formulation but was not sufficient to recommend. Both initial variants also produced one malformed first-attempt tool response on a negative case; these remained failures, not successful filtering.

In the refined main comparison, all nine Webapp checks (original source, changed requirements, assertive formulation; three repetitions each) were correctly empty for the candidate. The current prompt failed eight of those nine. In the follow-up, all six dashboard/editor requirement checks failed under the current prompt and passed under the refined addition, including a product specification using the word “dauerhaft”.

The release-watcher cases were already correctly empty under the current prompt in this evaluation. The screenshot's watcher memory was therefore **not reproduced with this model and these bounded batches**. Do not infer that automation extraction is fixed for other models or full histories.

### Did valid memories disappear?

No expected durable information disappeared with the refined candidate in the 66 positive responses across the main and follow-up sets. Manual inspection checked the extracted text, not just whether some candidate existed. Hardware names, versions, quantities, relationships, project involvement, enduring constraints, and long-term goals remained present. Mixed inputs kept the durable fact and omitted the task-specific details.

Four strict refined-main failures were not content loss:

- Three Rust-experience responses used `context` instead of the fixture's required `fact`. The current prompt made the same category error in all three corresponding responses.
- One response split “prefer CLI tools; browser only for review” into two preference records. Both propositions survived, but the unchanged automated expectation required one record containing both terms.

The assertions were **not weakened** to turn these into passes. Content retention is a separate, manually reviewed measure. Categories, phrasing, grammatical gender, and statement splitting still varied; generated memory text is not guaranteed to be an exact or inference-free rendering of the source.

The current prompt lost the volunteer-project fact in one follow-up response; the refined prompt retained it in all three. This is useful evidence against a blanket project-information exclusion, not proof that the new prompt universally improves recall.

### Request size and observed duration

For the refined main stage, mean provider-request duration was 5.36 seconds for current and 3.29 seconds for candidate. Reported input tokens increased from 111,162 to 134,430; output tokens fell from 47,342 to 28,791. This is an observation, **not a latency, cost, or cache-hit guarantee**; server load and sampling were uncontrolled.

## Reproduce or continue

Check scoring and paired inputs without model requests:

```bash
./scripts/eval.sh memory-extraction --check
```

Run the comparisons against an available endpoint, using unused output paths:

```bash
./scripts/eval.sh memory-extraction --runs 3 \
  --base-url http://192.168.1.102:13306/v1 --model qwen3.8-27b \
  --output /workspace/data/memory-extraction-new-initial.json

./scripts/eval.sh memory-extraction --runs 3 --refined \
  --base-url http://192.168.1.102:13306/v1 --model qwen3.8-27b \
  --output /workspace/data/memory-extraction-new-refined.json

./scripts/eval.sh memory-extraction --runs 3 --refined --holdout \
  --base-url http://192.168.1.102:13306/v1 --model qwen3.8-27b \
  --output /workspace/data/memory-extraction-new-holdout.json
```

Local raw artifacts, intentionally ignored by Git:

- `data/memory-extraction-20261104.json`
- `data/memory-extraction-20261104-refined.json`
- `data/memory-extraction-20261104-holdout.json`

If adopting the refined text, change only the isolated extraction contract and its affected documentation/expectations, then run the affected backend suite and required cache regressions. A changed extraction prompt intentionally invalidates that helper's own reusable prefix, not retained chat history. Keep the negative and positive fixtures together; do not optimize only for empty output. Explicit forgetting and other unmeasured boundaries still need their existing coverage.
