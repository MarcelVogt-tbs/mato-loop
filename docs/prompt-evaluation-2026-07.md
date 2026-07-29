# Prompt Evaluation Comparison - July 2026

## Scope

This report compares the complete three-run baseline and optimized prompt evaluations:

- Baseline: `data/prompt-eval-complete-3x.json`
- Optimized: `data/prompt-eval-optimized-complete-3x.json`
- Inventory: 99 cases, 115 semantic contracts, 214 inventory rows
- Requests per evaluation: 594 (99 cases x 2 models x 3 runs)
- Models: `Gemma-4-26B-A4B-it-MTP-GGUF` and `Qwen3.6-35B-A3B-MTP-GGUF`
- Endpoint: `http://192.168.1.32:13305/v1`
- Execution: serial requests

## Results

| Scope | Baseline | Optimized | Change |
| --- | ---: | ---: | ---: |
| Overall | 523/594 (88.05%) | 558/594 (93.94%) | +35, +5.89 percentage points |
| Gemma | 276/297 (92.93%) | 288/297 (96.97%) | +12, +4.04 percentage points |
| Qwen | 247/297 (83.16%) | 270/297 (90.91%) | +23, +7.74 percentage points |

Failures fell from 71 to 36, a reduction of 49.30%. Across the 198 case/model pairs, 27 improved, 13 regressed, and 158 were unchanged. Gross improvements added 51 passing runs; regressions cost 16.

Excluding the two known changed fixtures described below, results improved from 522/582 (89.69%) to 548/582 (94.16%): +26 passing runs and +4.47 percentage points.

## Improvements

The following case/model pairs improved. `G` means Gemma and `Q` means Qwen.

### Three Additional Passes

- G: `dreaming_repairs_invalid_usage_attribution`
- G/Q: `historical_year_and_model_version_are_preserved`
- Q: `missing_tool_result_marker_does_not_claim_success`
- Q: `prefetched_memory_overview_reports_exhausted_status`
- Q: `reconciliation_updates_corrected_memory`
- G: `researched_answer_uses_exact_citation`
- Q: `title_first_attempt_is_one_line`

### Two Additional Passes

- Q: `dreaming_attributes_exposed_memory_usage`
- Q: `explicit_personality_update_uses_instructions_field`
- Q: `memory_correction_copies_retrieved_id_revision`
- G: `missing_tool_result_marker_does_not_claim_success`
- Q: `prefetched_memory_overview_reports_partial_status`
- G: `quoted_hypothetical_and_third_party_facts_are_not_memories`
- Q: `research_budget_exhaustion_finalizes`
- Q: `title_first_attempt_ignores_user_format_injection`

### One Additional Pass

- Q: `compaction_preserves_contract`
- Q: `current_information_uses_search`
- Q: `dreaming_repairs_invalid_usage_attribution`
- Q: `ineligible_fetch_source_is_not_cited`
- Q: `interrupted_response_marker_continues_without_restarting`
- Q: `open_comparison_starts_candidate_neutral_search`
- G: `prefetched_memory_overview_reports_exhausted_status`
- Q: `quoted_hypothetical_and_third_party_facts_are_not_memories`
- Q: `researched_answer_uses_exact_citation`
- Q: `session_search_uses_query`
- Q: `shortened_tool_marker_is_not_treated_as_complete`

Twenty-three improved pairs reached 3/3. The improved pairs still below 3/3 are Qwen on `current_information_uses_search`, `open_comparison_starts_candidate_neutral_search`, `quoted_hypothetical_and_third_party_facts_are_not_memories`, and `researched_answer_uses_exact_citation`.

## Regressions

All regressed pairs had passed 3/3 in the baseline.

- Q: `failed_and_irrelevant_searches_are_reformulated`, 3/3 to 0/3
- Q: `dreaming_rejects_existing_memory_duplicate`, 3/3 to 1/3
- Q: `compacted_and_omitted_history_remain_untrusted`, 3/3 to 2/3
- Q: `compacted_detail_uses_session_read`, 3/3 to 2/3
- Q: `denied_tool_result_does_not_claim_success`, 3/3 to 2/3
- Q: `dreaming_extracts_durable_identity`, 3/3 to 2/3
- G: `memory_stored_outcome_confirms_storage`, 3/3 to 2/3
- G: `open_comparison_starts_candidate_neutral_search`, 3/3 to 2/3
- Q: `private_historical_tool_marker_is_respected`, 3/3 to 2/3
- Q: `safe_tool_error_envelopes_do_not_become_success`, 3/3 to 2/3
- Q: `session_search_result_is_read_by_session_id`, 3/3 to 2/3
- Q: `text_only_generated_image_marker_is_respected`, 3/3 to 2/3
- Q: `tool_result_pagination_uses_next_offset`, 3/3 to 2/3

Four regressions were Qwen empty-response incidents rather than ordinary contract choices. The most significant actionable regression is `failed_and_irrelevant_searches_are_reformulated`.

## Remaining Weaknesses

- `citation_without_title_uses_hostname_label`: 0/6. Both models remain unreliable when a source has no title.
- `failed_and_irrelevant_searches_are_reformulated`: 1/6 overall; Qwen is 0/3.
- Qwen exact research flow remains inconsistent: `current_information_uses_search`, `discovery_result_is_followed_by_exact_fetch`, and `researched_answer_uses_exact_citation` each pass 1/3.
- Session retrieval produced three invalid `session_read` calls and one missed `session_tool_result_read` continuation.
- Qwen memory selectivity remains unstable, especially `dreaming_rejects_existing_memory_duplicate` at 1/3.
- Qwen accounts for 27 of the 36 optimized failures and all four empty-response incidents.

## Failure Categories

The 36 failed optimized records contain 50 non-exclusive assertions:

- Citation and final-answer compliance: 25
- Search and tool discipline: 12
- Memory and dreaming: 5
- Session retrieval: 4
- Empty final content and no tool call: 4

The four empty-response incidents were:

- Q `compacted_and_omitted_history_remain_untrusted`, run 3: 199.878 seconds, output limit reached
- Q `safe_tool_error_envelopes_do_not_become_success`, run 2: 195.972 seconds, output limit reached
- Q `dreaming_rejects_existing_memory_duplicate`, run 1: 99.003 seconds, no final tool call
- Q `private_historical_tool_marker_is_respected`, run 3: anomalous empty final after 11.120 seconds

Empty-response incidents fell from 15 in the baseline to 4 in the optimized run.

## Performance

| Metric | Baseline | Optimized | Change |
| --- | ---: | ---: | ---: |
| Median elapsed time | 14.450 s | 14.888 s | +0.439 s |
| Mean elapsed time | 17.331 s | 20.446 s | +3.115 s |
| Total tokens | 740,529 | 843,866 | +103,337 (+13.95%) |
| Input tokens | 469,575 | 488,312 | +18,737 |
| Output tokens | 270,954 | 355,554 | +84,600 |
| Median tokens per record | 1,093 | 1,130.5 | +37.5 |

Gemma token use changed from 315,756 to 321,985. Qwen changed from 424,773 to 521,881. Qwen's mean elapsed time increased from 18.818 to 24.531 seconds while its median changed only from 15.057 to 15.288 seconds, so long reasoning outliers account for most of the additional cost.

## Comparability Notes

- The baseline has no evaluator fingerprint. The optimized output records evaluator fingerprint `a0c91dc90090598fb1d86ecf611741aa474deb8c383264cf617b448158b0d6f0`.
- `researched_answer_uses_exact_citation` changed from an `example.com` version 4.2 fixture to an official Python 3.12 documentation fixture.
- `missing_tool_result_marker_does_not_claim_success` changed from requiring a verbal uncertainty response to accepting structured file verification or an explicit unknown outcome.
- The optimized run used checkpoints. Runs 1 and 2 completed before `fetch_web` gained a non-model-facing `user_agent` argument. Run 3 resumed afterward. The tool schemas and prompt text presented to the models were unchanged by that wiring change.
- The resume implementation changed the evaluator fingerprint between the first two runs and the third run, but did not change case construction or result evaluation.

These caveats make the raw score unsuitable as a controlled model benchmark. It remains valid as an end-to-end regression assessment of the optimized MatoLoop prompt contracts.
