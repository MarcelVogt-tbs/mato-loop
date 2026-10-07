"""Twenty-minute evaluation-only comparison; tools are validated but never executed."""

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re
import runpy
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from jsonschema import ValidationError as SchemaValidationError
from jsonschema.validators import validator_for
from pydantic import ValidationError
from sqlalchemy import select

import mato_loop_backend.prompt_eval as prompt_eval
from mato_loop_backend.agent.prompts import automation_context_prompt
from mato_loop_backend.error_contract import error_payload
from mato_loop_backend.models import UserAccount
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.tools.catalog import tool_registry
from mato_loop_backend.tools.contracts import DynamicToolArgumentsError

HELPERS = runpy.run_path(str(Path(__file__).with_name("automation-permissions-eval.py")))
ROOT = Path("/workspace")
OPENCODE = {"shell_run": "bash", "write_file": "write", "edit_file": "edit",
            "web_search": "websearch", "load_skill": "skill", "todo_write": "todowrite"}
TARGETS = (("openai-codex/gpt-6-astra", "ChatGPT Codex", None),
           ("qwen3.8-27b-mxfp4", "R9700", "medium"))
VARIANTS = (("current", "_reason"), ("current", "reason"), ("opencode", "_reason"), ("opencode", "reason"))
AUTO_ID = "11111111-1111-4111-8111-111111111111"
FILE = "/home/evaluator/workspace/note.txt"


def rewrite(value, mapping):
    if isinstance(value, str):
        if not mapping:
            return value
        return re.sub(r"(?<!\w)(?:" + "|".join(map(re.escape, mapping)) + r")(?!\w)",
                      lambda match: mapping[match[0]], value)
    if isinstance(value, list):
        return [rewrite(item, mapping) for item in value]
    if isinstance(value, dict):
        return {mapping.get(key, key): rewrite(item, mapping) for key, item in value.items()}
    return value


def inputs(model_id, case, names, reason, prompt, repetition):
    mapping = dict(OPENCODE) if names == "opencode" else {}
    if reason == "reason":
        mapping["_reason"] = "reason"
    fixture = HELPERS["CASE"]
    if case == "file_write":
        fixture = replace(fixture, history=(), user=f'Erstelle auf dem Computer die neue Datei {FILE} mit dem exakten Inhalt "NEW". Der Zielordner ist vorhanden.')
    elif case == "file_edit":
        fixture = replace(fixture, history=HELPERS["_exchange"](
            "read", {"root": "computer", "path": FILE}, '{"content":"OLD","totalLines":1}', "call_read_file",
        ), user=f'Ersetze in der gerade gelesenen Datei {FILE} auf dem Computer OLD durch NEW.')
    else:
        fixture = replace(fixture, user=(
            "Meld dich bitte, wenn der Druck fertig ist. Prüfe alle fünf Minuten bis morgen um 20 Uhr. Danach beende die Überwachung."
            if repetition == 1 else
            "Sag mir Bescheid, sobald die Montagehilfen fertig gedruckt sind. Schau alle fünf Minuten nach; Schluss ist spätestens morgen um 20 Uhr."
        ))
    messages, tools = prompt_eval.build_case(fixture, model_id=model_id)
    assert len(tools) == len(fixture.tools)
    assert messages[0]["content"].count(HELPERS["BASELINE"]) == 1
    if prompt == "candidate":
        messages[0]["content"] = messages[0]["content"].replace(HELPERS["BASELINE"], HELPERS["CANDIDATE"])
    messages[0]["content"] += "\n\n" + automation_context_prompt([])
    if case in HELPERS["OUTCOMES"]:
        messages.extend(HELPERS["_exchange"]("request_session_permissions", HELPERS["PERMISSIONS"],
                                           json.dumps(HELPERS["OUTCOMES"][case]), "call_prepare_permissions"))
    return rewrite(messages, mapping), rewrite(tools, mapping), mapping


def validate_call(raw, schemas, mapping):
    """Validate the displayed contract first; only exact declared aliases are mapped back."""
    name, arguments = raw["name"], raw["arguments"]
    if name not in schemas:
        return None, error_payload("tool.unavailable")
    try:
        validator_for(schemas[name])(schemas[name]).validate(arguments)
    except SchemaValidationError as exc:
        parts = list(exc.absolute_path)
        if exc.validator == "additionalProperties" and isinstance(exc.instance, dict):
            parts += sorted(set(exc.instance) - set(exc.schema.get("properties", {})))[:1]
        return None, error_payload("tool.invalidArguments", field=".".join(map(str, parts)) or None)
    inverse = {alias: original for original, alias in mapping.items()}
    canonical = inverse.get(name, name)
    arguments = copy.deepcopy(arguments)
    if mapping.get("_reason") == "reason" and "reason" in arguments:
        arguments["_reason"] = arguments.pop("reason")
    definition = tool_registry._definitions.get(canonical) or tool_registry._operation_definitions.get(canonical)
    try:
        definition.validate_arguments(arguments)
    except ValidationError as exc:
        field = ".".join(map(str, exc.errors(include_input=False, include_url=False)[0]["loc"]))
        return None, rewrite(error_payload("tool.invalidArguments", field=field or None), mapping)
    except (DynamicToolArgumentsError, ValueError) as exc:
        return None, rewrite(error_payload("tool.invalidArguments", field=getattr(exc, "field", None)), mapping)
    return ToolCall(raw["id"], canonical, arguments), None


def simulate(raw, schemas, mapping, state, case):
    call, error = validate_call(raw, schemas, mapping)
    if error:
        return error, "invalid", None
    args, name = call.arguments, call.name
    if name == "request_session_permissions":
        targets = []
        for target in args["requests"]:
            nested, error = validate_call({"id": raw["id"], "name": target["tool"], "arguments": target["arguments"]}, schemas, mapping)
            if error:
                return error, "invalid", None
            supported = {"read", "glob", "grep", "write_file", "edit_file", "create_directory", "delete_path", "shell_run", "session_read", "session_browse", "session_search"}
            if nested.name not in supported or nested.arguments.get("elevated") or nested.arguments.get("environment"):
                return HELPERS["OUTCOMES"]["unavailable"], "unsupported_target", None
            targets.append(nested)
        if case == "unavailable":
            return HELPERS["OUTCOMES"]["unavailable"], "preflight_unavailable", None
        if any(target.name == "shell_run" and "http://printer.example.test/printer/objects/query?print_stats" in target.arguments["command"] for target in targets):
            state["prepared"] = True
        return HELPERS["OUTCOMES"]["success"], "prepared", None
    if name == "list_automations":
        return {"automations": state["automations"], "limit": 20, "hasMore": False, "contentIsUntrusted": True}, "discovery", None
    if name == "get_automation":
        match = next((item for item in state["automations"] if item["id"] == args["automation_id"]), None)
        return match or error_payload("automation.notFound"), "discovery", None
    if name in {"create_automation", "edit_automation"}:
        if case.startswith("file_"):
            return {}, "unexpected_action", False
        if name == "edit_automation" and args["automation_id"] != AUTO_ID:
            return error_payload("automation.notFound"), "discovery", None
        if args.get("enabled", True):
            return {"created": True, "enabled": True}, "activate", bool(state["prepared"] and case != "unavailable")
        state["automations"] = [{"id": AUTO_ID, **args, "revision": 1}]
        return {"id": AUTO_ID, "revision": 1, "enabled": False, "created": True}, "draft", None
    if name == "question":
        return {}, "question", case == "unavailable"
    if name == "read" and case == "file_edit":
        return {"content": "OLD", "totalLines": 1}, "read", None
    if name in {"write_file", "edit_file"} and case.startswith("file_"):
        valid = args.get("root") == "computer" and args.get("path") in {FILE, "note.txt"}
        valid = valid and (name == "write_file" and case == "file_write" and args.get("content") == "NEW"
                          or name == "edit_file" and case == "file_edit" and args.get("oldText") == "OLD" and args.get("newText") == "NEW")
        return {}, name, valid
    return {}, "unexpected_action", False


def check():
    for names, reason in VARIANTS:
        messages, tools, mapping = inputs("qwen3.8-27b-mxfp4", "initial", names, reason, "candidate", 1)
        schemas = {tool["name"]: tool["parameters"] for tool in tools}
        raw = {"id": "check", "name": "request_session_permissions", "arguments": rewrite(HELPERS["PERMISSIONS"], mapping)}
        state = {"prepared": False, "automations": []}
        assert simulate(raw, schemas, mapping, state, "initial")[1] == "prepared" and state["prepared"]
        bad = copy.deepcopy(raw)
        bad["arguments"]["requests"][0][reason] = "Wrong nesting"
        assert simulate(bad, schemas, mapping, state, "initial")[1] == "invalid"
        bad = copy.deepcopy(raw)
        bad["arguments"]["requests"][0]["tool"] = "functions." + mapping.get("shell_run", "shell_run")
        assert simulate(bad, schemas, mapping, state, "initial")[1] == "invalid"
        assert mapping.get("shell_run", "shell_run") in messages[2]["tool_calls"][0]["name"]
    print("Offline checks passed: exact aliases, optional reason, nested validation, namespace errors.", flush=True)


async def trial(row, provider, model, effort, deadline, save, report):
    case = row["case"]
    messages, tools, mapping = inputs(model.model_id, case, row["names"], row["reason"], row["prompt"], row["repetition"])
    schema_hash = HELPERS["digest"](tools)
    report["schemas"].setdefault(schema_hash, tools)
    row.update(messages=copy.deepcopy(messages), schemaHash=schema_hash, steps=[], status="running")
    state = {"prepared": case == "success", "automations": []}
    began = time.monotonic()
    trial_deadline = min(deadline, began + 110)
    schemas = {tool["name"]: tool["parameters"] for tool in tools}
    errors = 0
    try:
        for number in range(4):
            remaining = trial_deadline - time.monotonic()
            if remaining <= 0:
                row["status"] = "deadline" if trial_deadline == deadline else "timeout"
                return
            step = {"model": model.model_id, "scenario": case, "variant": f"{row['names']}/{row['reason']}/{row['prompt']}", "repetition": row["repetition"], "number": number + 1}
            row["steps"].append(step)
            save()
            await HELPERS["complete_request"](step, messages, tools, provider, model, effort, remaining, save, check_result=lambda *_: [])
            if step["status"] != "completed":
                row["status"] = "deadline" if time.monotonic() >= deadline else step["status"]
                return
            calls = step["toolCalls"]
            if not calls:
                row.update(status="completed", passed=False, outcome="text_without_required_action")
                return
            messages.append({"role": "assistant", "content": step["content"], "tool_calls": calls})
            step["toolResults"] = []
            prepared_before = state["prepared"]
            results = []
            for raw in calls:
                payload, event, outcome = simulate(raw, schemas, mapping, state, case)
                if event == "activate" and not prepared_before:
                    outcome = False
                if event == "question" and len(calls) != 1:
                    outcome = False
                errors += event == "invalid"
                result = {"toolCallId": raw["id"], "event": event, "content": payload}
                step["toolResults"].append(result)
                messages.append({"role": "tool", "tool_call_id": raw["id"], "content": json.dumps(payload, ensure_ascii=False)})
                if outcome is not None:
                    results.append(outcome)
            if results:
                row.update(status="completed", passed=all(results), outcome=step["toolResults"][-1]["event"])
                return
            if errors >= 2:
                row.update(status="completed", passed=False, outcome="repair_limit")
                return
        row.update(status="completed", passed=False, outcome="step_limit")
    except asyncio.CancelledError:
        row["status"] = "interrupted"
        raise
    except Exception as exc:
        row.update(status="error", errorCode=getattr(exc, "code", type(exc).__name__))
    finally:
        row["seconds"] = round(time.monotonic() - began, 3)
        row["validationErrors"] = errors
        row["firstAttemptValid"] = bool(row["steps"] and row["steps"][0].get("toolCalls")) and not any(
            result["event"] == "invalid" for result in row["steps"][0].get("toolResults", [])
        )
        save()
        print("TRIAL " + json.dumps({key: value for key, value in row.items() if key not in {"messages", "steps"}}), flush=True)


async def run(output):
    check()
    if output.exists():
        raise SystemExit("Refusing to overwrite prior results")
    async with HELPERS["AsyncSessionFactory"]() as db:
        user = (await db.scalars(select(UserAccount).where(UserAccount.is_admin.is_(True), UserAccount.is_active.is_(True)))).one()
        ai = await HELPERS["load_effective_ai"](db, user.id)
    targets = []
    for model_id, provider_name, effort in TARGETS:
        provider = next(item for item in ai.providers if item.enabled and item.name == provider_name)
        model = next(item for item in ai.models if item.enabled and item.provider_id == provider.id and item.model_id == model_id)
        prepared = await ProviderService.prepare_request(provider=provider, model=model, messages=[{"role": "user", "content": "test"}], tools=[], reasoning_effort=effort)
        targets.append((provider, model, effort, {"model": model_id, "provider": provider_name, "adapter": provider.adapter_type,
            "contextWindow": model.context_window_tokens, "reasoningEffort": prepared.request.reasoning_effort,
            "enableThinking": prepared.request.enable_thinking, "temperature": prepared.request.temperature}))
    production = [path for path in (ROOT / "apps/backend/src/mato_loop_backend").rglob("*.py")]
    def hashes():
        return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in production}
    report = {"commit": os.environ["MATO_LOOP_EVAL_COMMIT"],
        "models": [target[3] for target in targets], "aliases": OPENCODE, "baseline": HELPERS["BASELINE"],
        "candidate": HELPERS["CANDIDATE"], "budgetSeconds": 1200, "productionHashes": hashes(),
        "runnerHash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "schemas": {}, "records": []}
    prompt_eval.FIXED_NOW = datetime.now(UTC)
    report["fixtureTime"] = prompt_eval.FIXED_NOW.isoformat()
    start = time.monotonic()
    deadline = start + 1200
    report["startedAt"] = datetime.now(UTC).isoformat()
    def save():
        report["elapsedSeconds"] = round(time.monotonic() - start, 3)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    jobs = [(case, names, reason, prompt, 1)
            for case in ("initial", "success", "unavailable", "file_write", "file_edit")
            for names, reason, prompt in (
                (("current", "_reason", "baseline"), ("current", "_reason", "candidate"))
                if case in {"success", "unavailable"} else
                (("current", "_reason", "baseline"), *[(n, r, "candidate") for n, r in VARIANTS])
                if case == "initial" else tuple((n, r, "candidate") for n, r in VARIANTS))]
    jobs += [("initial", names, reason, prompt, 2) for names, reason, prompt in
             reversed([("current", "_reason", "baseline"), *[(n, r, "candidate") for n, r in VARIANTS]])]
    save()
    try:
        for index, (case, names, reason, prompt, repetition) in enumerate(jobs):
            for provider, model, effort, _ in (targets if index % 2 == 0 else list(reversed(targets))):
                if deadline - time.monotonic() < 5:
                    report["stoppedAtDeadline"] = True
                    return
                row = {"case": case, "names": names, "reason": reason, "prompt": prompt, "repetition": repetition, "model": model.model_id}
                report["records"].append(row)
                await trial(row, provider, model, effort, deadline, save, report)
    finally:
        report["finishedAt"] = datetime.now(UTC).isoformat()
        report["productionUnchanged"] = report["productionHashes"] == hashes()
        save()
        await HELPERS["engine"].dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "data/tool-contracts-20261007.json")
    args = parser.parse_args()
    if args.check:
        check()
    else:
        asyncio.run(run(args.output))
