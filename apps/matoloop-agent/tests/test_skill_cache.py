import hashlib
import io
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx

from matoloop_agent.cache import SessionCache
from matoloop_agent.errors import AgentError
from matoloop_agent.protocol import SkillCachePrepareArguments
from matoloop_agent.skill_cache import SkillCache
from matoloop_agent.transfer import TransferClient


class SkillCacheTests(unittest.IsolatedAsyncioTestCase):
    def test_publication_waits_for_an_in_progress_cache_clear(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as source:
                source.writestr("demo/SKILL.md", b"instructions")
            cache = SessionCache(root, 4096)
            skills = SkillCache(cache)
            clearing, resume, published = threading.Event(), threading.Event(), threading.Event()
            clear_fd, evict = cache._clear_fd, cache.evict

            def pause_clear(fd):
                clearing.set()
                if not resume.wait(5):
                    raise TimeoutError("Test did not release cache clearing")
                return clear_fd(fd)

            def after_publication():
                published.set()
                return evict()

            with (
                patch.object(cache, "_clear_fd", pause_clear),
                patch.object(cache, "evict", after_publication),
                ThreadPoolExecutor(max_workers=2) as workers,
            ):
                clear = workers.submit(cache.clear)
                try:
                    self.assertTrue(clearing.wait(5))
                    publish = workers.submit(
                        skills._publish, archive, ("backend", "session", "run", "revision"), "demo"
                    )
                    # The unfixed publisher reaches eviction before the blocked clear resumes.
                    published.wait(1)
                finally:
                    resume.set()
                clear.result(timeout=5)
                path = publish.result(timeout=5)
            self.assertEqual((path / "SKILL.md").read_bytes(), b"instructions")

    async def test_package_runs_with_relative_assets_and_survives_eviction_until_release(self):
        source = io.BytesIO()
        with zipfile.ZipFile(source, "w") as archive:
            for path, content in {
                "SKILL.md": (
                    b"---\nname: demo\ndescription: Demonstrate resources.\n---\n"
                    b"Run scripts/run.py."
                ),
                "scripts/run.py": (
                    b"from pathlib import Path\nimport sys\n"
                    b"source = Path(__file__).parent.parent / 'assets/input.bin'\n"
                    b"Path(sys.argv[1]).write_bytes(source.read_bytes())\n"
                ),
                "assets/input.bin": b"\x00\xffresource",
            }.items():
                info = zipfile.ZipInfo("demo/" + path)
                info.external_attr = (stat.S_IFREG | 0o755) << 16
                archive.writestr(info, content)
        content = source.getvalue()
        arguments = SkillCachePrepareArguments(
            runId=str(uuid4()),
            sessionId=str(uuid4()),
            name="demo",
            revision="a" * 64,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            transfer={
                "url": f"http://backend/api/workspace/transfers/{uuid4()}",
                "token": "ticket",
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = SessionCache(root, max_bytes=4096)
            skills = SkillCache(cache)
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))
            ) as http:
                transfer = TransferClient(
                    max_bytes=4096,
                    backend_url="ws://backend/api/execution-agents/connect",
                    access_key="key",
                    client=http,
                )
                result = await skills.prepare(arguments, "backend", transfer)
                path = Path(result["path"])
                cache.max_bytes = 0
                cache.evict()
                subprocess.run(
                    [sys.executable, str(path / "scripts/run.py"), str(root / "output.bin")],
                    check=True,
                    cwd=root,
                )
                self.assertEqual((root / "output.bin").read_bytes(), b"\x00\xffresource")
                with self.assertRaises(AgentError):
                    cache.clear()
                await skills.release("backend", arguments.session_id, arguments.run_id)
                self.assertFalse((path / "scripts/run.py").exists())

    async def test_prepare_rejects_archive_escape_and_links(self):
        for member, mode in (("demo/../../outside", stat.S_IFREG), ("demo/link", stat.S_IFLNK)):
            with self.subTest(member=member), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / "source.zip"
                with zipfile.ZipFile(archive, "w") as source:
                    info = zipfile.ZipInfo(member)
                    info.external_attr = mode << 16
                    source.writestr(info, b"outside")
                skills = SkillCache(SessionCache(root, 4096))
                with self.assertRaises(AgentError):
                    skills._publish(archive, ("backend", "session", "run", "revision"), "demo")
                self.assertFalse((root / "outside").exists())
