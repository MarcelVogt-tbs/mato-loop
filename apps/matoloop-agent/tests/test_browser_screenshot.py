import asyncio
import base64
import json
import os
import random
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from PIL import Image
from pydantic import ValidationError

from matoloop_agent.browser_contracts import (
    MAX_CHUNK_BYTES,
    MAX_FILE_BYTES,
    MAX_OBSERVATION_BYTES,
    BrowserActionResult,
    BrowserExecuteArguments,
    BrowserFileArguments,
)
from matoloop_agent.browser_worker import BrowserWorker
from matoloop_agent.filesystem import AgentError


class BrowserScreenshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.worker = BrowserWorker(Path(temporary.name))
        self.worker.live = self.worker.root
        self.browser_id, self.tab_id = str(uuid4()), str(uuid4())
        self.page = Mock(
            screenshot=AsyncMock(),
            bring_to_front=AsyncMock(),
            title=AsyncMock(return_value=""),
            is_closed=Mock(return_value=False),
            url="about:blank",
        )
        self.worker.context = Mock(close=AsyncMock())
        self.worker.tabs[self.tab_id] = self.page

    async def capture(self):
        return await self.worker.execute(
            BrowserExecuteArguments(
                browserId=self.browser_id, tabId=self.tab_id, action="screenshot"
            )
        )

    async def file(self, action, **kwargs):
        return await self.worker.file(
            BrowserFileArguments(browserId=self.browser_id, action=action, **kwargs)
        )

    def png(self, size=(1280, 800), *, noise=False):
        with (
            BytesIO() as output,
            Image.frombytes(
                "RGB",
                size,
                random.Random(42).randbytes(size[0] * size[1] * 3)
                if noise
                else b"\xff\x00\x00" * size[0] * size[1],
            ) as image,
        ):
            image.save(output, format="PNG")
            return output.getvalue()

    def test_descriptor_is_strict_and_bounded(self):
        self.assertIsNone(BrowserActionResult().screenshot)
        descriptor = {"fileId": str(uuid4()), "size": MAX_OBSERVATION_BYTES}
        self.assertEqual(
            BrowserActionResult(screenshot=descriptor).model_dump(by_alias=True)["screenshot"],
            descriptor,
        )
        for invalid in (
            "base64",
            {**descriptor, "fileId": "../private"},
            {**descriptor, "size": MAX_OBSERVATION_BYTES + 1},
            {**descriptor, "size": -1},
            {**descriptor, "size": True},
            {**descriptor, "size": "1"},
            {**descriptor, "path": "/private"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                BrowserActionResult(screenshot=invalid)

    async def test_large_png_chunks_are_exact_private_and_read_only(self):
        original = self.png(noise=True)
        self.assertGreater(len(original), 500_000)
        self.page.screenshot.return_value = original
        result = await self.capture()
        descriptor = result["screenshot"]
        self.assertEqual(descriptor["size"], len(original))
        self.assertEqual(result["downloads"], [])
        self.assertLess(len(json.dumps(result)), 1024)
        file_id = descriptor["fileId"]
        chunks = []
        for offset in range(0, descriptor["size"], MAX_CHUNK_BYTES):
            chunk = await self.file("read", fileId=file_id, offset=offset)
            data = base64.b64decode(chunk["data"], validate=True)
            self.assertLessEqual(len(data), MAX_CHUNK_BYTES)
            self.assertEqual(chunk["offset"], offset + len(data))
            self.assertEqual(chunk["complete"], chunk["offset"] == descriptor["size"])
            chunks.append(data)
        self.assertEqual(b"".join(chunks), original)
        self.assertEqual((self.worker.live / file_id).stat().st_mode & 0o777, 0o600)
        for action in ("choose", "write", "finish"):
            with self.subTest(action=action), self.assertRaises(AgentError) as error:
                await self.file(action, fileId=file_id)
            self.assertEqual(error.exception.code, "browser.invalidTarget")
        with self.assertRaises(AgentError):
            self.worker.upload_payload(file_id)
        with self.assertRaises(AgentError):
            await self.file("read", fileId=file_id, offset=len(original) + 1)
        with self.assertRaises(ValidationError):
            await self.file("read", fileId=file_id, maxBytes=MAX_CHUNK_BYTES + 1)
        (self.worker.live / file_id).unlink()
        human = self.worker.live / "human.txt"
        human.write_bytes(b"human file")
        (self.worker.live / file_id).symlink_to(human)
        with self.assertRaises(OSError):
            await self.file("read", fileId=file_id)
        await self.file("delete", fileId=file_id)
        self.assertEqual(human.read_bytes(), b"human file")
        with self.assertRaises(AgentError):
            await self.file("read", fileId=file_id)

    async def test_scaling_and_abandoned_cleanup_preserve_human_files(self):
        upload = await self.file("begin", filename="human.txt", size=3)
        await self.file("write", fileId=upload["fileId"], data="YWJj")
        await self.file("finish", fileId=upload["fileId"])
        download = await self.file("begin", filename="download.txt", size=0)
        await self.file("finish", fileId=download["fileId"])
        self.worker.files[download["fileId"]]["download"] = True
        previous = None
        for source, expected in (
            ((320, 240), (320, 240)),
            ((1280, 800), (1280, 800)),
            ((3840, 2160), (1464, 823)),
            ((3840, 240), (1568, 98)),
            ((320, 2160), (232, 1568)),
        ):
            with self.subTest(source=source):
                self.page.screenshot.return_value = self.png(source)
                result = await self.capture()
                self.assertEqual(
                    [entry["id"] for entry in result["downloads"]], [download["fileId"]]
                )
                file_id = result["screenshot"]["fileId"]
                with Image.open(self.worker.live / file_id) as image:
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(image.size, expected)
                    self.assertLessEqual(image.width * image.height, 1.15 * 1024 * 1024)
                if previous:
                    self.assertNotIn(previous, self.worker.files)
                    self.assertFalse((self.worker.live / previous).exists())
                previous = file_id
        self.page.screenshot.assert_awaited_with(type="png", full_page=False, timeout=8000)
        await self.worker.close()
        self.assertFalse((self.worker.live / previous).exists())
        self.assertEqual(set(self.worker.files), {upload["fileId"], download["fileId"]})
        self.assertEqual((self.worker.live / upload["fileId"]).read_bytes(), b"abc")
        self.assertTrue((self.worker.live / download["fileId"]).exists())

    async def test_capture_failure_cancellation_quota_and_write_failure_leave_no_screenshot(self):
        self.page.screenshot.return_value = self.png()
        for failure in (RuntimeError("capture failed"), asyncio.CancelledError()):
            await self.capture()
            self.page.screenshot.side_effect = failure
            with self.assertRaises(type(failure)):
                await self.capture()
            self.assertEqual(self.worker.files, {})
            self.assertEqual(list(self.worker.live.iterdir()), [])
            self.page.screenshot.side_effect = None
        with patch.object(self.worker, "total_size", return_value=2 * MAX_FILE_BYTES):
            with self.assertRaises(AgentError) as error:
                await self.capture()
            self.assertEqual(error.exception.code, "browser.fileTooLarge")
        for _ in range(32):
            await self.file("begin", filename="human.txt", size=0)
        with self.assertRaises(AgentError) as error:
            await self.capture()
        self.assertEqual(error.exception.code, "browser.fileTooLarge")
        for file_id in list(self.worker.files):
            await self.file("delete", fileId=file_id)
        real_open = os.open
        calls = 0

        def fail_write(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic staging failure")
            return real_open(*args, **kwargs)

        with patch("matoloop_agent.browser_worker.os.open", side_effect=fail_write):
            with self.assertRaises(OSError):
                await self.capture()
        self.assertEqual(self.worker.files, {})
        self.assertEqual(list(self.worker.live.iterdir()), [])
