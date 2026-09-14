import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

from matoloop_agent.browser_contracts import (
    BrowserFileArguments,
    BrowserPublishArguments,
    BrowserUploadArguments,
)
from matoloop_agent.browser_worker import BrowserWorker
from matoloop_agent.config import AgentConfig
from matoloop_agent.connection import MatoLoopAgent
from matoloop_agent.filesystem import AgentError


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
        self.agent = MatoLoopAgent(config, "test")
        self.manager = self.agent.browser
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

    async def test_publish_checks_content_destination_origin_and_is_idempotent(self):
        data = b"persistent download"
        file_id = str(uuid4())
        digest = hashlib.sha256(data).hexdigest()
        destination = self.root / "browser" / "downloads" / "result.bin"
        calls = []

        async def rpc(op, **values):
            calls.append((op, values))
            if op == "files":
                return {
                    "downloads": [
                        {
                            "id": file_id,
                            "filename": "result.bin",
                            "status": "ready",
                            "origin": "request-1",
                            "controlEpoch": 4,
                            "size": len(data),
                            "sha256": digest,
                        }
                    ]
                }
            if values["args"]["action"] == "read":
                return {
                    "fileId": file_id,
                    "filename": "result.bin",
                    "size": len(data),
                    "offset": len(data),
                    "complete": True,
                    "data": base64.b64encode(data).decode(),
                }
            self.assertIn(file_id, self.manager.publish_receipts)
            return {"ok": True}

        self.manager.rpc = AsyncMock(side_effect=rpc)
        args = BrowserPublishArguments(
            browserId=self.browser_id,
            fileId=file_id,
            path=str(destination),
            expectedOrigin="request-1",
        )
        result = await self.manager.publish(args)
        self.assertEqual(destination.read_bytes(), data)
        self.assertEqual(result, {"path": str(destination), "size": len(data), "sha256": digest})
        self.assertEqual(await self.manager.publish(args), result)
        self.assertEqual(len(calls), 3)

        with self.assertRaises(AgentError):
            await self.manager.publish(args.model_copy(update={"path": str(self.root / "other")}))

        other_id = str(uuid4())
        self.manager.publish_receipts.clear()
        with self.assertRaises(AgentError) as error:
            await self.manager.publish(
                args.model_copy(update={"file_id": other_id, "path": str(self.root / "outside")})
            )
        self.assertEqual(error.exception.code, "browser.invalidTarget")

    async def test_publish_existing_destination_preserves_collision_code(self):
        data = b"new"
        file_id = str(uuid4())
        digest = hashlib.sha256(data).hexdigest()
        destination = self.root / "browser" / "downloads" / "result.bin"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"existing")

        async def rpc(op, **values):
            if op == "files":
                return {
                    "downloads": [
                        {
                            "id": file_id,
                            "filename": destination.name,
                            "status": "ready",
                            "origin": "request-1",
                            "controlEpoch": 1,
                            "size": len(data),
                            "sha256": digest,
                        }
                    ]
                }
            return {
                "fileId": file_id,
                "filename": destination.name,
                "size": len(data),
                "offset": len(data),
                "complete": True,
                "data": base64.b64encode(data).decode(),
            }

        self.manager.rpc = AsyncMock(side_effect=rpc)
        with self.assertRaises(AgentError) as error:
            await self.manager.dispatch(
                "integration.browser.publish",
                BrowserPublishArguments(
                    browserId=self.browser_id,
                    fileId=file_id,
                    path=str(destination),
                    expectedOrigin="request-1",
                ),
            )
        self.assertEqual(error.exception.code, "path.exists")
        self.assertEqual(destination.read_bytes(), b"existing")

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
