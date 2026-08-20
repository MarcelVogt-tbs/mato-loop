# Prompt and tool-schema slimming evaluation, August 2026

## Decision

Deploy the later V2 prose-only candidate. Keep the earlier structural, aggressive, and conservative
candidates rejected: they reduced input tokens, but did not preserve quality strongly enough.

The evaluation used `Qwen3.6-35B-A3B-MTP-GGUF` through the Lemonade-compatible endpoint at
`http://192.168.1.32:13305/v1`. Candidate transformations existed only in the prompt evaluator until
the V2 gates passed; V2 wording is now the production wording.

## Static results

The representative evaluator fixture exposed 35 effective tools.

| Candidate | Schema tokens | System tokens | Combined | Change |
| --- | ---: | ---: | ---: | ---: |
| Current | 6,410 | 2,464 | 8,874 | - |
| Aggressive combined | 5,288 | 1,722 | 7,010 | -21.0% |
| Conservative combined | 5,506 | 2,191 | 7,697 | -13.3% |
| Structural schemas, `question` unchanged | 6,059 | 2,464 | 8,523 | -4.0% |
| V2 prose-only | 6,133 | 2,055 | 8,188 | -7.5% |

Static size reduction was not an acceptance criterion by itself. Live quality gates remained
authoritative.

## Live results

### Aggressive and conservative candidates

The aggressive schema candidate passed 17/24 targeted trajectories versus 20/24 for current.
Changing the `question` schema reduced the missing-calendar-details case from 3/3 to 1/3.
Compact automation descriptions and null semantics also produced invalid calls.

The conservative prompt candidate passed 15/18 trajectories versus 17/18 for current. It reduced
input by 4.5%, but increased median elapsed time from 6.732 to 8.343 seconds. The hostile-memory
case fell from 3/3 to 1/3. Removing the repeated current-turn citation reminder also produced an
immediate exact-citation regression.

### Final structural production candidate

The final candidate removed `default: null`, inlined definitions referenced once, and removed
redundant discriminator mappings, while preserving the complete `question` schema.

The one-run all-general comparison covered 120 cases per arm:

| Arm | Passed | Input tokens | Median elapsed |
| --- | ---: | ---: | ---: |
| Current compact candidate | 98/120 | 159,060 | 4.969 s |
| Expanded production baseline | 97/120 | 161,682 | 5.361 s |

The 15 disagreement cases were then repeated four times per arm:

| Arm | Passed | Input tokens | Median elapsed |
| --- | ---: | ---: | ---: |
| Current compact candidate | 38/60 | 84,636 | 8.653 s |
| Expanded production baseline | 45/60 | 85,864 | 8.447 s |

Combined evidence was 136/180 for the compact candidate and 142/180 for the expanded baseline.
The 1.4-1.6% input reduction did not justify the six-pass quality regression, so the production
change was reverted.

### V2 prose-only candidate

V2 preserved schema structure and all security, citation, memory, skill, identity, and trust rules.
It shortened redundant descriptions and repeated prompt wording. The expanded targeted 3x gate
passed 37/48 trajectories versus 34/48 for current, while using 65,106 instead of 71,784 input
tokens. The one-run all-general gate covered 120 cases per arm:

| Arm | Passed | Input tokens | Median elapsed |
| --- | ---: | ---: | ---: |
| V2 prose-only | 102/120 | 149,237 | 5.088 s |
| Previous production wording | 101/120 | 161,548 | 5.077 s |

The candidate used 7.6% fewer measured input tokens without an aggregate quality regression. A
calendar-question regression was corrected by stating the existing required and optional fields
directly; the repaired candidate passed 4/5 repetitions versus 1/5 for the previous wording. Media
disagreements were repeated separately and remained stochastic rather than candidate-specific.
After promotion, a production smoke passed 7/8 cases. Its single hostile-memory miss was followed by
a dedicated 5/5 production pass, satisfying the security gate.

## Preserved contracts

The evaluation showed that apparently redundant text and schema structure can materially guide
medium-sized local models. Future work must preserve:

- the complete `question` schema;
- the current-turn citation reminder;
- local identity and untrusted-memory precedence reminders;
- skill question coverage instructions;
- memory write authorization and retrieval completeness rules;
- nullable patch semantics and automation schedule defaults.

## Next optimization boundary

Further compression beyond V2 is not justified without a new model-facing hypothesis and a paired
quality gate. Additional cold-start improvement will likely require one or more of:

- a faster model or faster prompt-prefill hardware;
- an explicit user-selected tool profile;
- a provider mechanism that reliably reuses the common tool and system prefix;
- a different tool contract architecture that first demonstrates quality parity.

History compaction and smaller tool-result payloads can reduce later-round cost, but they do not
address the first request with zero cached input.

## Raw artifacts

Raw artifacts are intentionally stored under the gitignored `data/` directory:

- `prompt-slimming-qwen36-production-structural-all-general.json`
- `prompt-slimming-qwen36-production-structural-disagreements-4x.json`
- `prompt-slimming-qwen36-structural-safe-latest-3x.json`
- `prompt-slimming-qwen36-prompt-safe-latest-3x.json`
- `prompt-slimming-qwen36-schema-targeted-3x.json`
- `prompt-slimming-qwen36-combined-safe-3x.json`
- `prompt-slimming-qwen36-v2-final-targeted-3x.json`
- `prompt-slimming-qwen36-v2-all-general.json`
- `prompt-slimming-qwen36-v2-calendar-schema-repair-5x.json`
- `prompt-slimming-qwen36-v2-media-disagreements-3x.json`
- `prompt-slimming-qwen36-v2-media-isolation-3x.json`
- `prompt-slimming-qwen36-v2-production-smoke.json`
- `prompt-slimming-qwen36-v2-production-memory-5x.json`

The tracked report records the decision and aggregate evidence because raw evaluation artifacts are
not recoverable from Git history.
