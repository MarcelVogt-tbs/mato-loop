import ast
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from pydantic import ValidationError

from matoloop_agent.browser_contracts import (
    BrowserActionResult,
    BrowserCodeFeedback,
    BrowserExecuteArguments,
)
from matoloop_agent.browser_worker import BrowserWorker
from matoloop_agent.filesystem import AgentError


class BrowserCodeFeedbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.worker = BrowserWorker(Path("/tmp"))
        self.tab_id = str(uuid4())
        self.args = dict(browserId=str(uuid4()), tabId=self.tab_id)
        self.page = Mock(
            bring_to_front=AsyncMock(),
            goto=AsyncMock(),
            close=AsyncMock(),
            title=AsyncMock(return_value="Test"),
            url="about:blank",
            is_closed=Mock(return_value=False),
        )
        self.locator = Mock(
            click=AsyncMock(),
            fill=AsyncMock(),
            press_sequentially=AsyncMock(),
            press=AsyncMock(),
            is_editable=AsyncMock(return_value=True),
            evaluate=AsyncMock(return_value=True),
        )
        self.page.locator.return_value = self.locator
        self.page.keyboard.press = AsyncMock()
        self.page.mouse.wheel = AsyncMock()
        self.worker.context = Mock(new_page=AsyncMock(return_value=self.page))
        self.worker.tabs[self.tab_id] = self.page
        self.worker.active_tab_id = self.tab_id
        self.worker.navigation_state = AsyncMock()
        self.worker._settle = AsyncMock()
        self.worker._inspect = AsyncMock(
            return_value=("- textbox", {"snapshotSha256": "digest"}, None)
        )

    async def execute(self, action, **kwargs):
        result = await self.worker.execute(
            BrowserExecuteArguments(**self.args, action=action, **kwargs)
        )
        BrowserActionResult.model_validate(result)
        return result

    def assert_code(self, result, completed="", attempted=None, error=None, truncated=False):
        self.assertEqual(
            result["playwrightCode"],
            dict(completed=completed, attempted=attempted, truncated=truncated, errorCode=error),
        )
        for code in (completed, attempted):
            if code:
                ast.parse(code)
        self.assertLessEqual(len(completed) + len(attempted or ""), 32768)

    def test_strict_combined_wire_budget(self):
        self.assertIsNone(BrowserActionResult().playwright_code)
        self.assertEqual(
            BrowserCodeFeedback().model_dump(by_alias=True),
            dict(completed="", attempted=None, truncated=False, errorCode=None),
        )
        for kwargs in (
            dict(completed="x" * 32768),
            dict(attempted="x" * 32768, errorCode="browser.outcomeUnknown"),
            dict(completed="x" * 16384, attempted="x" * 16384, errorCode="browser.outcomeUnknown"),
        ):
            BrowserActionResult(playwrightCode=kwargs)
        for kwargs in (
            dict(completed="x" * 32769),
            dict(attempted="x" * 32769),
            dict(completed="x" * 16385, attempted="x" * 16384),
            dict(completed=None),
            dict(completed=42),
            dict(attempted=False),
            dict(truncated="true"),
            dict(errorCode="raw exception"),
            dict(unknown=True),
            dict(attempted="await page.close()"),
        ):
            with self.subTest(fields=list(kwargs)), self.assertRaises(ValidationError):
                BrowserActionResult(playwrightCode=kwargs)

    async def test_literal_actions_preserve_exact_arguments_and_options(self):
        selector = 'aria-ref=f2e12 >> text="it\'s \\ quoted\n\u00e4\U0001f680"'
        value = "\"'\\\n\r\t\x00\u00e4\U0001f680');not_code('"
        target = f"page.locator({selector!r})"
        for action, kwargs, method, values, options, expected in (
            (
                "click",
                dict(selector=selector),
                self.locator.click,
                (),
                {},
                f"await {target}.click()",
            ),
            (
                "fill",
                dict(selector=selector, text=value),
                self.locator.fill,
                (value,),
                {},
                f"await {target}.fill({value!r})",
            ),
            (
                "fill",
                dict(selector=selector, text=value, slowly=True),
                self.locator.press_sequentially,
                (value,),
                {},
                f"await {target}.press_sequentially({value!r})",
            ),
            (
                "press",
                dict(selector=selector, key=value),
                self.locator.press,
                (value,),
                {},
                f"await {target}.press({value!r})",
            ),
            (
                "press",
                dict(key=value),
                self.page.keyboard.press,
                (value,),
                {},
                f"await page.keyboard.press({value!r})",
            ),
            (
                "scroll",
                dict(direction="up"),
                self.page.mouse.wheel,
                (0, -600),
                {},
                "await page.mouse.wheel(0, -600)",
            ),
            (
                "scroll",
                dict(direction="down"),
                self.page.mouse.wheel,
                (0, 600),
                {},
                "await page.mouse.wheel(0, 600)",
            ),
            (
                "navigate",
                dict(url="https://example.test/a?q='quoted'"),
                self.page.goto,
                ("https://example.test/a?q='quoted'",),
                dict(wait_until="domcontentloaded", timeout=15000),
                "await page.goto(\"https://example.test/a?q='quoted'\", "
                "wait_until='domcontentloaded', timeout=15000)",
            ),
            ("select", {}, self.page.bring_to_front, (), {}, "await page.bring_to_front()"),
        ):
            with self.subTest(action=action, kwargs=kwargs):
                method.reset_mock()
                result = await self.execute(action, **kwargs)
                self.assert_code(result, expected)
                method.assert_awaited_once_with(*values, **options)
                if "selector" in kwargs:
                    self.page.locator.assert_called_with(selector)
        self.worker._settle.assert_not_awaited()
        self.worker._inspect.assert_not_awaited()
        self.worker.navigation_state.assert_not_awaited()

    async def test_fill_submit_partial_failure_and_no_retry(self):
        for slowly in (False, True):
            fill = self.locator.press_sequentially if slowly else self.locator.fill
            method = "press_sequentially" if slowly else "fill"
            first = f"await page.locator('#name').{method}('literal')"
            enter = "await page.locator('#name').press('Enter')"
            for failure in ("none", "fill", "submit"):
                with self.subTest(slowly=slowly, failure=failure):
                    fill.reset_mock()
                    self.locator.press.reset_mock()
                    fill.side_effect = (
                        RuntimeError("private fill error") if failure == "fill" else None
                    )
                    self.locator.press.side_effect = (
                        RuntimeError("private submit error") if failure == "submit" else None
                    )
                    result = await self.execute(
                        "fill", selector="#name", text="literal", slowly=slowly, submit=True
                    )
                    if failure == "none":
                        self.assert_code(result, first + "\n" + enter)
                    elif failure == "fill":
                        self.assert_code(result, attempted=first, error="browser.outcomeUnknown")
                        self.locator.press.assert_not_awaited()
                    else:
                        self.assert_code(result, first, enter, "browser.outcomeUnknown")
                    fill.assert_awaited_once_with("literal")
                    if failure != "fill":
                        self.locator.press.assert_awaited_once_with("Enter")
                    self.assertNotIn("private", str(result))
        self.worker._settle.assert_not_awaited()
        self.worker._inspect.assert_not_awaited()

    async def test_dispatched_errors_and_cancellation(self):
        for action, kwargs, operation, line in (
            ("click", dict(selector="#x"), self.locator.click, "await page.locator('#x').click()"),
            (
                "navigate",
                dict(url="https://example.test"),
                self.page.goto,
                "await page.goto('https://example.test', "
                "wait_until='domcontentloaded', timeout=15000)",
            ),
            (
                "press",
                dict(key="Enter"),
                self.page.keyboard.press,
                "await page.keyboard.press('Enter')",
            ),
            (
                "scroll",
                dict(direction="up"),
                self.page.mouse.wheel,
                "await page.mouse.wheel(0, -600)",
            ),
            ("select", {}, self.page.bring_to_front, "await page.bring_to_front()"),
            ("newtab", {}, self.worker.context.new_page, "page = await page.context.new_page()"),
        ):
            with self.subTest(action=action):
                operation.side_effect = TimeoutError("private failure")
                result = await self.execute(action, **kwargs)
                self.assert_code(result, attempted=line, error="browser.outcomeUnknown")
                operation.side_effect = None
        self.locator.click.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.execute("click", selector="#x")
        self.worker._inspect.assert_not_awaited()
        self.worker._settle.assert_not_awaited()

    async def test_tab_creation_close_and_late_failure_preserve_steps(self):
        self.worker.desktop = True
        result = await self.execute("newtab", url="https://example.test")
        create = "page = await page.context.new_page()"
        navigate = (
            "await page.goto('https://example.test', wait_until='domcontentloaded', timeout=15000)"
        )
        self.assert_code(result, create + "\n" + navigate)
        self.page.goto.side_effect = RuntimeError("private")
        self.assert_code(
            await self.execute("newtab", url="https://example.test"),
            create,
            navigate,
            "browser.outcomeUnknown",
        )
        self.page.goto.reset_mock()
        self.page.goto.side_effect = None
        self.page.bring_to_front.side_effect = RuntimeError("private focus failure")
        self.assert_code(
            await self.execute("newtab", url="https://example.test"),
            create,
            error="browser.outcomeUnknown",
        )
        self.page.goto.assert_not_awaited()
        self.page.bring_to_front.side_effect = None
        self.page.close.side_effect = RuntimeError("private")
        self.assert_code(
            await self.execute("closetab"),
            "await page.context.new_page()",
            "await page.close()",
            "browser.outcomeUnknown",
        )
        self.page.close.side_effect = None
        self.worker.navigation_state.side_effect = RuntimeError("private")
        result = await self.execute("closetab")
        self.assert_code(result, "await page.context.new_page()\nawait page.close()")
        self.assertEqual(result["data"], {"observationFailed": True})
        self.worker.tabs[str(uuid4())] = self.page
        self.worker.navigation_state.side_effect = None
        self.assert_code(await self.execute("closetab"), "await page.close()")

    async def test_whole_line_budget_omission_on_success_and_failure(self):
        for value in ("x" * 32768, "\x00" * 9000):
            for failure in ("none", "fill", "submit"):
                with self.subTest(length=len(value), failure=failure):
                    self.locator.fill.side_effect = RuntimeError() if failure == "fill" else None
                    self.locator.press.side_effect = RuntimeError() if failure == "submit" else None
                    result = await self.execute("fill", selector="#x", text=value, submit=True)
                    self.locator.fill.assert_awaited_with(value)
                    enter = "await page.locator('#x').press('Enter')"
                    self.assert_code(
                        result,
                        enter if failure == "none" else "",
                        enter if failure == "submit" else None,
                        "browser.outcomeUnknown" if failure != "none" else None,
                        truncated=True,
                    )
        # Each line fits individually, but the combined completed/attempted budget does not.
        value = "x" * (32768 - len("await page.locator('#x').fill('')"))
        self.locator.fill.side_effect = None
        self.locator.press.side_effect = RuntimeError()
        result = await self.execute("fill", selector="#x", text=value, submit=True)
        self.assert_code(
            result,
            f"await page.locator('#x').fill({value!r})",
            error="browser.outcomeUnknown",
            truncated=True,
        )

    async def test_observation_and_title_failure_retain_feedback(self):
        for failure in ("settle", "inspect", "title"):
            with self.subTest(failure=failure):
                self.worker._settle.side_effect = (
                    RuntimeError("private") if failure == "settle" else None
                )
                self.worker._inspect.side_effect = (
                    RuntimeError("private") if failure == "inspect" else None
                )
                self.page.title.side_effect = (
                    RuntimeError("private") if failure == "title" else None
                )
                result = await self.execute("click", selector="#x", observe=True)
                self.assert_code(result, "await page.locator('#x').click()")
                if failure != "title":
                    self.assertEqual(result["data"], {"observationFailed": True})
                else:
                    self.assertEqual(result["tabs"][0]["title"], "")
        self.locator.click.side_effect = RuntimeError("private")
        result = await self.execute("click", selector="#x", observe=True)
        self.assert_code(
            result, attempted="await page.locator('#x').click()", error="browser.outcomeUnknown"
        )

    async def test_no_feedback_for_guards_callbacks_or_predispatch_validation(self):
        self.worker.document_ids[self.page] = "document"
        del self.worker._inspect
        self.locator.evaluate.return_value = {"state": ["private"]}
        self.page.aria_snapshot = AsyncMock(return_value="- textbox")
        before = await self.worker._inspect(self.page, guard=True)
        result = await self.execute("inspect", guard=True)
        self.assertIsNone(result["playwrightCode"])
        self.assertEqual((result["text"], result["data"]), before[:2])
        self.assertEqual(await self.worker._inspect(self.page, guard=True), before)
        for action, kwargs in (
            ("tabs", {}),
            ("diagnostics", {}),
            ("run_code", dict(code="async def run(page): return 42")),
            ("evaluate", dict(function="() => 42")),
            ("screenshot", {}),
        ):
            self.worker.screenshot = AsyncMock(return_value=None)
            self.page.evaluate = AsyncMock(return_value=[True, None, None, 42])
            result = await self.execute(action, **kwargs)
            self.assertIsNone(result["playwrightCode"])
        self.locator.is_editable.return_value = False
        with self.assertRaises(AgentError) as error:
            await self.execute("fill", selector="#x", text="x", slowly=True)
        self.assertEqual(error.exception.code, "browser.invalidTarget")
        self.locator.press_sequentially.assert_not_awaited()
        self.worker.close = AsyncMock()
        with self.assertRaises(AgentError):
            await self.execute("click", selector="#x", allowNetwork=False)
        self.worker.close.assert_awaited_once()
        self.locator.click.assert_not_awaited()

    async def test_batch_confirmed_prefix_and_only_dispatched_attempt(self):
        fields = [
            dict(name="Not a locator", ref="e1", type="textbox", value="'\n\u00e4"),
            dict(name="Not a locator", ref="f2e2", type="combobox", value="Visible label"),
            dict(name="Not a locator", ref="e3", type="checkbox", value="false"),
            dict(name="Not a locator", ref="e4", type="radio", value="true"),
        ]
        lines = [
            f"await page.locator('aria-ref=e1').fill({fields[0]['value']!r}, timeout=1000)",
            "await page.locator('aria-ref=f2e2')"
            ".select_option(label='Visible label', timeout=1000)",
            "await page.locator('aria-ref=e3').set_checked(False, timeout=1000)",
            "await page.locator('aria-ref=e4').set_checked(True, timeout=1000)",
        ]
        for count, error in (
            (4, None),
            (1, "browser.snapshotChanged"),
            (1, "browser.outcomeUnknown"),
            (0, "browser.outcomeUnknown"),
        ):
            self.worker._fill_form = AsyncMock(
                return_value=dict(completed=count, total=4, errorCode=error)
            )
            result = await self.execute("fill_form", fields=fields)
            self.assert_code(
                result,
                "\n".join(lines[:count]),
                lines[count] if error == "browser.outcomeUnknown" else None,
                error,
            )
        self.worker._fill_form.side_effect = AgentError("browser.invalidTarget", "Controlled")
        with self.assertRaises(AgentError):
            await self.execute("fill_form", fields=fields)
        for count, error in ((1, None), (0, "browser.outcomeUnknown")):
            self.worker._fill_form = AsyncMock(
                return_value=dict(completed=count, total=1, errorCode=error)
            )
            result = await self.execute("fill_form", fields=[{**fields[0], "value": "x" * 32768}])
            self.assert_code(result, error=error, truncated=True)


if __name__ == "__main__":
    unittest.main()
