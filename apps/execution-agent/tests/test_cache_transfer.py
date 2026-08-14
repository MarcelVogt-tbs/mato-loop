import hashlib
import io
import tempfile
import threading
import time
import unittest
from pathlib import Path

import httpx
from pydantic import ValidationError

from mato_loop_execution_agent.cache import SessionCache
from mato_loop_execution_agent.protocol import ArtifactDescriptor, TransferSpec
from mato_loop_execution_agent.transfer import TransferClient
from mato_loop_execution_agent.workspace import AgentError

BACKEND_URL = "ws://backend/api/execution-agents/connect"
ACCESS_KEY = "durable-access-key"
TICKET_ID = "0a01092c-13d6-49fd-b62c-e2b98deed2bb"


def transfer_client(http: httpx.AsyncClient) -> TransferClient:
    return TransferClient(
        max_bytes=1024,
        backend_url=BACKEND_URL,
        access_key=ACCESS_KEY,
        client=http,
    )


class CacheTransferTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_stream_hook_validates_and_authenticates_content(self) -> None:
        content = b"upload-content"
        received = {}

        def handler(request: httpx.Request) -> httpx.Response:
            received["authorization"] = request.headers["Authorization"]
            received["ticket"] = request.headers["X-Mato-Transfer-Token"]
            received["body"] = request.content
            received["digest"] = request.headers["Digest"]
            return httpx.Response(204)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        transfer = transfer_client(http)
        digest = hashlib.sha256(content).hexdigest()
        await transfer.upload(
            TransferSpec(
                url=f"http://backend/api/workspace/transfers/{TICKET_ID}",
                token="upload-token",
            ),
            io.BytesIO(content),
            size=len(content),
            sha256=digest,
        )
        self.assertEqual(received["authorization"], f"Bearer {ACCESS_KEY}")
        self.assertEqual(received["ticket"], "upload-token")
        self.assertEqual(received["body"], content)
        self.assertTrue(received["digest"].startswith("sha-256="))
        await http.aclose()

    async def test_cache_validates_hits_replaces_corruption_and_evicts(self) -> None:
        content = b"validated-content"
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            self.assertEqual(request.headers["Authorization"], f"Bearer {ACCESS_KEY}")
            self.assertEqual(request.headers["X-Mato-Transfer-Token"], "download-token")
            return httpx.Response(200, content=content)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = SessionCache(root, max_bytes=len(content))
            http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            transfer = transfer_client(http)
            artifact = ArtifactDescriptor.model_validate(
                {
                    "artifactId": "artifact",
                    "backendId": "backend",
                    "sessionId": "session",
                    "path": "generated/model.bin",
                    "filename": "model.bin",
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "contentType": "application/octet-stream",
                }
            )
            spec = TransferSpec(
                url=f"http://backend/api/workspace/transfers/{TICKET_ID}",
                token="download-token",
            )

            self.assertFalse((await cache.ensure(artifact, spec, transfer))["cached"])
            self.assertTrue((await cache.ensure(artifact, spec, transfer))["cached"])
            self.assertEqual(calls, 1)
            cache._path(artifact).write_bytes(b"corrupt")
            self.assertFalse((await cache.ensure(artifact, spec, transfer))["cached"])
            self.assertEqual(calls, 2)

            other = cache.root / "old.bin"
            other.write_bytes(b"too-large")
            cache.evict()
            self.assertLessEqual(
                sum(path.stat().st_size for path in cache.root.rglob("*") if path.is_file()),
                len(content),
            )
            await http.aclose()

    async def test_transfer_uses_paired_origin_and_rejects_invalid_paths(self) -> None:
        requested = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(204)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        transfer = transfer_client(http)
        await transfer.upload(
            TransferSpec(
                url=f"http://127.0.0.1:6020/api/workspace/transfers/{TICKET_ID}",
                token="ticket",
            ),
            io.BytesIO(),
            size=0,
            sha256=hashlib.sha256(b"").hexdigest(),
        )
        self.assertEqual(
            requested,
            [f"http://backend/api/workspace/transfers/{TICKET_ID}"],
        )
        for url in (
            f"http://backend/other/transfers/{TICKET_ID}",
            f"http://backend/api/workspace/transfers/{TICKET_ID}?token=leak",
        ):
            with self.subTest(url=url), self.assertRaises(AgentError):
                await transfer.download(
                    TransferSpec(url=url, token="ticket"),
                    Path("/tmp"),
                    expected_size=0,
                    expected_sha256=hashlib.sha256(b"").hexdigest(),
                )
        await http.aclose()

    async def test_cache_rejects_root_artifacts_and_serializes_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = SessionCache(Path(temporary), max_bytes=1024)
            with self.assertRaises(ValidationError):
                ArtifactDescriptor.model_validate(
                    {
                        "artifactId": "artifact",
                        "backendId": "backend",
                        "sessionId": "session",
                        "path": ".",
                        "filename": "root",
                        "size": 0,
                        "sha256": hashlib.sha256(b"").hexdigest(),
                        "contentType": "application/octet-stream",
                    }
                )

            active = 0
            maximum = 0
            original = cache._evict_unlocked

            def observed_evict() -> int:
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                time.sleep(0.03)
                try:
                    return original()
                finally:
                    active -= 1

            cache._evict_unlocked = observed_evict

            def store(index: int) -> None:
                content = str(index).encode()
                artifact = ArtifactDescriptor.model_validate(
                    {
                        "artifactId": f"artifact-{index}",
                        "backendId": "backend",
                        "sessionId": "session",
                        "path": f"files/{index}",
                        "filename": str(index),
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "contentType": "application/octet-stream",
                    }
                )
                cache.store(artifact, io.BytesIO(content))

            threads = [threading.Thread(target=store, args=(index,)) for index in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(maximum, 1)

    async def test_cache_rejects_symlinked_descriptor_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            cache = SessionCache(base, max_bytes=1024)
            outside = base / "outside"
            outside.mkdir()
            (cache.root / "backend").symlink_to(outside, target_is_directory=True)
            artifact = ArtifactDescriptor.model_validate(
                {
                    "artifactId": "artifact",
                    "backendId": "backend",
                    "sessionId": "session",
                    "path": "files/item",
                    "filename": "item",
                    "size": 1,
                    "sha256": hashlib.sha256(b"x").hexdigest(),
                    "contentType": "application/octet-stream",
                }
            )
            with self.assertRaises(AgentError):
                cache.store(artifact, io.BytesIO(b"x"))
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
