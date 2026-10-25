import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

from matoloop_agent.browser import BrowserRuntime
from matoloop_agent.browser_contracts import (
    BrowserFileArguments,
    BrowserUploadArguments,
)
from matoloop_agent.browser_worker import BrowserWorker
from matoloop_agent.config import AgentConfig
from matoloop_agent.filesystem import Filesystem
from matoloop_agent.process import ProcessRunner
from matoloop_agent.terminal import TerminalManager


class Download:
    def __init__(self, source: Path, filename: str = "result.bin") -> None:
        self.source = source
        self.suggested_filename = filename
        self.deleted = False

    async def path(self):
        return self.source

    async def save_as(self, target):
        Path(target).write_bytes(self.source.read_bytes())

    async def delete(self):
        self.deleted = True

    async def cancel(self):
        self.deleted = True


class BrowserFileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        config = AgentConfig.model_validate(
            {
                "homeDirectory": self.root,
                "backendUrl": "ws://localhost/api/execution-agents/connect",
                "allowInsecure": True,
                "agentId": str(uuid4()),
            }
        )
        config._data_directory = self.root
        filesystem = Filesystem(config)
        terminal = TerminalManager(config, ProcessRunner(config, filesystem))
        self.manager = BrowserRuntime(config, terminal, filesystem)
        self.browser_id = str(uuid4())
        self.manager.browser_id = self.browser_id

    async def test_worker_retains_bounded_download_status_and_origin(self):
        source = self.root / "source"
        source.write_bytes(b"download")
        worker = BrowserWorker(self.root)
        worker.live = self.root

        download = Download(source, "../unsafe/name.bin")
        await worker.download(download, "manual-request", 7)
        status = worker.file_status()["downloads"][0]

        self.assertEqual(status["filename"], ".._unsafe_name.bin")
        self.assertEqual(status["status"], "ready")
        self.assertEqual(status["origin"], "manual-request")
        self.assertEqual(status["controlEpoch"], 7)
        self.assertEqual(status["size"], 8)
        self.assertEqual(status["sha256"], hashlib.sha256(b"download").hexdigest())
        self.assertTrue(download.deleted)

        failed = Download(self.root / "missing")
        await worker.download(failed, "later-request", 8)
        failure = worker.file_status()["downloads"][1]
        self.assertEqual(failure["status"], "failed")
        self.assertEqual(failure["errorCode"], "browser.actionFailed")
        self.assertNotIn("sha256", failure)

        dots = Download(source, "..")
        await worker.download(dots, None, None)
        self.assertEqual(worker.file_status()["downloads"][2]["filename"], "download")

    async def test_failed_downloads_release_capacity_and_status_stays_bounded(self):
        worker = BrowserWorker(self.root)
        worker.live = self.root
        missing = self.root / "missing"
        for index in range(40):
            await worker.download(Download(missing), str(index), index)

        statuses = worker.file_status()["downloads"]
        self.assertEqual(len(statuses), 32)
        self.assertEqual([item["origin"] for item in statuses], [str(i) for i in range(8, 40)])
        self.assertEqual(worker.total_size(), 0)

        staged = await worker.file(
            BrowserFileArguments(
                browserId=self.browser_id, action="begin", filename="upload", size=1
            )
        )
        self.assertEqual(staged["offset"], 0)
        self.assertEqual(len(worker.files), 32)
        self.assertEqual(len(worker.file_status()["downloads"]), 31)

    async def test_upload_streams_open_export_into_worker_staging(self):
        source = self.root / "upload.bin"
        source.write_bytes(b"x" * 70_000)
        file_id = str(uuid4())
        chunks = []

        async def rpc(op, **values):
            args = values["args"]
            if args["action"] == "begin":
                self.assertEqual(args["filename"], "upload.bin")
                self.assertEqual(args["size"], 70_000)
                return {"fileId": file_id, "offset": 0, "size": 70_000, "complete": False}
            if args["action"] == "write":
                chunk = base64.b64decode(args["data"])
                self.assertEqual(args["offset"], sum(map(len, chunks)))
                chunks.append(chunk)
                offset = sum(map(len, chunks))
                return {"fileId": file_id, "offset": offset, "size": 70_000, "complete": False}
            return {"fileId": file_id, "offset": 70_000, "size": 70_000, "complete": True}

        self.manager.rpc = AsyncMock(side_effect=rpc)
        result = await self.manager.stage_upload(
            BrowserUploadArguments(browserId=self.browser_id, path=str(source))
        )
        self.assertEqual(b"".join(chunks), source.read_bytes())
        self.assertTrue(result["complete"])
        self.assertEqual([len(chunk) for chunk in chunks], [65_536, 4_464])
