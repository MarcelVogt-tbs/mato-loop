"""Compare isolated extraction prompts without reading or writing application data."""

import argparse
import asyncio
import hashlib
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from mato_loop_backend import prompt_eval as evaluation
from mato_loop_backend.memory.dreaming import (
    DREAMING_SYSTEM_PROMPT,
    DreamCandidates,
    _dreaming_reasoning_effort,
    _record_candidates_tool,
    _serialize_messages,
)
from mato_loop_backend.providers.base import ProviderRequest
from mato_loop_backend.providers.reasoning import reasoning_profile
from mato_loop_backend.settings.models import MemorySettings, ModelConfig, ReasoningEffort

ADDITION = (
    "Distinguish durable knowledge about the user from instructions for a particular task. "
    "Requirements for an application or deliverable, review steps, automation schedules, "
    "notification conditions, and stop conditions do not by themselves establish recurring "
    "preferences, lasting setups, or long-term personal goals. Repeated execution of an "
    "instruction is not evidence of a recurring personal preference. Do not turn tentative "
    "choices into settled preferences. Still extract directly supported enduring personal "
    "facts, stable working styles, lasting setups, and long-term projects, including when "
    "stated once or alongside task instructions."
)
REFINED_ADDITION = (
    "Apply this boundary to every category: information about the user is not the same as "
    "specifications of an artifact the user is designing. Do not store application requirements, "
    "feature choices, implementation plans, review workflows, or automation instructions as "
    "context, fact, goal, or preference. Calling an artifact 'my project' or asking that it "
    "'remain' a certain way does not establish durable personal knowledge. A request to build "
    "something is not evidence that the user already has a lasting setup. Keep the user's "
    "explicit long-term involvement and project purpose, not its feature specification. "
    "Keep existing equipment and lasting setups, enduring constraints, relationships, and "
    "explicit general working styles; one clear user statement still suffices. In mixed "
    "messages, extract those durable facts and omit the task requirements. "
    "Examples: 'It should stay a lightweight webapp; I see it mainly as a review UI; the AI "
    "works through the CLI' -> no memory. 'This app must not support uploads' -> no memory. "
    "'Check hourly for this release and notify me only on official confirmation' -> no memory, "
    "even when repeated. 'Across my projects I prefer CLI tools for AI work' -> preference. "
    "'I am developing Kartenwerk as a long-term open-source project' -> long-term goal. "
    "'I run Home Assistant on my own Raspberry Pi 5' -> lasting setup. "
    "Never turn uncertain or tentative choices into established preferences."
)
BASELINE = DREAMING_SYSTEM_PROMPT.removesuffix(" " + REFINED_ADDITION)
WEBAPP = (
    "ja soll eine webapp bleiben ich meinte als framework eher ein frontend framework wenn das hilft. "
    "es soll weiterhin schlank bleiben desewgen weiß ich nicht ob der bild upload sein muss. "
    "ich sehe es primär als review oberfläche die ki arbeitet über die cli "
    "(so ki freundlich wie nur mögich um die anofrderungen zu erfüllen)"
)
WATCHER = (
    "Prüfe, ob OpenAI GPT-6.2 Astra offiziell angekündigt oder veröffentlicht hat (z. B. via "
    "openai.com/index, offizielle X-Accounts @OpenAI/@sama, oder aktuelle News von heute). "
    "Nutze web_search und bei Bedarf webfetch für Primärquellen.\n\n"
    "Nur bei Bestätigung durch eine offizielle Quelle (OpenAI-Ankündigung, dokumentierter "
    "Modell-Release oder eindeutige offizielle Bestätigung): Erstelle eine notify-Meldung mit "
    'Titel "GPT-6.2 Astra ist da" und einer kurzen Zusammenfassung (Veröffentlichungsdatum, '
    "Verfügbarkeit in ChatGPT/Codex/API, wichtigste Neuerungen) mit Quellenlinks. Danach "
    "deaktiviere diese Automation selbst (mit edit_automation, enabled=false), damit keine "
    "weiteren Prüfungen mehr laufen.\n\nWenn keine offizielle Bestätigung vorliegt (nur Leaks, "
    "Usage-Logs, Gerüchte oder Spekulation): nicht informieren, keine Notification erstellen, "
    "keine Änderungen vornehmen. Stille Nutzung von silence am Ende, wenn nichts Neues zu melden ist."
)


def case(name, user, count=0, contains=(), excludes=(), history=()):
    return evaluation.PromptCase(
        id=name, kind="dreaming", user=user, history=history, tool_choice="required",
        expected_tool="record_memory_candidates", expected_tool_call_count=1,
        expected_candidate_count=count, expected_candidate_content_contains=contains,
        expected_candidate_content_excludes=excludes,
    )


CASES = tuple(
    item for item in evaluation.CASES
    if item.id.startswith("memory_plan_dreaming_")
) + (
    case("observed_webapp_tentative", WEBAPP),
    case("observed_webapp_changed_requirements", "Baue jetzt doch den Bild-Upload und manuelle Bearbeitung ein.",
         history=({"role": "user", "content": WEBAPP},)),
    case("observed_release_watcher", WATCHER),
    case("repeated_release_watcher", WATCHER, history=(
        {"role": "user", "content": WATCHER},
        {"role": "assistant", "content": "Keine offizielle Bestätigung gefunden."},
        {"role": "user", "content": WATCHER},
        {"role": "assistant", "content": "Weiterhin keine offizielle Bestätigung."},
    )),
    case("app_requirements_assertive", "Die Review-App soll keinen Bild-Upload haben. Ich möchte im Browser "
         "nur Bereiche markieren, kommentieren und freigeben. Die KI soll ausschließlich per CLI bearbeiten."),
    case("automation_print_watch", "Überwache meinen Druck alle fünf Minuten. Informiere mich nur bei "
         "Fertigstellung und beende danach den Watcher. Spätestens heute um 20 Uhr aufhören."),
    case("tentative_general_preference", "Vielleicht sind CLI-Werkzeuge grundsätzlich eher mein Ding, "
         "aber ich bin mir noch nicht sicher. Für diesen Versuch bleiben wir erstmal bei der Webapp."),
    case("positive_cli_working_style", "Bei meinen Projekten bevorzuge ich grundsätzlich CLI-Werkzeuge "
         "für KI-Arbeit und nutze den Browser nur zur Review.", 1, ("CLI", "Browser")),
    case("positive_recurring_source_preference", "Bei Nachrichten zu KI-Modellen möchte ich grundsätzlich "
         "nur offizielle Primärquellen und keine Gerüchte berücksichtigt haben.", 1, ("offiziell",)),
    case("positive_home_automation_setup", "Ich betreibe dauerhaft Home Assistant auf meinem eigenen "
         "Raspberry Pi 5 für die Hausautomation.", 1, ("Home Assistant", "Raspberry Pi 5")),
    case("positive_ongoing_project", "Ich entwickle langfristig eine Open-Source-App namens Kartenwerk "
         "zur Vektorisierung historischer Karten.", 1, ("Kartenwerk",)),
    case("positive_relationship", "Meine Schwester heißt Marlene und ist Tierärztin.", 1, ("Marlene",)),
    case("mixed_hardware_and_watcher", "Ich besitze eine Radeon AI PRO R9700 mit 32 GB VRAM. "
         + WATCHER, 1, ("R9700", "32"), ("Astra", "notify", "Watcher")),
    case("mixed_working_style_and_app", "Bei allen meinen Projekten bevorzuge ich grundsätzlich "
         "CLI-Werkzeuge für KI-Arbeit. Für diese Review-App baue heute Bild-Upload und einen "
         "grünen Freigabe-Button ein.", 1, ("CLI",), ("Upload", "Button")),
)

HOLDOUT = (
    case("holdout_dashboard_design", "Für das Vereins-Dashboard wünsche ich mir eine kompakte "
         "Tabelle ohne Diagramme. Exportiert wird ausschließlich CSV, die Verwaltung erfolgt per REST-API."),
    case("holdout_artifact_durable_word", "Der neue Editor soll dauerhaft ohne Benutzerkonten "
         "auskommen. Plane dafür einen lokalen Dokumentenimport und Tastaturkürzel."),
    case("holdout_train_alert", "Schau bis zur Abfahrt alle zehn Minuten nach, ob mein Zug "
         "Verspätung hat. Melde dich erst ab 15 Minuten und hör nach der Ankunft auf."),
    case("holdout_demo_constraint", "Für die heutige Demo bitte nichts in die Cloud schicken. "
         "Morgen entscheiden wir neu, ob das für das Produkt überhaupt nötig ist."),
    case("holdout_existing_immich", "Meine private Fotoverwaltung läuft auf einem selbst "
         "gehosteten Immich-Server.", 1, ("Immich",)),
    case("holdout_profession", "Ich arbeite als Vermessungsingenieur.", 1, ("Vermessungsingenieur",)),
    case("holdout_allergy", "Ich habe eine Erdnussallergie.", 1, ("Erdnuss",)),
    case("holdout_general_working_style", "Bei Code-Reviews sind mir grundsätzlich kleine, "
         "überschaubare Änderungen lieber als große Umbauten.", 1, ("klein",)),
    case("holdout_enduring_confidentiality", "Aus beruflicher Verschwiegenheit darf ich "
         "Kundendaten grundsätzlich nie an Cloud-Modelle senden.", 1, ("Kundendaten", "Cloud")),
    case("holdout_long_term_personal_goal", "Ich möchte in den nächsten drei Jahren meine "
         "Ausbildung zum Bergführer abschließen.", 1, ("Bergführer",)),
    case("holdout_mixed_default_browser", "Ich nutze Firefox als Standardbrowser. Für die Demo "
         "heute baue bitte einen violetten Exportknopf ein.", 1, ("Firefox",), ("Exportknopf", "violett")),
    case("holdout_mixed_project_involvement", "Ich betreue seit 2020 ehrenamtlich das "
         "Open-Source-Projekt Mooratlas zur Dokumentation von Moorflächen. In diesem Ticket "
         "sollst du nur die Suchleiste nach rechts verschieben.", 1, ("Mooratlas",), ("Suchleiste",)),
)


def inputs(item, variant, addition=ADDITION, *, model_id="qwen3.8-27b", configured_effort=None, timeout=180):
    model = ModelConfig(provider_id=UUID(int=1), model_id=model_id)
    profile = reasoning_profile(model_id)
    if configured_effort is not None and (profile is None or configured_effort not in profile.options):
        raise ValueError("The model does not support the selected reasoning effort.")
    effort = _dreaming_reasoning_effort(model, configured_effort)
    if effort is None:
        effort = profile.default_effort if profile else model.reasoning_effort
    source = []
    for index, turn in enumerate((*item.history, {"role": "user", "content": item.user})):
        source.append(SimpleNamespace(
            id=evaluation.DREAM_USER_MESSAGE_ID if index == 0 else UUID(int=100 + index),
            role=turn["role"], content=turn["content"],
            created_at=datetime(2026, 11, 1, tzinfo=UTC) + timedelta(hours=index),
        ))
    messages = [
        {"role": "system", "content": BASELINE + (" " + addition if variant == "candidate" else "")},
        {"role": "user", "content": "SOURCE MESSAGE BATCH (UNTRUSTED DATA)\n" + _serialize_messages(source)},
    ]
    return ProviderRequest(
        model=model_id, messages=messages,
        tools=[_record_candidates_tool(MemorySettings().dreaming_max_candidates)],
        tool_choice="required", stream=False, supports_parallel_tool_calls=False,
        max_output_tokens=4096, timeout=timeout,
        reasoning_effort=None if profile and profile.supports_disable and effort == ReasoningEffort.NONE else effort,
        enable_thinking=effort != ReasoningEffort.NONE if effort is not None and profile and profile.supports_disable else None,
    ), source


def score(item, result, source):
    failures = evaluation.evaluate(item, result)
    try:
        parsed = DreamCandidates.model_validate(result.tool_calls[0].arguments)
        user_ids = {message.id for message in source if message.role == "user"}
        if any(not set(candidate.evidence_message_ids) <= user_ids for candidate in parsed.candidates):
            failures.append("candidate cites unsupported user evidence")
        if any(candidate.remove for candidate in parsed.candidates):
            failures.append("unexpected removal candidate")
    except (ValueError, IndexError, TypeError):
        failures.append("invalid candidate schema")
    return failures


def check():
    item = next(item for item in CASES if item.id == "positive_cli_working_style")
    baseline, source = inputs(item, "baseline")
    candidate, _ = inputs(item, "candidate")
    assert baseline.tools == candidate.tools and baseline.messages[1:] == candidate.messages[1:]
    assert candidate.messages[0]["content"] == baseline.messages[0]["content"] + " " + ADDITION
    assert inputs(item, "candidate", REFINED_ADDITION)[0].messages[0]["content"] == DREAMING_SYSTEM_PROMPT
    assert baseline.enable_thinking is True and baseline.reasoning_effort == ReasoningEffort.MEDIUM
    configured, _ = inputs(item, "candidate", configured_effort=ReasoningEffort.NONE)
    assert configured.enable_thinking is False and configured.reasoning_effort is None
    empty = evaluation.ProviderResult(tool_calls=[evaluation.ToolCall(
        "empty", "record_memory_candidates", {"candidates": []},
    )])
    assert score(item, empty, source)
    assert not score(case("negative", "Teste das heute."), empty, source)
    print(f"Checks passed: paired inputs and empty-output rejection; {len(CASES)} cases.")


async def run(args):
    addition = REFINED_ADDITION if args.refined else ADDITION
    cases = HOLDOUT if args.holdout else CASES
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "startedAt": datetime.now(UTC).isoformat(), "model": args.model, "baseUrl": args.base_url,
        "runs": args.runs, "sourceFingerprints": evaluation.source_fingerprints(),
        "configuredReasoningEffort": args.reasoning_effort,
        "maxCandidates": MemorySettings().dreaming_max_candidates,
        "evaluatorSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "prompts": {"baseline": BASELINE, "candidate": BASELINE + " " + addition},
        "cases": [asdict(item) for item in cases], "records": [],
    }
    with output.open("x") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    for repetition in range(1, args.runs + 1):
        for index, item in enumerate(cases):
            variants = ("baseline", "candidate") if (repetition + index) % 2 else ("candidate", "baseline")
            for variant in variants:
                request, source = inputs(
                    item, variant, addition, model_id=args.model,
                    configured_effort=ReasoningEffort(args.reasoning_effort) if args.reasoning_effort else None,
                    timeout=args.timeout,
                )
                started = time.monotonic()
                result = await evaluation.chat_completions(
                    base_url=args.base_url, api_key=args.api_key,
                    request=request, on_delta=None,
                )
                failures = score(item, result, source)
                record = {
                    "case": item.id, "expected": item.expected_candidate_count,
                    "variant": variant, "run": repetition, "passed": not failures, "failures": failures,
                    "seconds": round(time.monotonic() - started, 3),
                    "toolCalls": [asdict(call) for call in result.tool_calls],
                    "content": result.content, "usage": result.usage,
                    "messagesHash": hashlib.sha256(json.dumps(request.messages, sort_keys=True).encode()).hexdigest(),
                    "reasoningEffort": request.reasoning_effort, "enableThinking": request.enable_thinking,
                }
                payload["records"].append(record)
                output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                print(f"{'PASS' if not failures else 'FAIL'} {variant} run={repetition} "
                      f"{item.id} {record['seconds']}s {failures}", flush=True)
    payload["finishedAt"] = datetime.now(UTC).isoformat()
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://192.168.1.102:13306/v1")
    parser.add_argument("--api-key")
    parser.add_argument("--model", default="qwen3.8-27b")
    parser.add_argument("--reasoning-effort", choices=[effort.value for effort in ReasoningEffort], help="Use an explicit Dreaming reasoning level; omitted uses the production automatic setting.")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output", default="/workspace/data/memory-extraction-20261104.json")
    parser.add_argument("--refined", action="store_true", help="Test the explicit artifact/user boundary with contrast examples.")
    parser.add_argument("--holdout", action="store_true", help="Use independently worded cases without changing the prompts.")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check()
    if not args.check:
        asyncio.run(run(args))
