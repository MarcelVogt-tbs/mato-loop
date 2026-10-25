import asyncio
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from pydantic import ValidationError

from matoloop_agent.browser import BrowserRuntime
from matoloop_agent.browser_contracts import (
    BrowserExecuteArguments,
    BrowserFileArguments,
    BrowserScriptResult,
)
from matoloop_agent.browser_worker import BrowserWorker, ScriptOutput
from matoloop_agent.filesystem import AgentError


class BrowserScriptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.worker = BrowserWorker(Path(temporary.name))
        self.worker.live = self.worker.root
        self.tab_id = str(uuid4())
        self.args = dict(browserId=str(uuid4()), tabId=self.tab_id)
        self.page = Mock(
            bring_to_front=AsyncMock(),
            title=AsyncMock(return_value="Test"),
            url="about:blank",
            is_closed=Mock(return_value=False),
            effects=[],
        )
        self.worker.context = Mock()
        self.worker.tabs[self.tab_id] = self.page

    async def execute(self, action="run_code", **kwargs):
        return await self.worker.execute(
            BrowserExecuteArguments(**self.args, action=action, **kwargs)
        )

    def test_wire_and_json_bounds(self):
        args = BrowserExecuteArguments(**self.args, action="evaluate", function="() => null")
        self.assertEqual(args.model_dump(by_alias=True)["function"], "() => null")
        self.assertFalse(args.guard or args.slowly or args.submit)
        for extra in (
            dict(action="run_code"),
            dict(action="evaluate"),
            dict(action="select", tabId=None),
            dict(action="run_code", code="x" * 32769),
            dict(action="evaluate", function="x" * 32769),
            dict(action="inspect", slowly=True),
            dict(action="inspect", submit=True),
            dict(action="inspect", guard="true"),
            dict(action="inspect", code="x"),
        ):
            with self.subTest(extra=str(extra)[:80]), self.assertRaises(ValidationError):
                BrowserExecuteArguments(**{**self.args, **extra})
        self.assertEqual(BrowserScriptResult(value="x" * 32766).value, "x" * 32766)
        deep = None
        for _ in range(34):
            deep = [deep]
        cycle = []
        cycle.append(cycle)
        for value in (
            float("nan"),
            float("inf"),
            b"bytes",
            {1: "key"},
            {1, 2},
            "x" * 32767,
            "\u00e4" * 16384,
            deep,
            cycle,
            [None] * 32768,
        ):
            with self.subTest(kind=type(value)), self.assertRaises(ValidationError):
                BrowserScriptResult(value=value)
        for invalid in (
            {"failureReason": "runtimeError"},
            {"line": 1},
            {"errorCode": "browser.outcomeUnknown", "failureReason": "rawException"},
            {
                "errorCode": "browser.outcomeUnknown",
                "failureReason": "runtimeError",
                "failureDetail": "resultTooLarge",
            },
            {
                "errorCode": "browser.outcomeUnknown",
                "failureReason": "invalidResult",
                "failureDetail": "typeError",
            },
        ):
            with self.assertRaises(ValidationError):
                BrowserScriptResult(**invalid)
        self.assertIsNone(BrowserScriptResult(errorCode="browser.outcomeUnknown").failure_reason)
        output = ScriptOutput()
        self.assertEqual(output.write("a" * 1_000_000), 1_000_000)
        self.assertEqual(output.write("b" * 1_000_000), 1_000_000)
        self.assertEqual(output.text, "a" * 32768)
        self.assertTrue(output.truncated)

    async def test_preflight_execution_once_fresh_namespace_and_output(self):
        for code in (
            "print('must not execute')\nasync def run(page):\n invalid syntax !",
            "print('must not execute')\ndef run(page): pass",
            "print('must not execute')\nasync def run(other): pass",
            "async def run(page, extra): pass",
            "async def run(page=None): pass",
        ):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream), self.assertRaises(AgentError) as error:
                await self.execute(code=code)
            self.assertEqual(error.exception.code, "browser.invalidTarget")
            self.assertEqual(stream.getvalue(), "")
        result = await self.execute(
            code="""import os, sys
print('module')
print('stderr', file=sys.stderr)
marker = 42
async def run(page):
    page.effects.append('once')
    print('callback')
    return {'pid': os.getpid(), 'value': marker}
"""
        )
        self.assertEqual(result["script"]["value"], {"pid": os.getpid(), "value": 42})
        self.assertEqual(result["script"]["output"], "module\nstderr\ncallback\n")
        self.assertEqual(self.page.effects, ["once"])
        result = await self.execute(code="async def run(page): return 'marker' in globals()")
        self.assertIs(result["script"]["value"], False)
        for ending in (
            "return object()",
            "raise RuntimeError('not exposed')",
            "raise TypeError('not exposed')",
            "raise AttributeError('not exposed')",
            "raise TimeoutError('not a deadline')",
            "return float('nan')",
            "return 'x' * 32769",
            "return " + "[" * 34 + "None" + "]" * 34,
            "raise SystemExit('not exposed')",
            "raise __import__('asyncio').CancelledError()",
            "return type('NotJSON', (), {'__repr__': lambda self: print('REPR')})()",
        ):
            result = await self.execute(
                code=(
                    "async def run(page):\n    page.effects.append('partial')\n"
                    "    print('retained')\n    " + ending
                )
            )
            self.assertEqual(
                result["script"],
                dict(
                    value=None,
                    output="retained\n",
                    outputTruncated=False,
                    errorCode="browser.outcomeUnknown",
                    failureReason=(
                        "invalidResult" if ending.startswith("return") else "runtimeError"
                    ),
                    failureDetail=(
                        "resultTooDeep"
                        if "[[[" in ending
                        else "resultTooLarge"
                        if "32769" in ending
                        else "resultTypeUnsupported"
                        if ending.startswith("return")
                        else "typeError"
                        if ending.startswith("raise TypeError")
                        else "attributeError"
                        if ending.startswith("raise AttributeError")
                        else None
                    ),
                    line=None if ending.startswith("return") else 4,
                ),
            )
        result = await self.execute(
            code="print('top-level')\nraise ValueError()\nasync def run(page): pass"
        )
        self.assertEqual(result["script"]["output"], "top-level\n")
        self.assertEqual(result["script"]["errorCode"], "browser.outcomeUnknown")
        self.assertEqual(result["script"]["failureReason"], "runtimeError")
        self.assertEqual(result["script"]["line"], 2)

    async def test_timeout_cancellation_and_failed_observation(self):
        timeout = asyncio.timeout
        with patch(
            "matoloop_agent.browser_worker.asyncio.timeout",
            lambda seconds: timeout(0.01 if seconds == 120 else seconds),
        ):
            result = await self.execute(
                code="""import asyncio
async def run(page):
    print('started')
    page.effects.append('partial')
    await asyncio.sleep(60)
"""
            )
        self.assertEqual(result["script"]["errorCode"], "browser.outcomeUnknown")
        self.assertEqual(result["script"]["failureReason"], "scriptTimeout")
        self.assertIsNone(result["script"]["line"])
        self.assertEqual(result["script"]["output"], "started\n")
        self.worker._settle = AsyncMock(side_effect=RuntimeError("closed page"))
        self.page.title.side_effect = TimeoutError
        result = await self.execute(code="async def run(page): return 42", observe=True)
        self.assertEqual(result["script"]["value"], 42)
        self.assertIsNone(result["script"]["errorCode"])
        self.assertEqual(result["data"], {"observationFailed": True})
        self.assertEqual(result["tabs"][0]["title"], "")
        pending = asyncio.create_task(
            self.execute(
                code="""import asyncio
async def run(page):
    page.effects.append('cancel')
    await asyncio.sleep(60)
"""
            )
        )
        while "cancel" not in self.page.effects:
            await asyncio.sleep(0)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending

    async def test_guard_opt_in_and_normal_observation_identity(self):
        self.worker.document_ids[self.page] = "document"
        self.page.locator.return_value.evaluate = AsyncMock(return_value={"state": ["private"]})
        self.page.aria_snapshot = AsyncMock(return_value="- button")
        normal = await self.worker._inspect(self.page, guard=False)
        self.page.locator.return_value.evaluate.assert_not_awaited()
        self.worker.geometry["width"] = 400
        self.assertEqual(normal, await self.worker._inspect(self.page, guard=False))
        guarded = await self.worker._inspect(self.page)
        self.assertNotEqual(normal[1], guarded[1])
        self.page.locator.return_value.evaluate.assert_awaited_once()
        self.page.url = "about:blank#new"
        self.assertNotEqual(normal[1], (await self.worker._inspect(self.page, guard=False))[1])

        small = await self.worker._inspect(
            self.page, browser_id=self.args["browserId"], guard=False
        )
        self.assertEqual(small[0], "- button")
        self.assertIsNone(small[2])

        full = "- button first\n" + "- button second " + "x" * 32_768
        self.page.aria_snapshot.return_value = full
        truncated = await self.worker._inspect(
            self.page, browser_id=self.args["browserId"], guard=False
        )
        self.assertIsNone(truncated[0])
        self.assertEqual(
            truncated[1],
            {
                "snapshotSha256": truncated[1]["snapshotSha256"],
                "snapshotCharacters": len(full),
            },
        )
        self.assertEqual(truncated[2]["size"], len(full.encode()))
        file_id = truncated[2]["fileId"]
        chunk = await self.worker.file(
            BrowserFileArguments(browserId=self.args["browserId"], action="read", fileId=file_id)
        )
        self.assertTrue(chunk["data"])
        for action in ("choose", "write", "finish"):
            with self.assertRaises(AgentError):
                await self.worker.file(
                    BrowserFileArguments(
                        browserId=self.args["browserId"], action=action, fileId=file_id
                    )
                )
        self.page.aria_snapshot.return_value = full + "changed outside released text"
        changed = await self.worker._inspect(
            self.page, browser_id=self.args["browserId"], guard=False
        )
        self.assertEqual(changed[0], truncated[0])
        self.assertNotEqual(changed[1]["snapshotSha256"], truncated[1]["snapshotSha256"])
        self.worker._inspect = AsyncMock(return_value=("", {}, None))
        await self.execute(action="inspect")
        self.worker._inspect.assert_awaited_with(
            self.page,
            browser_id=self.args["browserId"],
            guard=False,
            selector=None,
            depth=None,
        )
        await self.execute(action="inspect", guard=True)
        self.worker._inspect.assert_awaited_with(
            self.page, browser_id=None, guard=True, selector=None, depth=None
        )
        self.worker._settle = AsyncMock()
        await self.execute(code="async def run(page): return 1", observe=True, guard=True)
        self.worker._inspect.assert_awaited_with(
            self.page,
            browser_id=self.args["browserId"],
            guard=False,
        )

    @unittest.skipUnless(
        os.getuid() == 0 and os.environ.get("MATOLOOP_AGENT_CONTAINER") == "1",
        "Requires the disposable development container",
    )
    async def test_disposable_headed_scripts(self):
        process = await asyncio.create_subprocess_exec(
            "runuser",
            "-u",
            "matoloop-browser",
            "--",
            sys.executable,
            __file__,
            "BrowserHeadedScripts",
            "-v",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 150)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        print(output.decode(), flush=True)
        self.assertEqual(process.returncode, 0, output.decode())


@unittest.skipUnless(
    os.getuid() != 0 and os.environ.get("MATOLOOP_AGENT_CONTAINER") == "1",
    "Executed as the isolated browser user by the container test",
)
class BrowserHeadedScripts(unittest.IsolatedAsyncioTestCase):
    async def test_real_scripts_refs_typing_and_closed_context(self):
        with tempfile.TemporaryDirectory() as root:
            worker = BrowserWorker(Path(root))
            try:
                self.assertEqual((await worker.start())["state"], "ready")
                page = worker.context.pages[0]
                tab_id = next(key for key, tab in worker.tabs.items() if tab is page)

                async def execute(action, **kwargs):
                    return await worker.execute(
                        BrowserExecuteArguments(
                            browserId=str(uuid4()),
                            tabId=tab_id,
                            action=action,
                            **kwargs,
                        )
                    )

                result = await execute(
                    "run_code",
                    code="""import sys
print('module')
async def run(page):
    await page.set_content('''<input aria-label=Name><button id=step>First</button>
        <script>window.steps=0; step.onclick=()=>{
            steps++; step.textContent=steps===1?'Second':steps===2?'Third':'Finished';
        };</script>''')
    for expected in ('First', 'Second', 'Third'):
        if await page.locator('#step').inner_text() != expected:
            raise RuntimeError('conditional step')
        await page.locator('#step').click()
    print('steps', file=sys.stderr)
    return await page.evaluate('steps')
""",
                    observe=True,
                )
                self.assertEqual(result["script"]["value"], 3)
                self.assertEqual(result["script"]["output"], "module\nsteps\n")
                ref = re.search(r'textbox "Name" \[ref=(\w+)\]', result["text"])[1]
                result = await execute(
                    "evaluate",
                    selector="aria-ref=" + ref,
                    function="async node => { node.value='element'; return node.value; }",
                )
                self.assertEqual(result["script"]["value"], "element")
                result = await execute("evaluate", function="() => Promise.resolve({steps})")
                self.assertEqual(result["script"]["value"], {"steps": 3})
                for function in (
                    "() => { steps++; throw Error('not exposed'); }",
                    "() => { steps++; return NaN; }",
                    "() => { steps++; return undefined; }",
                    "() => { steps++; return {bad: () => 1}; }",
                    "() => { steps++; return 'x'.repeat(32769); }",
                    "() => { steps++; return new Array(40000); }",
                    "() => { steps++; return " + "[" * 34 + "null" + "]" * 34 + "; }",
                ):
                    before = await page.evaluate("steps")
                    result = await execute("evaluate", function=function)
                    self.assertEqual(result["script"]["errorCode"], "browser.outcomeUnknown")
                    self.assertIn(
                        result["script"]["failureReason"], {"runtimeError", "invalidResult"}
                    )
                    if result["script"]["failureReason"] == "invalidResult":
                        self.assertEqual(
                            result["script"]["failureDetail"],
                            "resultTooDeep"
                            if "[[[" in function
                            else "resultTooLarge"
                            if "32769" in function or "40000" in function
                            else "resultTypeUnsupported",
                        )
                    self.assertEqual(await page.evaluate("steps"), before + 1)
                for ending in ("return object()", "raise RuntimeError('not exposed')"):
                    result = await execute(
                        "run_code",
                        code=(
                            "async def run(page):\n"
                            "    await page.locator('input').fill('partial')\n"
                            "    print('partial')\n    " + ending
                        ),
                    )
                    self.assertEqual(result["script"]["errorCode"], "browser.outcomeUnknown")
                    self.assertEqual(
                        result["script"]["failureReason"],
                        "invalidResult" if ending.startswith("return") else "runtimeError",
                    )
                    if ending.startswith("return"):
                        self.assertEqual(
                            result["script"]["failureDetail"],
                            "resultTypeUnsupported"
                            if ending == "return object()"
                            else "resultTooLarge",
                        )
                    self.assertEqual(
                        result["script"]["line"], None if ending.startswith("return") else 4
                    )
                    self.assertEqual(result["script"]["output"], "partial\n")
                    self.assertEqual(await page.locator("input").input_value(), "partial")
                await page.set_content("""<form
                    onsubmit="event.preventDefault(); window.submitted=true">
                    <input aria-label=Name><input type=checkbox><button>Send</button></form>
                    <script>window.keys=[]; document.onkeydown=e=>keys.push(e.key);</script>""")
                await execute(
                    "fill", selector="input:first-child", text="abc", slowly=True, submit=True
                )
                self.assertEqual(await page.evaluate("keys"), ["a", "b", "c", "Enter"])
                self.assertTrue(await page.evaluate("submitted"))
                with self.assertRaises(AgentError):
                    await execute("fill", selector="input[type=checkbox]", text="x", slowly=True)
                await execute("fill", selector="input:first-child", text="replacement")
                self.assertEqual(await page.locator("input").first.input_value(), "replacement")
                fresh = await worker.context.new_page()
                await execute("select")
                self.assertEqual((await worker.navigation_state())["activeTabId"], tab_id)
                result = await execute(
                    "run_code",
                    code="""async def run(page):
    print('x' * 1000000)
    await page.close()
    return {'closed': True}
""",
                    observe=True,
                )
                self.assertEqual(result["script"]["value"], {"closed": True})
                self.assertEqual(len(result["script"]["output"]), 32768)
                self.assertTrue(result["script"]["outputTruncated"])
                self.assertEqual(result["data"], {"observationFailed": True})
                page = fresh
                tab_id = next(key for key, tab in worker.tabs.items() if tab is page)
                result = await execute(
                    "run_code",
                    code="async def run(page):\n    await page.context.close()\n    return 42",
                    observe=True,
                )
                self.assertEqual(result["script"]["value"], 42)
                self.assertIsNone(result["script"]["errorCode"])
                self.assertEqual(result["data"], {"observationFailed": True})
            finally:
                await worker.close()

    async def test_pipe_framing_script_failure_and_hard_block_closure(self):
        with tempfile.TemporaryDirectory() as root:
            manager = BrowserRuntime(SimpleNamespace(data_dir=Path(root)), None)
            try:
                await manager.spawn(Path(root))
                self.assertEqual((await manager.rpc("start"))["state"], "ready")
                args = dict(browserId=str(uuid4()), action="tabs")
                tab = (await manager.rpc("execute", args=args))["tabs"][0]["id"]
                args.update(action="run_code", tabId=tab)
                result = await manager.rpc(
                    "execute",
                    args={
                        **args,
                        "code": """import asyncio, sys
print('top-level')
print('stderr', file=sys.stderr)
async def late():
    await asyncio.sleep(0.05)
    print('late task')
async def run(page):
    asyncio.create_task(late())
    print('callback')
    return 42
""",
                    },
                )
                self.assertEqual(result["script"]["output"], "top-level\nstderr\ncallback\n")
                self.assertEqual(result["script"]["value"], 42)
                await asyncio.sleep(0.1)
                self.assertTrue((await manager.rpc("health"))["ok"])
                result = await manager.rpc(
                    "execute",
                    args={
                        **args,
                        "code": """import asyncio
async def run(page):
    await page.set_content('<p>Partial effect</p>')
    print('before failure')
    raise RuntimeError()
""",
                    },
                )
                self.assertEqual(result["script"]["errorCode"], "browser.outcomeUnknown")
                self.assertEqual(result["script"]["output"], "before failure\n")
                self.assertEqual(result["script"]["failureReason"], "runtimeError")
                self.assertEqual(result["script"]["line"], 5)
                result = await manager.rpc(
                    "execute",
                    args={
                        **args,
                        "code": """async def run(page):
    await page.locator('#missing').click(timeout=10)
""",
                    },
                )
                self.assertEqual(result["script"]["failureReason"], "playwrightTimeout")
                self.assertEqual(result["script"]["line"], 2)
                result = await manager.rpc(
                    "execute",
                    args={
                        **args,
                        "code": """async def run(page):
    session = await page.context.browser.new_browser_cdp_session()
    try:
        info = await session.send('SystemInfo.getProcessInfo')
        return {'text': await page.locator('p').inner_text(),
                'pid': next(p['id'] for p in info['processInfo'] if p['type'] == 'browser')}
    finally:
        await session.detach()
""",
                    },
                )
                self.assertEqual(result["script"]["value"]["text"], "Partial effect")
                browser_pid = result["script"]["value"]["pid"]
                process = manager.worker
                timeout = asyncio.timeout
                # Exercise the real process-group kill; shorten only the parent's 140s RPC budget.
                with patch(
                    "matoloop_agent.browser.asyncio.timeout",
                    lambda seconds: timeout(0.3 if seconds == 140 else seconds),
                ):
                    with self.assertRaises(AgentError) as error:
                        await manager.rpc(
                            "execute",
                            args={
                                **args,
                                "code": """while True: pass
async def run(page): return 1
""",
                            },
                        )
                self.assertEqual(error.exception.code, "browser.outcomeUnknown")
                self.assertIsNone(manager.worker)
                self.assertIsNotNone(process.returncode)
                # Chromium finishes profile writes after losing the killed driver's pipe.
                async with asyncio.timeout(5):
                    while True:
                        try:
                            os.kill(browser_pid, 0)
                        except ProcessLookupError:
                            break
                        await asyncio.sleep(0.05)
            finally:
                await manager.close()


if __name__ == "__main__":
    unittest.main()
