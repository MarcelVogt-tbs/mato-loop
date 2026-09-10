import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

import pytest

from mato_loop_backend import browser_perf_eval as bench
from mato_loop_backend.providers.base import ProviderError, ProviderResult, ToolCall

OWNER, MODEL, PROVIDER = UUID(int=1), UUID(int=2), UUID(int=3)


def fill(state, ref="e1", **changes):
    return ToolCall(ref, "browser", {"action": "fill", "tabId": bench.TAB,
                                   "selector": f"aria-ref={ref}", "text": bench.TARGET[ref],
                                   "snapshotSha256": bench.inspect(state).data["snapshotSha256"], **changes})


def test_schema_and_full_snapshot_simulation():
    old, new = bench.schema(True), bench.schema(False)
    for tool in (old, new):
        parameters = tool["parameters"]
        assert not {"code", "function", "slowly", "submit"} & parameters["properties"].keys()
        assert parameters["properties"]["action"]["enum"] == [
            "tabs", "newtab", "closetab", "navigate", "inspect", "screenshot", "click", "fill",
            *([] if tool is old else ["fill_form"]), "press", "scroll", "diagnostics", "upload",
        ]
        assert "evaluate" not in parameters["properties"]["selector"]["description"]
        assert "required" in parameters["properties"]["snapshotSha256"]["description"]
    assert bench.schemas(True, "narrow") == [old]
    assert bench.schemas(False) == [new]
    assert "fields" not in old["parameters"]["properties"]
    assert "fill_form" not in old["parameters"]["properties"]["action"]["enum"]
    assert "fill_form" in new["parameters"]["properties"]["action"]["enum"]
    assert "Request independent known form fills" in old["description"]
    assert old["parameters"]["properties"]["observe"] == new["parameters"]["properties"]["observe"]
    state = dict.fromkeys(bench.FIELDS, "")
    for observe in (True, False):
        observation = bench.simulate(state, ToolCall("inspect", "browser", {"action": "inspect", "tabId": bench.TAB, "observe": observe}), True)
        assert observation["data"] == bench.inspect(state).data
    for fields in (None, []):
        assert "error" in bench.simulate(state, fill(state, fields=fields), True)
    batch = [fill(state, ref) for ref in bench.FIELDS]
    initial = bench.inspect(state).data["snapshotSha256"]
    results = [bench.simulate(state, call, True) for call in batch]
    assert results[0]["data"]["snapshotSha256"] != initial
    assert [result["error"]["code"] for result in results[1:]] == ["browser.bindingChanged"] * 2
    assert state == {"e1": bench.TARGET["e1"], "e2": "", "e3": ""}
    observation = bench.simulate(state, ToolCall("inspect", "browser", {"action": "inspect", "tabId": bench.TAB}), True)
    assert observation["data"] == bench.inspect(state).data
    for ref in ("e2", "e3"):
        assert "error" not in bench.simulate(state, fill(state, ref), True)
    final_token = bench.inspect(state).data["snapshotSha256"]
    assert state == bench.TARGET

    native = dict.fromkeys(bench.FIELDS, "")
    call = ToolCall("native", "browser", {"action": "fill_form", "tabId": bench.TAB, "snapshotSha256": initial,
                                        "fields": [{"ref": ref, "name": name, "type": "textbox", "value": bench.TARGET[ref]} for ref, name in bench.FIELDS.items()]})
    assert "error" in bench.simulate(native, call, True)
    call.arguments["fields"][2]["name"] = "Wrong"
    assert "error" in bench.simulate(native, call, False)
    assert native == dict.fromkeys(bench.FIELDS, "")
    call.arguments["fields"][2]["name"] = bench.FIELDS["e3"]
    result = bench.simulate(native, call, False)
    assert result["batch"] == {"completed": 3, "total": 3}
    assert native == bench.TARGET
    assert result["data"]["snapshotSha256"] == final_token == bench.inspect(native).data["snapshotSha256"]
    assert bench.simulate(native, call, False)["error"]["code"] == "browser.snapshotChanged"


@pytest.mark.parametrize("baseline", [True, False])
@pytest.mark.parametrize("changes", [
    {"code": None}, {"function": None}, {"slowly": False}, {"submit": False},
    {"action": "select"}, {"action": "evaluate"}, {"action": "run_code"},
    {"snapshotSha256": None}, {"text": 123}, {"observe": "false"}, {"unknown": None},
])
def test_legacy_rejects_unoffered_fields_and_invalid_arguments(baseline, changes):
    state = dict.fromkeys(bench.FIELDS, "")
    assert "error" in bench.simulate(state, fill(state, **changes), baseline)
    assert state == dict.fromkeys(bench.FIELDS, "")


def test_narrow_schema_and_individual_types_without_hash(monkeypatch):
    tools = bench.schemas(False, "narrow")
    assert [tool["name"] for tool in tools] == ["browser_snapshot", "browser_type", "browser_fill_form"]
    assert tools == [bench.ToolRegistry._contributed_definition(tool).schema() for tool in bench.NARROW_TOOLS]
    assert all("snapshotSha256" not in tool["parameters"]["properties"] for tool in tools)
    model = next(tool.arguments_model for tool in bench.NARROW_TOOLS if tool.name == "browser_type")
    converted = []
    original = model.to_action

    def to_action(self):
        converted.append(self.ref)
        return original(self)

    monkeypatch.setattr(model, "to_action", to_action)
    state = dict.fromkeys(bench.FIELDS, "")
    snapshot = ToolCall("snapshot", "browser_snapshot", {"tabId": bench.TAB})
    assert bench.simulate(state, snapshot, False, "narrow")["data"] == bench.inspect(state).data
    initial = bench.inspect(state).data
    for ref in bench.FIELDS:
        call = ToolCall(ref, "browser_type", {"tabId": bench.TAB, "ref": ref, "text": bench.TARGET[ref], "observe": False})
        assert "error" not in bench.simulate(state, call, False, "narrow")
    assert set(converted) == set(bench.FIELDS)
    assert state == bench.TARGET and bench.inspect(state).data != initial


@pytest.mark.parametrize("name,changes", [
    ("browser_type", {"snapshotSha256": "a" * 64}),
    ("browser_type", {"selector": "aria-ref=e1"}),
    ("browser_type", {"tabId": None}),
    ("browser_type", {"tabId": "22222222-2222-4222-8222-222222222222"}),
    ("browser_type", {"tab_id": bench.TAB}),
    ("browser_type", {"ref": "e99"}),
    ("browser_type", {"text": 123}),
    ("browser_type", {"text": "x" * 32769}),
    ("browser_type", {"observe": "false"}),
    ("browser_type", {"submit": True}),
    ("browser_type", {"slowly": True}),
    ("browser_type", {"code": "raise RuntimeError('must not execute')"}),
    ("browser_snapshot", {}),
    ("browser", {"action": "fill"}),
    ("browser_run_code", {"code": "raise RuntimeError('must not execute')"}),
    ("browser_evaluate", {"function": "() => { throw Error('must not execute'); }"}),
])
def test_narrow_rejects_invalid_or_unsupported_calls_without_mutation(name, changes):
    state = dict.fromkeys(bench.FIELDS, "")
    arguments = {"tabId": bench.TAB, **changes} if name in {"browser_run_code", "browser_evaluate"} else {
        "tabId": bench.TAB, "ref": "e1", "text": "changed", **changes,
    }
    call = ToolCall("invalid", name, arguments)
    assert "error" in bench.simulate(state, call, False, "narrow")
    assert state == dict.fromkeys(bench.FIELDS, "")


@pytest.mark.parametrize("change", ["name", "type", "duplicate", "total_length", "extra"])
def test_narrow_batch_validates_before_mutation(change):
    state = dict.fromkeys(bench.FIELDS, "")
    fields = [{"ref": ref, "name": name, "type": "textbox", "value": bench.TARGET[ref]} for ref, name in bench.FIELDS.items()]
    if change in {"name", "type"}:
        fields[-1][change] = "Wrong" if change == "name" else "combobox"
    elif change == "duplicate":
        fields[-1] = fields[0]
    elif change == "total_length":
        fields[-1]["value"] = "x" * 32768
    else:
        fields[-1]["extra"] = True
    call = ToolCall("batch", "browser_fill_form", {"tabId": bench.TAB, "fields": fields})
    assert "error" in bench.simulate(state, call, False, "narrow")
    assert state == dict.fromkeys(bench.FIELDS, "")


def setup_live(monkeypatch, complete):
    db = AsyncMock()
    db.__aenter__.return_value = db
    db.get.return_value = SimpleNamespace(id=OWNER, is_active=True)
    monkeypatch.setattr(bench, "AsyncSessionFactory", Mock(return_value=db))
    model = SimpleNamespace(id=MODEL, provider_id=PROVIDER, enabled=True, availability="available", supports_tools=True, model_id="openai-codex/synthetic-test-model")
    provider = SimpleNamespace(id=PROVIDER, enabled=True, adapter_type="openai-codex", owner_id=OWNER, credential_id=PROVIDER)
    monkeypatch.setattr(bench, "load_effective_ai", AsyncMock(return_value=SimpleNamespace(models=[model], providers=[provider])))
    monkeypatch.setattr(bench, "load_credential", AsyncMock(return_value=object()))
    monkeypatch.setattr(bench, "readonly_auth", AsyncMock(return_value={}))
    monkeypatch.setattr(bench.provider_service.ProviderService, "complete", complete)
    return db


@pytest.mark.parametrize("interface", ["legacy", "narrow"])
async def test_default_off_and_readonly_budget_replay(monkeypatch, interface):
    private = {"continuation_scope": {"providerId": str(PROVIDER)}, "responses_output_items": [{"type": "reasoning", "encrypted_content": "PRIVATE"}]}

    async def complete(db, **kwargs):
        assert bench.provider_service._RETRY_DELAYS == ()
        assert bench.provider_service._adapter_runtime().refresh_codex_credential is bench.readonly_auth
        assert kwargs["timeout"] == 45
        assert isinstance(kwargs["reasoning_effort"], bench.ReasoningEffort)
        assert kwargs["reasoning_effort"].value in {"medium", "low"}
        messages = kwargs["messages"]
        if len(messages) > 2:
            assert messages[-2]["provider_data"] is private
        call = (ToolCall("inspect", "browser", {"action": "inspect", "tabId": bench.TAB}) if kwargs["tools"][0]["name"] == "browser"
                else ToolCall("inspect", "browser_snapshot", {"tabId": bench.TAB}))
        return ProviderResult(content="PRIVATE", tool_calls=[call], provider_data=private,
                              usage={"cached_input_tokens": 7}, finish_reason="tool_calls")

    mocked = AsyncMock(side_effect=complete)
    db = setup_live(monkeypatch, mocked)
    disabled = await bench.evaluate()
    assert disabled["outcome"] == "disabled"
    assert disabled["interface"] == "legacy"
    assert (await bench.evaluate(interface="narrow"))["outcome"] == "disabled"
    assert "adapter" not in disabled and "model" not in disabled
    bench.AsyncSessionFactory.assert_not_called()
    assert (await bench.evaluate(live=True))["outcome"] == "selected_owner_and_model_required"
    bench.AsyncSessionFactory.assert_not_called()
    retries = bench.provider_service._RETRY_DELAYS
    report = await bench.evaluate(live=True, owner_id=OWNER, model_id=MODEL, interface=interface)
    assert str(db.execute.call_args.args[0]) == "SET TRANSACTION READ ONLY"
    db.get.assert_awaited_once_with(bench.UserAccount, OWNER)
    bench.load_effective_ai.assert_awaited_once_with(db, OWNER)
    db.commit.assert_not_called()
    db.flush.assert_not_called()
    assert report["requestCount"] == mocked.await_count == report["maxRequests"] == 12
    assert report["adapter"] == "openai-codex"
    assert report["model"] == "openai-codex/synthetic-test-model"
    assert report["interface"] == interface and report["synthetic"]
    assert "subset" in report["scope"] and "code_execution" in report["scope"]
    assert report["requestTimeoutSeconds"] == 45 and report["globalTimeoutSeconds"] == 360
    for metrics, baseline in zip(report["schemas"].values(), (True, False), strict=True):
        encoded = json.dumps(bench.schemas(baseline, interface), ensure_ascii=True, separators=(",", ":"))
        assert metrics["jsonBytes"] == len(encoded.encode())
        assert metrics["jsonChars"] == len(encoded)
        assert metrics["estimatedTokens"] == (len(encoded) + 3) // 4
    assert [len(cell["requests"]) for cell in report["cells"]] == [4, 2, 4, 2]
    assert all(not cell["finalFieldMatch"] for cell in report["cells"])
    assert all(cell["seconds"] == round(sum(row["seconds"] for row in cell["requests"]), 3) for cell in report["cells"])
    assert report["cells"][0]["requests"][0]["cached_input_tokens"] == 7
    assert report["transmittedMaxOutputTokens"] is None
    assert "PRIVATE" not in json.dumps(report)
    assert bench.provider_service._RETRY_DELAYS == retries


@pytest.mark.parametrize("options,interface", [([], "legacy"), (["--interface", "narrow"], "narrow")])
def test_cli_interface_is_opt_in(monkeypatch, capsys, options, interface):
    evaluate = AsyncMock(return_value={"interface": interface, "outcome": "disabled"})
    monkeypatch.setattr(bench, "evaluate", evaluate)
    monkeypatch.setattr("sys.argv", ["browser_perf_eval", *options])
    monkeypatch.setattr(bench.logging, "disable", Mock())
    bench.main()
    evaluate.assert_awaited_once_with(live=False, owner_id=None, model_id=None, interface=interface)
    assert json.loads(capsys.readouterr().out)["interface"] == interface


@pytest.mark.parametrize("blocked", ["missing_owner", "inactive_owner", "missing_model", "missing_provider", "other_adapter"])
async def test_owner_and_selected_codex_required(monkeypatch, blocked):
    complete = AsyncMock()
    db = setup_live(monkeypatch, complete)
    ai = bench.load_effective_ai.return_value
    if blocked == "missing_owner":
        db.get.return_value = None
    elif blocked == "inactive_owner":
        db.get.return_value.is_active = False
    elif blocked == "missing_model":
        ai.models[0].id = UUID(int=4)
    elif blocked == "missing_provider":
        ai.providers[0].id = UUID(int=4)
    else:
        ai.providers[0].adapter_type = "openai-compatible"
    report = await bench.evaluate(live=True, owner_id=OWNER, model_id=MODEL)
    assert report["outcome"] == ("active_owner_required" if blocked.endswith("owner") else "selected_provider_configuration_unavailable")
    assert report["requestCount"] == 0
    assert "adapter" not in report and "model" not in report
    if blocked.endswith("owner"):
        bench.load_effective_ai.assert_not_called()
    bench.readonly_auth.assert_not_called()
    complete.assert_not_called()


@pytest.mark.parametrize("interface,narrow_mode", [("legacy", None), ("narrow", "browser_type"), ("narrow", "browser_fill_form")])
async def test_complete_batch_and_recovery_match(monkeypatch, interface, narrow_mode):
    async def complete(db, **kwargs):
        messages = kwargs["messages"]
        initial = json.loads(messages[1]["content"])["inspection"]
        state = dict.fromkeys(bench.FIELDS, "")
        if kwargs["tools"][0]["name"] == "browser_snapshot":
            if narrow_mode == "browser_type":
                calls = [ToolCall(ref, narrow_mode, {"tabId": bench.TAB, "ref": ref, "text": bench.TARGET[ref]}) for ref in bench.FIELDS]
            else:
                calls = [ToolCall("native", narrow_mode, {"tabId": bench.TAB,
                         "fields": [{"ref": ref, "name": name, "type": "textbox", "value": bench.TARGET[ref]} for ref, name in bench.FIELDS.items()]})]
        elif "fields" in kwargs["tools"][0]["parameters"]["properties"]:
            calls = [ToolCall("native", "browser", {"action": "fill_form", "tabId": bench.TAB,
                     "snapshotSha256": initial["data"]["snapshotSha256"],
                     "fields": [{"ref": ref, "name": name, "type": "textbox", "value": bench.TARGET[ref]} for ref, name in bench.FIELDS.items()]})]
        elif len(messages) == 2:
            calls = [fill(state, ref) for ref in bench.FIELDS]
        else:
            state["e1"] = bench.TARGET["e1"]
            if len(messages) > 6:
                state["e2"] = bench.TARGET["e2"]
            calls = [fill(state, "e3" if state["e2"] else "e2")]
        return ProviderResult(tool_calls=calls, finish_reason="tool_calls")

    setup_live(monkeypatch, AsyncMock(side_effect=complete))
    report = await bench.evaluate(live=True, owner_id=OWNER, model_id=MODEL, interface=interface)
    assert report["requestCount"] == 8
    assert [cell["requestCount"] for cell in report["cells"]] == [3, 1, 3, 1]
    assert all(cell["finalFieldMatch"] and cell["outcome"] == "matched" for cell in report["cells"])
    assert report["cells"][0]["requests"][0]["toolErrors"] == 2
    assert [cell["mode"] for cell in report["cells"]] == ["baseline", "fill_form" if interface == "legacy" else "narrow"] * 2


@pytest.mark.parametrize("finish", ["length", "max_tokens", "max_output_tokens", "MAX_TOKENS"])
async def test_truncated_calls_never_change_simulated_state(monkeypatch, finish):
    state = dict.fromkeys(bench.FIELDS, "")
    setup_live(monkeypatch, AsyncMock(return_value=ProviderResult(
        tool_calls=[fill(state, ref) for ref in bench.FIELDS], finish_reason=finish,
    )))
    simulate = Mock(side_effect=AssertionError("Truncated actions must not execute"))
    monkeypatch.setattr(bench, "simulate", simulate)
    report = await bench.evaluate(live=True, owner_id=OWNER, model_id=MODEL)
    assert report["requestCount"] == 4
    assert all(cell["outcome"] == "output_truncated" and not cell["finalFieldMatch"] for cell in report["cells"])
    simulate.assert_not_called()


async def test_no_retry_timeout_and_preflight_fail_closed(monkeypatch):
    operation = AsyncMock(side_effect=ProviderError("PRIVATE", status_code=503))

    async def failing(*args, **kwargs):
        return await bench.provider_service._with_retries(operation)

    db = setup_live(monkeypatch, AsyncMock(side_effect=failing))
    report = await bench.evaluate(live=True, owner_id=OWNER, model_id=MODEL)
    assert operation.await_count == report["requestCount"] == 4
    assert all(cell["outcome"] == "provider_error" for cell in report["cells"])
    assert "PRIVATE" not in json.dumps(report)

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(bench.provider_service.ProviderService, "complete", AsyncMock(side_effect=slow))
    monkeypatch.setattr(bench, "REQUEST_SECONDS", 0.001)
    report = await bench.evaluate(live=True, owner_id=OWNER, model_id=MODEL)
    assert report["requestCount"] == 4
    assert all(cell["outcome"] == "request_timeout" for cell in report["cells"])
    monkeypatch.setattr(bench, "GLOBAL_SECONDS", 0.001)
    monkeypatch.setattr(bench, "REQUEST_SECONDS", 45)
    report = await bench.evaluate(live=True, owner_id=OWNER, model_id=MODEL)
    assert report["outcome"] == "global_timeout"
    assert report["requestCount"] <= 1
    monkeypatch.setattr(bench, "GLOBAL_SECONDS", 360)
    db.execute.side_effect = RuntimeError("PRIVATE")
    bench.provider_service.ProviderService.complete.reset_mock()
    assert (await bench.evaluate(live=True, owner_id=OWNER, model_id=MODEL))["outcome"] == "readonly_preflight_failed"
    bench.provider_service.ProviderService.complete.assert_not_called()


async def test_existing_credential_only(monkeypatch):
    payload = {"access": "PRIVATE", "expires": int((bench.time.time() + 1000) * 1000), "account_id": "PRIVATE"}
    credential = SimpleNamespace(user_id=OWNER, encrypted_payload=b"encrypted")
    key_path = Mock()
    key_path.read_bytes.return_value = b"key"
    directory = Mock()
    directory.__truediv__ = Mock(return_value=key_path)
    monkeypatch.setattr(bench, "get_runtime_config", lambda: SimpleNamespace(secrets_dir=directory))
    with patch.object(bench, "Fernet") as cipher:
        cipher.return_value.decrypt.side_effect = lambda _: json.dumps(payload)
        assert await bench.readonly_auth(None, credential, owner_id=OWNER) == payload
        payload["expires"] = 0
        with pytest.raises(bench.Blocked, match="credential_refresh_required"):
            await bench.readonly_auth(None, credential, owner_id=OWNER)
        key_path.write_bytes.assert_not_called()
        directory.mkdir.assert_not_called()
        with pytest.raises(bench.Blocked, match="credential_missing_or_wrong_owner"):
            await bench.readonly_auth(None, credential, owner_id=None)
