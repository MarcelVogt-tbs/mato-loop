# Browser Performance

## Decision

Optimize completed browser work per model request, not just individual Playwright calls.
Keep fresh approval/binding checks, but produce settled observations, identify stale page
state separately from detachment, and authorize bounded form batches as exact ordered lists.
Full snapshots remain canonical. Incremental text is a request-local projection using Python's
standard-library unified diff, not a second accessibility implementation or a persistent MCP
tracking baseline. Missing context always falls back to full observations.

The comparison reference is Playwright MCP inside Playwright `v1.58.0`, matching the agent pin:
[form batching](https://github.com/microsoft/playwright/blob/v1.58.0/packages/playwright/src/mcp/browser/tools/form.ts),
[completion waiting](https://github.com/microsoft/playwright/blob/v1.58.0/packages/playwright/src/mcp/browser/tools/utils.ts),
and [response tracking](https://github.com/microsoft/playwright/blob/v1.58.0/packages/playwright/src/mcp/browser/tab.ts).
MatoLoop keeps Python Playwright, not the official MCP runtime or its fixed waits. Public
`browser_*` tools now have small per-operation schemas. `browser_run_code` uses Python
`async def run(page)` rather than JavaScript Playwright snippets; `browser_evaluate` runs
JavaScript in Chromium. Both use the explicitly accepted read/write/code permission scope,
AUTO for both trust levels. Normal actions no longer require whole-page source hashes;
only actual approval waits capture guards. See [Code Authority](browser.md#code-authority).

## Observed Bottleneck

A development session examined directly in PostgreSQL and container logs on 2026-09-10
had 29 browser model requests, always one browser call per response. Provider phases totaled
256.884 seconds; median request duration was 8.649 seconds. Context preparation totaled
1.455 seconds. Browser coordinator authorization plus execution totaled 9.837 seconds,
including one 8.022-second failed key action. Six calls failed: three stale snapshots,
two action failures and one unsupported inspect selector. There were no screenshots,
pseudonymization or compactions in this section. About 84% of input tokens were reported cached.

Stored observations showed expanded custom selects before their options appeared and
closing lists still present after selection. All three rejected source hashes had been
copied correctly from the latest observation. This supports fixing observation timing and
recovery rounds rather than removing authorization or attributing all delay to Chromium.
Provider duration includes final local preparation/authentication, not just remote inference.

## Bounded Comparison

`mato_loop_backend.browser_perf_eval` is opt-in and uses the selected active owner's
effective Codex model and existing credentials. It never invokes a browser, creates a
session, modifies the measured session, or saves model settings. Database access is read-only;
credentials needing renewal block the evaluator rather than triggering refresh. Authentication
may be renewed separately through the normal provider path before evaluation.

Four cells compare the previous single-field tool contract with `fill_form`, at `medium`
and `low` reasoning. Each starts with the same three synthetic textboxes. Baseline multi-call
responses are allowed; source-snapshot invalidation is simulated as in the real old contract.
The test stops when the three values match, excluding a subsequent natural-language confirmation.
Limits: at most 12 model requests, 45 seconds each, 360 seconds overall, no retries. Native
Codex does not transmit an output-token cap. Cancellation does not guarantee cancellation of
remote processing. Output contains safe metrics, not credentials or raw provider responses.

Run in the development stack, with explicit owner and model IDs:

```bash
bash -c 'source ./scripts/dev.sh help >/dev/null && compose exec -T backend /opt/mato-loop/backend/venv/bin/python -B -m mato_loop_backend.browser_perf_eval --live --owner-id "$1" --model-id "$2"' sh OWNER_UUID MODEL_UUID
```

One serial four-cell sample on 2026-09-10, using `openai-codex/gpt-5.6-sol`:

| Mode | Reasoning | Requests | Tool errors | Values matched | Provider time |
| --- | --- | ---: | ---: | --- | ---: |
| Individual fills | medium | 4 | 2 | yes | 34.390 s |
| `fill_form` | medium | 1 | 0 | yes | 7.839 s |
| Individual fills | low | 4 | 2 | yes | 32.151 s |
| `fill_form` | low | 1 | 0 | yes | 7.419 s |

Ten requests were issued, all reporting zero cached input tokens. The baseline attempted
three same-snapshot calls in its first response; two were rejected after the first edit.
The sample shows the benefit of a real batch, not a statistically established speedup for
general browsing. Smaller reasoning effort had only a modest effect in this sample. No
default reasoning settings were changed.

A read-only replay of the recorded 29-call browser section through the new observation
projection encoded eight results as diffs. Tool-result text fell from 107,847 to 77,553 UTF-8
bytes (28.1%), with the canonical messages unchanged. This measures tool-text reduction,
not total provider tokens or inference latency; full recovery inspections remain full.

Separately, a disposable headed Chromium fixture measured three sequential worker fills
with observations at 592.0 ms versus one three-field batch/final observation at 257.3 ms.
These are worker measurements, not full LLM-to-browser end-to-end timings. The fixture also
checks delayed dialog/listbox transitions, fetch/navigation activity, instability bounds,
target replacement, partial completion and lost acknowledgement. An additional network-blocked
headed fixture loads the actual installed Vue 3.5.13 / Quasar 2.18.2 bundles and production CSS
from the frontend dependency volume mounted read-only. Dialog refs, Project/Status select
options, selection results, closing lists and stable repeat hashes pass at 1280x800 and
390x844 in light and dark mode, with default Quasar transitions.

Run that optional real-Quasar check through the same development stack:

```bash
bash -c 'source ./scripts/dev.sh help >/dev/null && compose run --rm --no-deps --volume mato-loop_frontend_node_modules:/test-node-modules:ro --entrypoint sh matoloop-agent -lc "uv sync --frozen && uv run --no-sync python -m unittest discover -s tests -p test_browser_observations.py -v"'
```

The mount name above uses the default Compose project name. The test is skipped when the
assets are not mounted; its isolated non-root browser subprocess executes the headed cases.

## Limitations

- Settling cannot predict a timer that changes the page after the quiet window. Long polling
  or continuously changing pages can return an explicit unstable-observation result.
- Custom comboboxes are not native selects and still require observed click/keyboard targets.
- Whole-page guards are reserved for actual approval waits. Native form batches still use
  per-target checks between their own edits and are not transactional. Free Python workflows
  have the worker account's rights, not those per-field guarantees.
- Cross-origin frame and shadow-DOM behavior needs additional headed coverage.
- A fresh model decision still takes seconds with the measured provider. Subsecond steps
  inside a batch are not subsecond model decisions.

## Narrow Interface Comparison

`--interface legacy` retains the previous schema comparison above; it excludes the newly
added internal code/typing/tab-selection arguments to keep that baseline reproducible.
`--interface narrow` compares that legacy baseline against the current `browser_snapshot`,
`browser_type` and `browser_fill_form` tools. The current path has no source-hash precondition.
The evaluator offers only this three-textbox subset: it never executes generated Python or
JavaScript in the backend, and does not measure arbitrary code workflows, submission, typing
character by character, navigation or the complete real browser UI. Those behaviors are
covered separately by isolated headed worker tests.

```bash
bash -c 'source ./scripts/dev.sh help >/dev/null && compose exec -T backend /opt/mato-loop/backend/venv/bin/python -B -m mato_loop_backend.browser_perf_eval --live --interface narrow --owner-id "$1" --model-id "$2"' sh OWNER_UUID MODEL_UUID
```

One additional four-cell sample on 2026-09-10 with the same `openai-codex/gpt-5.6-sol` model:

| Mode | Reasoning | Requests | Tool errors | Values matched | Provider time |
| --- | --- | ---: | ---: | --- | ---: |
| Legacy individual fills | medium | 4 | 2 | yes | 33.621 s |
| Narrow tools | medium | 1 | 0 | yes | 5.040 s |
| Legacy individual fills | low | 4 | 2 | yes | 31.971 s |
| Narrow tools | low | 1 | 0 | yes | 5.853 s |

All ten calls reported zero cached input. Compact schema JSON for this subset fell from
3,584 to 2,508 bytes; the complete installed toolset is larger and is not represented by
that comparison. First-call output was 112 tokens for narrow/medium versus 377 for
legacy/medium. These are single synthetic samples, not a claim of production-wide speedup
or evidence that LOW is always faster. No session history or reasoning default was changed.
