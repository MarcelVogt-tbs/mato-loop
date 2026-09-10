import asyncio
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from time import monotonic
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from playwright.async_api import ElementHandle
from pydantic import ValidationError

from matoloop_agent.browser_contracts import (
    BrowserActionResult,
    BrowserBatchResult,
    BrowserExecuteArguments,
    BrowserFormField,
)
from matoloop_agent.browser_worker import BrowserWorker
from matoloop_agent.filesystem import AgentError

QUASAR_ASSETS = tuple(
    Path("/test-node-modules") / name
    for name in (
        "vue/dist/vue.global.prod.js",
        "quasar/dist/quasar.umd.prod.js",
        "quasar/dist/quasar.prod.css",
    )
)


class BrowserObservationTests(unittest.IsolatedAsyncioTestCase):
    def test_wire_bounds(self):
        field = dict(name="Name", ref="f2e12", type="textbox", value="")
        args = dict(browserId=str(uuid4()), tabId=str(uuid4()), action="fill_form", fields=[field])
        self.assertFalse(BrowserExecuteArguments(**args).settle)
        self.assertIsNone(BrowserActionResult().batch)
        for invalid in (
            {**args, "tabId": None},
            {**args, "fields": []},
            {**args, "fields": [field] * 21},
            {**args, "fields": [field, field]},
            {**args, "action": "inspect"},
            {**args, "snapshotSha256": "a" * 64},
            {**args, "settle": "true"},
            {
                **args,
                "fields": [
                    {**field, "ref": "e1", "value": "a" * 20000},
                    {**field, "ref": "e2", "value": "a" * 20000},
                ],
            },
        ):
            with self.subTest(invalid=str(invalid)[:100]), self.assertRaises(ValidationError):
                BrowserExecuteArguments(**invalid)
        for invalid in (
            {"name": ""},
            {"name": "x" * 201},
            {"ref": "e1 >> button"},
            {"ref": "e1\n"},
            {"value": "a" * 32769},
            {"type": "checkbox", "value": "yes"},
            {"type": "radio", "value": "false"},
        ):
            with self.subTest(invalid=str(invalid)[:100]), self.assertRaises(ValidationError):
                BrowserFormField(**{**field, **invalid})
        for value in ("true", "false"):
            BrowserFormField(**{**field, "type": "checkbox", "value": value})
        BrowserFormField(**{**field, "type": "radio", "value": "true"})
        for invalid in (
            dict(completed=0, total=1),
            dict(completed=2, total=1),
            dict(completed=1, total=1, errorCode="browser.snapshotChanged"),
            dict(completed=0, total=21, errorCode="browser.outcomeUnknown"),
            dict(completed=True, total=1),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                BrowserBatchResult(**invalid)
        BrowserBatchResult(completed=0, total=1, errorCode="browser.outcomeUnknown")
        BrowserBatchResult(completed=1, total=1)

    async def test_observation_failure_does_not_discard_success(self):
        worker = BrowserWorker(Path("/tmp"))
        tab_id = str(uuid4())
        page = Mock(
            bring_to_front=AsyncMock(),
            title=AsyncMock(return_value=""),
            is_closed=Mock(return_value=False),
            url="about:blank",
        )
        worker.context, worker.tabs[tab_id] = Mock(), page
        worker._fill_form = AsyncMock(return_value={"completed": 1, "total": 1})
        worker._settle = AsyncMock(side_effect=AgentError("browser.observationUnstable", "Test"))
        worker._inspect = AsyncMock()
        args = dict(browserId=str(uuid4()), tabId=tab_id)
        result = await worker.execute(
            BrowserExecuteArguments(
                **args,
                action="fill_form",
                observe=True,
                fields=[dict(name="A", ref="e1", type="textbox", value="x")],
            )
        )
        self.assertEqual(result["batch"], {"completed": 1, "total": 1, "errorCode": None})
        self.assertEqual(result["data"], {"observationFailed": True})
        worker._inspect.assert_not_awaited()
        with self.assertRaises(AgentError) as error:
            await worker.execute(BrowserExecuteArguments(**args, action="inspect", settle=True))
        self.assertEqual(error.exception.code, "browser.observationUnstable")
        worker._settle.reset_mock()
        worker._inspect.return_value = ("", {})
        await worker.execute(BrowserExecuteArguments(**args, action="inspect"))
        worker._settle.assert_not_awaited()

    async def test_cancel_disposes_all_fixed_handles(self):
        worker = BrowserWorker(Path("/tmp"))
        guard = Mock(evaluate=AsyncMock(return_value=True), dispose=AsyncMock())
        handle = Mock(
            evaluate_handle=AsyncMock(return_value=guard),
            is_visible=AsyncMock(return_value=True),
            is_enabled=AsyncMock(return_value=True),
            fill=AsyncMock(side_effect=asyncio.CancelledError),
            dispose=AsyncMock(),
        )
        page = Mock()
        page.locator.return_value.element_handles = AsyncMock(return_value=[handle])
        worker.document_ids[page] = "document"
        with self.assertRaises(asyncio.CancelledError):
            await worker._fill_form(
                page, [BrowserFormField(name="A", ref="e1", type="textbox", value="x")]
            )
        guard.dispose.assert_awaited_once()
        handle.dispose.assert_awaited_once()
        handle.fill.assert_awaited_once()

    @unittest.skipUnless(
        os.getuid() == 0 and os.environ.get("MATOLOOP_AGENT_CONTAINER") == "1",
        "Requires the disposable development container",
    )
    async def test_disposable_headed_browser(self):
        process = await asyncio.create_subprocess_exec(
            "runuser",
            "-u",
            "matoloop-browser",
            "--",
            sys.executable,
            __file__,
            "BrowserHeadedObservations",
            "-v",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 100)
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
class BrowserHeadedObservations(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.worker = BrowserWorker(Path(temporary.name))
        self.addAsyncCleanup(self.worker.close)
        self.assertEqual((await self.worker.start())["state"], "ready")
        self.page = self.worker.context.pages[0]
        self.tab_id = next(key for key, page in self.worker.tabs.items() if page is self.page)
        self.browser_id = str(uuid4())

    async def execute(self, action, **kwargs):
        if action == "fill_form":
            kwargs.setdefault("observe", True)
        return await self.worker.execute(
            BrowserExecuteArguments(
                browserId=self.browser_id, tabId=self.tab_id, action=action, **kwargs
            )
        )

    async def fields(self):
        result = await self.execute("inspect", settle=True)
        return [
            dict(name=name, ref=ref, type="textbox", value=name + " value")
            for name, ref in re.findall(r'textbox "([ABC])" \[ref=([^\]]+)\]', result["text"])
        ]

    @unittest.skipUnless(
        all(path.is_file() for path in QUASAR_ASSETS),
        "Mount frontend node_modules read-only at /test-node-modules",
    )
    async def test_actual_quasar_dialog_and_select(self):
        async with asyncio.timeout(45):
            await self.page.route("**/*", lambda route: route.abort())
            user_agent = await self.page.evaluate("navigator.userAgent")
            session = await self.worker.page_sessions[self.page]
            for width, height in ((1280, 800), (390, 844)):
                for dark in (False, True):
                    with self.subTest(width=width, dark=dark):
                        await self.worker.resize(width, height)
                        await session.send(
                            "Emulation.setUserAgentOverride",
                            {
                                "userAgent": user_agent
                                if width == 1280
                                else "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
                            },
                        )
                        await self.page.goto("about:blank")
                        await self.page.set_content("""<div id="app">
                            <q-btn id="open" label="Open" @click="open=true"></q-btn>
                            <q-dialog v-model="open" full-width><q-card class="q-pa-md">
                                <q-input v-model="title" label="Title"></q-input>
                                <q-select v-model="project" label="Project"
                                    :options="['Alpha','Beta']"></q-select>
                                <q-select v-model="status" label="Status"
                                    :options="['Draft','Ready']"></q-select>
                            </q-card></q-dialog></div>""")
                        await self.page.add_style_tag(path=str(QUASAR_ASSETS[2]))
                        for path in QUASAR_ASSETS[:2]:
                            await self.page.add_script_tag(path=str(path))
                        await self.page.evaluate(
                            """dark => {
                            Vue.createApp({setup:()=>({open:Vue.ref(false), title:Vue.ref(''),
                                project:Vue.ref(null), status:Vue.ref(null)})})
                                .use(Quasar, {config:{dark}}).mount('#app');
                        }""",
                            dark,
                        )
                        versions = await self.page.evaluate("[Vue.version, Quasar.version]")
                        self.assertEqual(versions, ["3.5.13", "2.18.2"])
                        self.assertEqual(
                            await self.page.evaluate(
                                "[innerWidth, innerHeight, Quasar.Dark.isActive, "
                                "Boolean(Quasar.Platform.is.mobile)]"
                            ),
                            [width, height, dark, width == 390],
                        )
                        result = await self.execute("click", selector="#open", observe=True)
                        self.assertRegex(result["text"], r'textbox "Title" \[ref=\w+\]')
                        for label, choices in (
                            ("Project", ("Alpha", "Beta")),
                            ("Status", ("Draft", "Ready")),
                        ):
                            ref = re.search(
                                r'combobox "' + label + r'" \[ref=(\w+)\]', result["text"]
                            )[1]
                            result = await self.execute(
                                "click", selector="aria-ref=" + ref, observe=True
                            )
                            self.assertIn("listbox", result["text"])
                            for choice in choices:
                                self.assertIn('option "' + choice + '"', result["text"])
                            ref = re.search(
                                r'option "' + choices[1] + r'" \[ref=(\w+)\]', result["text"]
                            )[1]
                            result = await self.execute(
                                "click", selector="aria-ref=" + ref, observe=True
                            )
                            self.assertNotIn("listbox", result["text"])
                            self.assertNotIn('option "', result["text"])
                            self.assertIn(choices[1], result["text"])
                        for _ in range(2):
                            repeated = await self.execute("inspect", settle=True)
                            self.assertEqual(
                                repeated["data"]["snapshotSha256"], result["data"]["snapshotSha256"]
                            )
                        print(
                            f"REAL QUASAR Vue={versions[0]} Quasar={versions[1]} "
                            f"viewport={width}x{height} dark={dark}: passed",
                            flush=True,
                        )

    async def test_dialog_menu_load_and_unstable_ceiling(self):
        # Reproduce the transition/mount ordering of Quasar dialogs and listboxes.
        await self.page.set_content("""<style>
            @keyframes enter {from {opacity:0} to {opacity:1}}
            @keyframes leave {from {opacity:1} to {opacity:0}}
            @keyframes spin {to {transform:rotate(360deg)}}
            .enter {animation:enter .3s} .leave {animation:leave .3s}
            #spinner {animation:spin 1s infinite}
            </style><span id=spinner>Busy indicator</span>
            <button id=open onclick="dialog.hidden=false;dialog.className='enter';
                dialog.onanimationend=()=>dialog.innerHTML='<input aria-label=Name>'">Open</button>
            <div role=dialog id=dialog hidden></div>
            <button id=menu aria-expanded=false onclick="this.setAttribute('aria-expanded','true');
                list.hidden=false;list.className='enter';list.onanimationend=()=>
                list.innerHTML='<div role=option>Mounted option</div>'">Menu</button>
            <div id=list role=listbox hidden></div>
            <button id=close onclick="menu.setAttribute('aria-expanded','false');
                list.className='leave';list.onanimationend=()=>list.hidden=true">Close</button>""")
        result = await self.execute("click", selector="#open", observe=True)
        self.assertIn('textbox "Name" [ref=', result["text"])
        result = await self.execute("click", selector="#menu", observe=True)
        self.assertIn('option "Mounted option"', result["text"])
        result = await self.execute("click", selector="#close", observe=True)
        self.assertNotIn('option "Mounted option"', result["text"])

        entered = asyncio.Event()

        async def delayed(route):
            entered.set()
            await asyncio.sleep(0.35)
            await route.fulfill(body="Loaded", headers={"Access-Control-Allow-Origin": "*"})

        await self.page.route("http://pending.test/**", delayed)
        await self.page.evaluate("""() => {
            fetch('http://pending.test/data').then(r=>r.text()).then(text=>{
                document.body.insertAdjacentHTML('beforeend', '<button>'+text+'</button>');
            });
        }""")
        result = await self.execute("inspect", settle=True)
        self.assertIn('button "Loaded"', result["text"])
        entered.clear()
        navigation = asyncio.create_task(self.page.goto("http://pending.test/navigation"))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            result = await self.execute("inspect", settle=True)
            self.assertIn("Loaded", result["text"])
        finally:
            await navigation
        await self.page.set_content('<input aria-label="Name">')
        await self.page.evaluate("""() => {
            window.churn=setInterval(()=>document.body.dataset.tick=Date.now(), 40);
        }""")
        started = monotonic()
        with self.assertRaises(AgentError) as error:
            await self.execute("inspect", settle=True)
        self.assertEqual(error.exception.code, "browser.observationUnstable")
        self.assertLess(monotonic() - started, 2.3)
        result = await self.execute("fill", selector="input", text="known success", observe=True)
        self.assertEqual(result["data"], {"observationFailed": True})
        self.assertEqual(await self.page.locator("input").input_value(), "known success")
        await self.page.evaluate("clearInterval(churn)")
        self.assertIn("known success", (await self.execute("inspect", settle=True))["text"])

    async def test_batch_prefix_types_and_single_observation(self):
        html = "<form><input aria-label=A><input aria-label=B><input aria-label=C></form>"
        await self.page.set_content(html)
        fields = await self.fields()
        self.assertEqual(len(fields), 3)
        with patch.object(self.worker, "_inspect", wraps=self.worker._inspect) as inspect:
            started = monotonic()
            for field in fields:
                await self.execute(
                    "fill", selector="aria-ref=" + field["ref"], text=field["value"], observe=True
                )
            sequential_ms = (monotonic() - started) * 1000
            self.assertEqual(inspect.await_count, 3)
        await self.page.set_content(html)
        fields = await self.fields()
        with patch.object(self.worker, "_inspect", wraps=self.worker._inspect) as inspect:
            started = monotonic()
            result = await self.execute("fill_form", fields=fields)
            batch_ms = (monotonic() - started) * 1000
            inspect.assert_awaited_once()
        self.assertEqual(result["batch"], {"completed": 3, "total": 3, "errorCode": None})
        self.assertEqual(
            result["playwrightCode"]["completed"],
            "\n".join(
                f"await page.locator({'aria-ref=' + field['ref']!r})"
                f".fill({field['value']!r}, timeout=1000)"
                for field in fields
            ),
        )
        self.assertIsNone(result["playwrightCode"]["attempted"])
        self.assertEqual(
            await self.page.locator("input").evaluate_all("nodes=>nodes.map(n=>n.value)"),
            ["A value", "B value", "C value"],
        )
        print(
            f"BROWSER TIMING sequential: calls=3 observations=3 ms={sequential_ms:.1f}; "
            f"batch: calls=1 observations=1 ms={batch_ms:.1f}",
            flush=True,
        )

        for effect in (
            "document.querySelectorAll('input')[1].replaceWith(document.createElement('input'))",
            "document.querySelectorAll('input')[1].value='changed'",
            "document.querySelector('form').action='http://changed.test/'",
            "location.href='about:blank#changed'",
        ):
            with self.subTest(effect=effect):
                await self.page.set_content(html)
                fields = await self.fields()
                await self.page.locator("input").first.evaluate(
                    "(node,effect)=>node.addEventListener('input',()=>eval(effect), {once:true})",
                    effect,
                )
                result = await self.execute("fill_form", fields=fields)
                self.assertEqual(
                    result["batch"],
                    {"completed": 1, "total": 3, "errorCode": "browser.snapshotChanged"},
                )
                self.assertEqual(
                    result["playwrightCode"],
                    dict(
                        completed=f"await page.locator({'aria-ref=' + fields[0]['ref']!r})"
                        f".fill({fields[0]['value']!r}, timeout=1000)",
                        attempted=None,
                        truncated=False,
                        errorCode="browser.snapshotChanged",
                    ),
                )

        await self.page.set_content(html)
        fields = await self.fields()
        fields[2]["type"] = "combobox"
        with self.assertRaises(AgentError) as error:
            await self.execute("fill_form", fields=fields)
        self.assertEqual(error.exception.code, "browser.invalidTarget")
        self.assertEqual(await self.page.locator("input").first.input_value(), "")

        # A dispatched field may have taken effect even when its acknowledgement fails.
        await self.page.set_content(html)
        fields = await self.fields()
        fill = ElementHandle.fill
        calls = 0

        async def uncertain(handle, *args, **kwargs):
            nonlocal calls
            calls += 1
            await fill(handle, *args, **kwargs)
            if calls == 2:
                raise RuntimeError("Synthetic lost acknowledgement")

        with patch.object(ElementHandle, "fill", uncertain):
            result = await self.execute("fill_form", fields=fields)
        self.assertEqual(
            result["batch"], {"completed": 1, "total": 3, "errorCode": "browser.outcomeUnknown"}
        )
        self.assertEqual(
            result["playwrightCode"],
            dict(
                completed=f"await page.locator({'aria-ref=' + fields[0]['ref']!r})"
                f".fill({fields[0]['value']!r}, timeout=1000)",
                attempted=f"await page.locator({'aria-ref=' + fields[1]['ref']!r})"
                f".fill({fields[1]['value']!r}, timeout=1000)",
                truncated=False,
                errorCode="browser.outcomeUnknown",
            ),
        )
        self.assertEqual(calls, 2)
        self.assertEqual(await self.page.locator("input").nth(1).input_value(), "B value")
        self.assertEqual(await self.page.locator("input").nth(2).input_value(), "")

        await self.page.set_content(html)
        fields = await self.fields()

        async def navigates(handle, *args, **kwargs):
            await fill(handle, *args, **kwargs)
            await self.page.goto("about:blank")

        with patch.object(ElementHandle, "fill", navigates):
            result = await self.execute("fill_form", fields=fields)
        self.assertEqual(
            result["batch"], {"completed": 1, "total": 3, "errorCode": "browser.snapshotChanged"}
        )

        await self.page.set_content(html)
        fields = await self.fields()
        with patch.object(self.worker, "_inspect", wraps=self.worker._inspect) as inspect:
            result = await self.execute("fill_form", fields=fields, observe=False)
            inspect.assert_not_awaited()
        self.assertIsNone(result["text"])
        self.assertEqual(result["batch"]["completed"], 3)

        await self.page.set_content("""<input type=checkbox aria-label=Check>
            <input type=radio aria-label=Radio><select aria-label=Choice>
            <option value=a>A</option><option value=b>B</option></select>""")
        result = await self.execute("inspect", settle=True)
        refs = re.findall(r'(checkbox|radio|combobox) "([^"]+)" \[ref=([^\]]+)\]', result["text"])
        self.assertEqual(len(refs), 3)
        result = await self.execute(
            "fill_form",
            fields=[
                dict(type=kind, name=name, ref=ref, value="B" if kind == "combobox" else "true")
                for kind, name, ref in refs
            ],
        )
        self.assertEqual(result["batch"]["completed"], 3)
        self.assertTrue(await self.page.locator("input[type=checkbox]").is_checked())
        self.assertTrue(await self.page.locator("input[type=radio]").is_checked())
        self.assertEqual(await self.page.locator("select").input_value(), "b")

        await self.page.set_content("""<div role=checkbox aria-label=Custom aria-checked=false
            onclick="this.setAttribute('aria-checked',
                this.getAttribute('aria-checked') !== 'true')"
            >Custom</div><div role=combobox aria-label=Unsupported>Custom select</div>""")
        result = await self.execute("inspect", settle=True)
        ref = re.search(r'checkbox "Custom" \[ref=([^\]]+)\]', result["text"])[1]
        result = await self.execute(
            "fill_form", fields=[dict(type="checkbox", name="Custom", ref=ref, value="true")]
        )
        self.assertEqual(result["batch"]["completed"], 1)
        self.assertEqual(
            await self.page.get_by_role("checkbox").get_attribute("aria-checked"), "true"
        )
        ref = re.search(r'combobox "Unsupported" \[ref=([^\]]+)\]', result["text"])[1]
        with self.assertRaises(AgentError) as error:
            await self.execute(
                "fill_form",
                fields=[dict(type="combobox", name="Unsupported", ref=ref, value="value")],
            )
        self.assertEqual(error.exception.code, "browser.invalidTarget")

    async def test_native_select_uses_unique_enabled_label(self):
        berlin = '<option value="42">Berlin</option>'
        for options, value, succeeds in (
            (berlin, "Berlin", True),
            ('<option value="42" label="Berlin">Internal city</option>', "Berlin", True),
            (berlin, "42", False),
            (berlin, "berlin", False),
            (berlin + '<option value="43">Berlin</option>', "Berlin", False),
            (berlin + '<option value="43" disabled>Berlin</option>', "Berlin", False),
            (berlin.replace('value="42"', 'value="42" disabled'), "Berlin", False),
            ('<optgroup label="Cities" disabled>' + berlin + "</optgroup>", "Berlin", False),
        ):
            with self.subTest(options=options, value=value):
                await self.page.set_content(
                    '<input aria-label="Note"><select aria-label="City">'
                    '<option value="">Choose</option>' + options + "</select>"
                    "<script>window.effects=0;document.oninput=()=>window.effects++;</script>"
                )
                result = await self.execute("inspect", settle=True)
                if options == berlin:
                    self.assertIn('option "Berlin"', result["text"])
                refs = dict(
                    re.findall(r'(?:textbox|combobox) "([^"]+)" \[ref=(\w+)\]', result["text"])
                )
                fields = [
                    dict(name="Note", ref=refs["Note"], type="textbox", value="changed"),
                    dict(name="City", ref=refs["City"], type="combobox", value=value),
                ]
                if succeeds:
                    result = await self.execute("fill_form", fields=fields)
                    self.assertEqual(
                        result["batch"], {"completed": 2, "total": 2, "errorCode": None}
                    )
                    self.assertEqual(await self.page.locator("select").input_value(), "42")
                    self.assertEqual(
                        await self.page.locator("select").evaluate(
                            "node => node.selectedOptions[0].label"
                        ),
                        "Berlin",
                    )
                else:
                    with self.assertRaises(AgentError) as error:
                        await self.execute("fill_form", fields=fields)
                    self.assertEqual(error.exception.code, "browser.invalidTarget")
                    self.assertEqual(await self.page.locator("input").input_value(), "")
                    self.assertEqual(await self.page.locator("select").input_value(), "")
                    self.assertEqual(await self.page.evaluate("window.effects"), 0)


if __name__ == "__main__":
    unittest.main()
