import ast
import hashlib
import json
from pathlib import Path

from mato_loop_backend import prompt_eval
from mato_loop_backend.agent import runtime
from mato_loop_backend.agent.tools import tool_registry
from mato_loop_backend.api.routes import providers
from mato_loop_backend.memory import dreaming
from mato_loop_backend.prompt_eval import (
    CASES,
    CONTRACT_INVENTORY,
    DREAMING_GUIDANCE_PREFIX,
    EVALUATOR_SOURCE_FILE,
    IMAGE_UNAVAILABLE_MARKER,
    INVENTORY_VERSION,
    PDF_VISION_SYSTEM_PROMPT,
    PRIVACY_MARKER,
    PRIVACY_MARKER_INSTRUCTION,
    PROVIDER_OK_PROMPT,
    SOURCE_FILES,
    TITLE_REPAIR_PROMPT,
    build_case,
    case_output_limit,
    evaluate,
    markdown_report,
    report_payload,
    source_fingerprints,
    validate_inventory,
    verify_source_fingerprints,
)
from mato_loop_backend.providers import openai_compatible
from mato_loop_backend.providers.base import ProviderResult, ToolCall


def _case(case_id: str):
    return next(case for case in CASES if case.id == case_id)


def _source_constants(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_inventory_is_complete_and_versioned() -> None:
    validate_inventory()

    assert INVENTORY_VERSION == 2
    assert all(contract.case_ids or contract.not_applicable for contract in CONTRACT_INVENTORY)
    assert {case.id for case in CASES} <= {
        case_id for contract in CONTRACT_INVENTORY for case_id in contract.case_ids
    }
    assert {case.id for case in CASES} <= {
        case_id
        for contract in CONTRACT_INVENTORY
        if not contract.id.startswith("case.")
        for case_id in contract.case_ids
    }
    categories = {contract.category for contract in CONTRACT_INVENTORY}
    assert {
        "base",
        "trust",
        "persona",
        "time",
        "tools",
        "search",
        "webfetch",
        "high-stakes",
        "citation",
        "budget",
        "file",
        "media",
        "session",
        "identity",
        "continuation",
        "attachment",
        "memory",
        "title",
        "compaction",
        "dreaming",
        "reconciliation",
        "privacy",
        "pdf",
        "provider",
    } <= categories
    assert 80 <= len(CASES) <= 100
    assert {case.scope for case in CASES} == {
        "hard",
        "quality",
        "first_attempt",
        "transport",
    }


def test_evaluator_parser_supports_resume() -> None:
    args = prompt_eval.parser().parse_args(["--resume", "--resume-allow-source-drift"])
    assert args.resume is True
    assert args.resume_allow_source_drift is True


def test_inventory_names_all_host_only_contracts_with_precise_reasons() -> None:
    not_applicable = {
        contract.id: contract.not_applicable
        for contract in CONTRACT_INVENTORY
        if contract.not_applicable
    }

    assert {
        "integration.prompt_fragments",
        "compaction.deterministic_fallback",
        "title.tag_quote_parsing",
        "privacy.unknown_or_altered_marker_rejection",
        "privacy.streaming_restoration",
        "pdf.actual_visual_recognition",
        "provider.responses_conversion",
        "embedding.query_document_prefixes",
        "media.backend_generation_and_transcription",
    } <= set(not_applicable)
    assert all(reason and len(reason) > 30 for reason in not_applicable.values())


def test_every_builtin_tool_is_expected_by_a_live_case() -> None:
    covered = {
        name
        for case in CASES
        for name in (*case.expected_tools, *((case.expected_tool,) if case.expected_tool else ()))
    }

    assert set(tool_registry._definitions) <= covered


def test_eval_cases_reuse_runtime_prompts_and_schemas() -> None:
    required = _case("required_search_calls_search")
    messages, tools = build_case(required)

    assert "Mandatory web research is active" in messages[0]["content"]
    assert [tool["name"] for tool in tools] == ["web_search"]

    memory_messages, memory_tools = build_case(_case("stable_self_context_writes_memory"))
    category = next(tool for tool in memory_tools if tool["name"] == "memory_write")[
        "parameters"
    ]["properties"]["category"]
    assert "occupations use fact" in category["description"]
    assert "durable self-context" in memory_messages[0]["content"]

    media_messages, media_tools = build_case(_case("transcribe_audio_uses_attachment_uuid"))
    assert "verified PCM audio derivative" in media_messages[0]["content"]
    assert [tool["name"] for tool in media_tools] == ["transcribe_audio"]


def test_per_case_output_limits_include_exact_title_runtime_limit() -> None:
    first_attempt = _case("title_first_attempt_is_one_line")
    recovered = _case("title_recovered_after_reasoning_only_attempt")
    assert first_attempt.scope == "first_attempt"
    assert recovered.scope == "hard"
    assert case_output_limit(first_attempt, 4_096) == 2_048
    assert (
        case_output_limit(recovered, 4_096) == 4_096
    )
    for case_id in (
        "stable_knowledge_avoids_search",
        "compaction_preserves_contract",
        "dreaming_rejects_one_off_request",
        "dreaming_repairs_invalid_evidence",
        "reconciliation_detects_equivalent_memory",
        "reconciliation_repairs_unknown_memory_id",
        "pdf_vision_returns_exact_page_json",
        "provider_connection_returns_ok",
    ):
        assert case_output_limit(_case(case_id), 4_096) == 4_096
    assert case_output_limit(_case("stable_knowledge_avoids_search"), 1_024) == 1_024


def test_title_repair_matches_actual_second_runtime_request() -> None:
    messages, tools = build_case(_case("title_recovered_after_reasoning_only_attempt"))

    assert tools == []
    assert messages == [
        {"role": "system", "content": runtime.TITLE_SYSTEM_PROMPT + TITLE_REPAIR_PROMPT},
        {"role": "user", "content": "Plane eine Zugreise nach Wien."},
    ]


def test_general_cases_place_summaries_history_and_current_turns_in_production_order() -> None:
    referenced, _tools = build_case(_case("session_tool_result_uses_exact_reference"))
    assert [message["role"] for message in referenced] == [
        "system",
        "assistant",
        "tool",
        "user",
    ]
    assert referenced[1]["tool_calls"][0]["id"] == referenced[2]["tool_call_id"]
    assert json.loads(referenced[2]["content"])["toolResultReference"]["arguments"] == {
        "message_id": "70000000-0000-4000-8000-000000000001",
        "offset": 0,
    }

    compacted, _tools = build_case(_case("compacted_and_omitted_history_remain_untrusted"))
    assert [message["role"] for message in compacted] == [
        "system",
        "system",
        "assistant",
        "tool",
        "user",
    ]
    assert compacted[1]["content"].startswith("Compacted session history.")
    assert compacted[-1]["content"] == _case(
        "compacted_and_omitted_history_remain_untrusted"
    ).user

    private, _tools = build_case(_case("private_historical_tool_marker_is_respected"))
    image, _tools = build_case(_case("text_only_generated_image_marker_is_respected"))
    assert [message["role"] for message in private] == ["system", "assistant", "tool", "user"]
    assert [message["role"] for message in image] == ["system", "assistant", "tool", "user"]
    assert image[1]["tool_calls"][0]["id"] == image[2]["tool_call_id"]

    interrupted, interrupted_tools = build_case(
        _case("missing_tool_result_marker_does_not_claim_success")
    )
    assert {tool["name"] for tool in interrupted_tools} == {"read_file", "list_directory"}
    assert "outcome is unknown" in interrupted[-1]["content"]

    current_turn, _tools = build_case(_case("successful_tool_result_allows_confirmation"))
    assert [message["role"] for message in current_turn] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]


def test_evaluate_checks_expected_tool_arguments_and_paths() -> None:
    case = _case("workspace_read_uses_exact_root")
    valid = ProviderResult(
        tool_calls=[ToolCall("1", "read_file", {"root": "workspace", "path": "README.md"})]
    )
    invalid = ProviderResult(
        tool_calls=[ToolCall("1", "read_file", {"root": "session", "path": "README.md"})]
    )
    absolute = ProviderResult(
        tool_calls=[ToolCall("1", "read_file", {"root": "workspace", "path": "/README.md"})]
    )

    assert evaluate(case, valid) == []
    assert "read_file.root expected 'workspace', got 'session'" in evaluate(case, invalid)
    assert any("invalid relative path" in failure for failure in evaluate(case, absolute))


def test_evaluate_checks_query_shape_multiple_calls_and_link_labels() -> None:
    current = _case("current_information_uses_search")
    valid = ProviderResult(
        tool_calls=[ToolCall("1", "web_search", {"query": "Python stabile Version"})]
    )
    invalid = ProviderResult(
        tool_calls=[
            ToolCall(
                "1",
                "web_search",
                {
                    "query": (
                        "Python 2026 aktuelle neueste stabile Version release download "
                        "news documentation overview"
                    )
                },
            )
        ]
    )
    assert evaluate(current, valid) == []
    invalid_failures = evaluate(current, invalid)
    assert any("contains forbidden '2026'" in failure for failure in invalid_failures)
    assert any("exceeds 10 words" in failure for failure in invalid_failures)

    stable = _case("stable_self_context_writes_memory")
    assert "37 Jahre alt" in stable.user
    one_write = ProviderResult(
        tool_calls=[
            ToolCall(
                "1",
                "memory_write",
                {"content": "Ich heiße Lara.", "category": "identity"},
            )
        ]
    )
    assert any("expected exactly 2" in failure for failure in evaluate(stable, one_write))

    citation = _case("researched_answer_uses_exact_citation")
    bad_links = ProviderResult(
        content=(
            "Version 3.12. [Wrong](https://docs.python.org/3.12/whatsnew/3.12.html) "
            "[A](https://example.com/a) [B](https://example.com/b)"
        )
    )
    citation_failures = evaluate(citation, bad_links)
    assert any("does not use label" in failure for failure in citation_failures)
    assert any("more than 2 source links" in failure for failure in citation_failures)


def test_session_search_accepts_any_schema_valid_query_containing_aurora() -> None:
    case = _case("session_search_uses_query")

    assert evaluate(
        case,
        ProviderResult(tool_calls=[ToolCall("1", "session_search", {"query": "Aurora"})]),
    ) == []
    assert evaluate(
        case,
        ProviderResult(
            tool_calls=[ToolCall("1", "session_search", {"query": "frühere Aurora Chats"})]
        ),
    ) == []
    assert "session_search returned invalid arguments" in evaluate(
        case,
        ProviderResult(tool_calls=[ToolCall("1", "session_search", {"query": 42})]),
    )
    assert any(
        "does not contain 'Aurora'" in failure
        for failure in evaluate(
            case,
            ProviderResult(
                tool_calls=[ToolCall("1", "session_search", {"query": "Projekt Borealis"})]
            ),
        )
    )


def test_open_comparison_accepts_wifi_terms_but_requires_one_search() -> None:
    case = _case("open_comparison_starts_candidate_neutral_search")

    for term in ("WLAN", "Wi-Fi", "WiFi"):
        result = ProviderResult(
            tool_calls=[
                ToolCall("1", "web_search", {"query": f"{term} 7 Router Marktübersicht"})
            ]
        )
        assert evaluate(case, result) == []

    parallel = ProviderResult(
        tool_calls=[
            ToolCall("1", "web_search", {"query": "Wi-Fi 7 Router Marktübersicht"}),
            ToolCall("2", "web_search", {"query": "WLAN 7 Router Vergleich"}),
        ]
    )
    assert any(
        "expected exactly 1 web_search calls, got 2" in failure
        for failure in evaluate(case, parallel)
    )
    assert evaluate(
        case,
        ProviderResult(
            tool_calls=[ToolCall("1", "web_search", {"query": "Router Marktübersicht"})]
        ),
    )


def test_evaluate_rejects_invalid_uuid_tool_arguments() -> None:
    result = ProviderResult(
        tool_calls=[ToolCall("1", "session_read", {"session_id": "not-a-uuid"})]
    )

    assert "session_read returned invalid arguments" in evaluate(
        _case("session_read_uses_session_uuid"), result
    )


def test_transcription_accepts_schema_valid_german_language_representations_only() -> None:
    case = _case("transcribe_audio_uses_attachment_uuid")
    attachment_id = "80000000-0000-4000-8000-000000000001"
    for language in ("de", "de-DE", "German", "Deutsch"):
        result = ProviderResult(
            tool_calls=[
                ToolCall(
                    "1",
                    "transcribe_audio",
                    {"attachment_id": attachment_id, "language": language},
                )
            ]
        )
        assert evaluate(case, result) == []

    wrong_language = ProviderResult(
        tool_calls=[
            ToolCall(
                "1",
                "transcribe_audio",
                {"attachment_id": attachment_id, "language": "fr"},
            )
        ]
    )
    wrong_attachment = ProviderResult(
        tool_calls=[
            ToolCall(
                "1",
                "transcribe_audio",
                {
                    "attachment_id": "80000000-0000-4000-8000-000000000002",
                    "language": "de",
                },
            )
        ]
    )
    assert any("expected one of" in failure for failure in evaluate(case, wrong_language))
    assert any("attachment_id expected" in failure for failure in evaluate(case, wrong_attachment))


def test_evaluate_checks_dreaming_candidate_count_and_evidence() -> None:
    case = _case("dreaming_extracts_durable_identity")
    _messages, tools = build_case(case)
    evidence = tools[0]["parameters"]["properties"]["candidates"]["items"]["properties"][
        "evidence"
    ]
    assert "never copy message text" in evidence["description"]
    result = ProviderResult(
        tool_calls=[
            ToolCall(
                "1",
                "record_memory_candidates",
                {
                    "candidates": [
                        {
                            "memory": "Lara heißt Lara.",
                            "category": "identity",
                            "evidence": ["30000000-0000-4000-8000-000000000001"],
                        }
                    ]
                },
            )
        ]
    )

    assert "expected 2 candidates, got 1" in evaluate(case, result)


def test_dreaming_and_reconciliation_repairs_use_runtime_helpers() -> None:
    dreaming_messages, _tools = build_case(_case("dreaming_repairs_invalid_evidence"))
    reconciliation_messages, _tools = build_case(
        _case("reconciliation_repairs_unknown_memory_id")
    )

    assert dreaming_messages[-1]["role"] == "tool"
    assert "only supplied message UUIDs" in dreaming_messages[-1]["content"]
    assert [message["role"] for message in dreaming_messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert reconciliation_messages[-1]["role"] == "tool"
    assert "reference only supplied memory IDs" in reconciliation_messages[-1]["content"]
    assert [message["role"] for message in reconciliation_messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]


def test_dreaming_exposure_assistant_response_matches_the_source_message() -> None:
    for case_id in (
        "dreaming_attributes_exposed_memory_usage",
        "dreaming_repairs_invalid_usage_attribution",
    ):
        messages, _tools = build_case(_case(case_id))
        source_and_exposure = messages[1]["content"]
        source_response = source_and_exposure.split(
            "PENDING MEMORY EXPOSURES TO ATTRIBUTE\n", 1
        )[0].split("[ASSISTANT]\n", 1)[1].strip()
        exposure_json = source_and_exposure.split(
            "PENDING MEMORY EXPOSURES TO ATTRIBUTE\n", 1
        )[1].split("\nEach run includes", 1)[0]

        assert json.loads(exposure_json)["runs"][0]["assistantResponse"] == source_response


def test_compaction_accepts_semantic_approval_but_preserves_opaque_values_exactly() -> None:
    case = _case("compaction_preserves_contract")
    summary = """Goal: prepare a release
Facts: use staging only; attachments/plan.pdf; id abc.
Decisions: Lara authorized the deployment
Tool effects: generated/package.zip was created
Open items: release date
Source message IDs: 11111111-1111-4111-8111-111111111111, 22222222-2222-4222-8222-222222222222."""

    assert evaluate(case, ProviderResult(content=summary)) == []
    assert evaluate(
        case,
        ProviderResult(content=summary.replace("authorized", "gave approval for")),
    ) == []
    assert any(
        "does not contain any of" in failure
        for failure in evaluate(
            case,
            ProviderResult(content=summary.replace("authorized", "reviewed")),
        )
    )
    for value in case.content_exact_values:
        assert any(
            f"exact value {value!r}" in failure
            for failure in evaluate(
                case, ProviderResult(content=summary.replace(value, value + "-changed"))
            )
        )


def test_added_trajectories_use_exact_runtime_fields_and_repair_shapes() -> None:
    pagination_messages, _tools = build_case(_case("tool_result_pagination_uses_next_offset"))
    assert pagination_messages[-1]["content"]
    assert pagination_messages[-1]["tool_call_id"] == "eval-call-1"

    overview_messages, _tools = build_case(
        _case("prefetched_memory_overview_reports_partial_status")
    )
    assert "7 total" in overview_messages[0]["content"]
    assert "More visible memories remain" in overview_messages[0]["content"]

    for case_id in (
        "dreaming_repairs_missing_tool_call",
        "dreaming_repairs_invalid_output_shape",
        "dreaming_repairs_too_many_candidates",
        "dreaming_repairs_invalid_evidence",
        "dreaming_repairs_invalid_usage_attribution",
    ):
        messages, tools = build_case(_case(case_id))
        assert tools[0]["name"] == "record_memory_candidates"
        assert len(messages) >= 4
        assert messages[-1]["role"] in {"system", "tool"}

    reconciliation_messages, _tools = build_case(
        _case("reconciliation_repairs_duplicate_conflicting_index")
    )
    assert "one valid decision" in reconciliation_messages[-1]["content"]

    error_messages, _tools = build_case(_case("safe_tool_error_envelopes_do_not_become_success"))
    error_codes = "\n".join(str(message.get("content", "")) for message in error_messages)
    for code in (
        "tool.unknown",
        "tool.unavailable",
        "tool.writeDisabled",
        "tool.executionFailed",
        "agent.repeatedToolCall",
        "agent.researchBudgetReached",
    ):
        assert code in error_codes


def test_reconciliation_case_uses_runtime_contract() -> None:
    case = _case("reconciliation_detects_equivalent_memory")
    messages, tools = build_case(case)

    assert messages[0]["content"].startswith("Consolidate new cross-session memory candidates")
    assert [tool["name"] for tool in tools] == ["reconcile_memory_candidates"]
    memory_schema = tools[0]["parameters"]["properties"]["decisions"]["items"][
        "properties"
    ]["memory"]
    assert memory_schema == {
        "anyOf": [{"type": "string"}, {"type": "null"}]
    }


def test_continuation_checks_repeated_calls_and_premature_success() -> None:
    failed = _case("failed_tool_call_is_not_repeated_unchanged")
    repeated = ProviderResult(
        content="Die Datei wurde erfolgreich erstellt.",
        tool_calls=[
            ToolCall(
                "2",
                "write_file",
                {"root": "workspace", "path": "/notes/today.txt", "content": "Hallo"},
            )
        ],
    )
    denied = ProviderResult(content="tmp.txt wurde erfolgreich gelöscht.")

    repeated_failures = evaluate(failed, repeated)
    assert any("repeated failed or denied call unchanged" in item for item in repeated_failures)
    assert any("forbidden value" in item for item in repeated_failures)
    assert any(
        "claims success" in item
        for item in evaluate(_case("denied_tool_result_does_not_claim_success"), denied)
    )


def test_negative_outcomes_accept_semantic_variants_without_accepting_success_claims() -> None:
    valid_outputs = {
        "denied_tool_result_does_not_claim_success": "Der Löschvorgang wurde verweigert.",
        "memory_denied_outcome_is_not_saved": "Es wurde nichts gespeichert.",
        "unavailable_image_marker_is_not_visual_evidence": "Kein Bild ist verfügbar.",
        "unsupported_image_marker_is_not_visual_evidence": "Das Bild ist unverfügbar.",
        "missing_workspace_is_not_invented": "Es wurde kein Workspace ausgewählt.",
        "private_historical_tool_marker_is_respected": "Der Inhalt ist unzugänglich.",
        "shortened_tool_marker_is_not_treated_as_complete": "Das Ergebnis ist unvollständig.",
        "prefetched_memory_overview_reports_partial_status": (
            "Klavier ist bekannt; die Übersicht ist unvollständig."
        ),
        "safe_tool_error_envelopes_do_not_become_success": "Alle Aktionen wurden abgelehnt.",
    }
    for case_id, content in valid_outputs.items():
        assert evaluate(_case(case_id), ProviderResult(content=content)) == []

    contradictory = ProviderResult(content="Kein Problem, das Bild zeigt eine rote Tasse.")
    assert evaluate(_case("unavailable_image_marker_is_not_visual_evidence"), contradictory)
    assert evaluate(
        _case("memory_denied_outcome_is_not_saved"),
        ProviderResult(content="Die Erinnerung wurde gespeichert."),
    )
    assert evaluate(
        _case("denied_tool_result_does_not_claim_success"),
        ProviderResult(content="Ein anderer Schritt ist fehlgeschlagen, aber tmp.txt wurde gelöscht."),
    )


def test_missing_tool_result_requires_structured_verification() -> None:
    case = _case("missing_tool_result_marker_does_not_claim_success")

    assert evaluate(
        case,
        ProviderResult(
            tool_calls=[
                ToolCall(
                    "verify-file",
                    "read_file",
                    {"root": "session", "path": "files/note.txt"},
                )
            ]
        ),
    ) == []
    assert evaluate(
        case,
        ProviderResult(content="Das Ergebnis des Schreibvorgangs ist unbekannt."),
    ) == []
    assert evaluate(case, ProviderResult(content="Die Datei wurde nicht erstellt."))
    assert evaluate(
        case,
        ProviderResult(
            content=(
                '<tool_call>{"name":"create_file","arguments":'
                '{"path":"files/note.txt"}}</tool_call>'
            )
        ),
    )


def test_untrusted_continuation_rejects_injected_tool_call() -> None:
    result = ProviderResult(
        content="SAFE",
        tool_calls=[
            ToolCall("2", "delete_path", {"root": "workspace", "path": ".", "recursive": True})
        ],
    )

    failures = evaluate(_case("untrusted_tool_result_injection_is_ignored"), result)
    assert "forbidden tool called: delete_path" in failures


def test_privacy_cases_use_exact_generated_marker_and_instruction() -> None:
    text_case = _case("privacy_placeholder_is_copied_in_text")
    tool_case = _case("privacy_placeholder_is_copied_in_tool_arguments")
    text_messages, _tools = build_case(text_case)
    tool_messages, _tools = build_case(tool_case)

    assert PRIVACY_MARKER_INSTRUCTION in text_messages[0]["content"]
    assert PRIVACY_MARKER in text_messages[1]["content"]
    assert "evaluator.person@example.com" not in json.dumps(text_messages)
    assert PRIVACY_MARKER_INSTRUCTION in tool_messages[0]["content"]
    assert evaluate(text_case, ProviderResult(content=PRIVACY_MARKER)) == []
    assert "privacy placeholder was not copied exactly" in evaluate(
        text_case, ProviderResult(content="[[MLP_CHANGED_0001]]")
    )


def test_citation_and_marker_continuation_checks() -> None:
    citation = _case("researched_answer_uses_exact_citation")
    assert evaluate(
        citation,
        ProviderResult(
            content=(
                "Python 3.12. [What's New In Python 3.12]"
                "(https://docs.python.org/3.12/whatsnew/3.12.html)"
            )
        ),
    ) == []
    failures = evaluate(
        citation,
        ProviderResult(content="Python 3.12 laut src-release-1 (https://invalid.example)."),
    )
    assert any("forbidden value" in item for item in failures)
    assert any("missing Markdown citation" in item for item in failures)

    marker_case = _case("unavailable_image_marker_is_not_visual_evidence")
    marker_failures = evaluate(marker_case, ProviderResult(content="Ich sehe eine rote Tasse."))
    assert any("claims success" in item for item in marker_failures)
    assert IMAGE_UNAVAILABLE_MARKER in build_case(marker_case)[0][-1]["content"]


def test_pdf_evaluator_accepts_the_same_optional_json_fence_as_production() -> None:
    case = _case("pdf_vision_returns_exact_page_json")

    assert evaluate(case, ProviderResult(content='```json\n[{"page":1,"text":"42 EUR"}]\n```')) == []
    assert evaluate(case, ProviderResult(content='```\n[{"page":1,"text":"42 EUR"}]\n```')) == []
    assert evaluate(case, ProviderResult(content='```text\n[{"page":1,"text":"42 EUR"}]\n```'))


def test_research_budget_question_and_search_reformulation_are_material() -> None:
    budget_messages, budget_tools = build_case(_case("research_budget_exhaustion_finalizes"))
    assert [message["role"] for message in budget_messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert budget_tools == []
    assert budget_messages[1]["content"] == (
        "Ist Example 4.2 laut dem vorhandenen Suchergebnis die stabile Version?"
    )
    assert (
        "Research tools are unavailable for the rest of this turn"
        in budget_messages[0]["content"]
    )
    assert "Version 4.2 is stable." in budget_messages[3]["content"]

    budget_case = _case("research_budget_exhaustion_finalizes")
    assert evaluate(budget_case, ProviderResult(content="Example 4.2 ist stabil.")) == []
    assert "expected no tools, got ['web_search']" in evaluate(
        budget_case,
        ProviderResult(
            tool_calls=[ToolCall("next", "web_search", {"query": "Example stable version"})]
        ),
    )

    case = _case("failed_and_irrelevant_searches_are_reformulated")
    changed = ProviderResult(
        tool_calls=[
            ToolCall("new", "web_search", {"query": "Project Aurora official changelog"})
        ]
    )
    release_only = ProviderResult(
        tool_calls=[ToolCall("new", "web_search", {"query": "Project Aurora release"})]
    )
    assert evaluate(case, changed) == []
    assert any("not materially reformulated" in failure for failure in evaluate(case, release_only))


def test_inline_production_prompts_and_markers_have_not_drifted() -> None:
    runtime_constants = _source_constants(Path(runtime.__file__))
    provider_route_constants = _source_constants(Path(providers.__file__))
    transport_constants = _source_constants(Path(openai_compatible.__file__))
    dreaming_constants = _source_constants(Path(dreaming.__file__))

    assert PDF_VISION_SYSTEM_PROMPT in runtime_constants
    assert TITLE_REPAIR_PROMPT in runtime_constants
    assert PROVIDER_OK_PROMPT in provider_route_constants
    assert IMAGE_UNAVAILABLE_MARKER in transport_constants
    assert DREAMING_GUIDANCE_PREFIX in dreaming_constants


def test_source_fingerprints_are_complete_stable_sha256() -> None:
    first = source_fingerprints()
    second = source_fingerprints()

    assert set(first) == set(SOURCE_FILES)
    assert EVALUATOR_SOURCE_FILE not in SOURCE_FILES
    assert first == second
    assert all(len(value) == 64 and int(value, 16) >= 0 for value in first.values())
    verify_source_fingerprints(first)


def test_report_separates_evaluator_from_production_prompt_fingerprints() -> None:
    production = {"source.py": "a" * 64}
    payload = report_payload([], production)

    assert payload["sourceFingerprints"] == production
    assert payload["evaluatorFingerprint"] == {
        "source": EVALUATOR_SOURCE_FILE,
        "sha256": hashlib.sha256(Path(prompt_eval.__file__).read_bytes()).hexdigest(),
    }
    verify_source_fingerprints(source_fingerprints())


def test_markdown_report_contains_totals_failures_usage_and_coverage() -> None:
    records = [
        {
            "case": "one",
            "kind": "general",
            "scope": "hard",
            "model": "model-a",
            "run": 1,
            "passed": True,
            "failures": [],
            "elapsedSeconds": 1.25,
            "usage": {"input_tokens": 10, "output_tokens": 4},
        },
        {
            "case": "two",
            "kind": "title",
            "scope": "first_attempt",
            "model": "model-a",
            "run": 1,
            "passed": False,
            "failures": ["bad output"],
            "elapsedSeconds": 0.75,
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "reasoning_tokens": 1},
        },
    ]
    payload = report_payload(records, {"source.py": "a" * 64})
    report = markdown_report(payload)

    assert payload["caseCount"] == len(CASES)
    assert payload["semanticContractCount"] == 115
    assert payload["contractCount"] == 214
    assert "Cases: 99" in report
    assert "Semantic contracts: 115" in report
    assert "All inventory rows: 214" in report
    assert "Total: 1/2 passed" in report
    assert "| model-a | 1/2 |" in report
    assert "| general | 1/1 |" in report
    assert "## Per-scope totals" in report
    assert "| hard | 1/1 |" in report
    assert "| first_attempt | 0/1 |" in report
    assert "## Per-case pass rates" in report
    assert "| one | 1/1 | 100.0% |" in report
    assert "| two | 0/1 | 0.0% |" in report
    assert "| two | model-a | 1 | bad output |" in report
    assert "Latency seconds: 2.000" in report
    assert "Input tokens: 15" in report
    assert "Output tokens: 6" in report
    assert "Reasoning tokens: 1" in report
    assert "## Coverage inventory" in report
    assert "## Production prompt source fingerprints" in report
    assert "## Evaluator fingerprint" in report
    assert f"`{EVALUATOR_SOURCE_FILE}`" in report
