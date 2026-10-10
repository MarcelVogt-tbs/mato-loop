"""Run-leased skill directories in the existing session cache."""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .errors import AgentError

MAX_BYTES = 8 * 1024 * 1024
MAX_FILES = 256


class SkillCache:
    def __init__(self, cache):
        self.cache = cache
        self.prepared: dict[tuple[str, str, str, str], tuple[Path, int]] = {}
        self.lock = asyncio.Lock()
        # Both platform caches use this set when choosing eviction candidates.
        self.cache.protected_paths = set()

    async def prepare(self, arguments, backend_id: str, transfer) -> dict:
        key = (backend_id, arguments.session_id, arguments.run_id, arguments.revision)
        async with self.lock:
            if key in self.prepared:
                return {"path": self.prepared[key][0].as_posix()}
            archive = await transfer.download(
                arguments.transfer,
                self.cache.staging,
                expected_size=arguments.size,
                expected_sha256=arguments.sha256,
            )
            try:
                # The caller waits for publication even when its transport request is cancelled.
                task = asyncio.create_task(
                    asyncio.to_thread(self._publish, archive, key, arguments.name)
                )
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                return {"path": task.result().as_posix()}
            finally:
                archive.unlink(missing_ok=True)

    def _publish(self, archive: Path, key: tuple[str, str, str, str], name: str) -> Path:
        with self.cache.lock:
            return self._publish_locked(archive, key, name)

    def _publish_locked(self, archive: Path, key: tuple[str, str, str, str], name: str) -> Path:
        # A fresh random private directory avoids replacing any exposed working copy.
        temporary = Path(tempfile.mkdtemp(prefix=".skill-", dir=self.cache.root))
        self.cache.protected_paths.add(temporary)
        success = False
        try:
            total = 0
            seen: set[str] = set()
            with zipfile.ZipFile(archive) as source:
                entries = source.infolist()
                if len(entries) > MAX_FILES:
                    raise AgentError("operation.tooLarge", "Skill contains too many files")
                for entry in entries:
                    path = PurePosixPath(entry.filename)
                    parts = entry.filename.split("/")
                    mode = entry.external_attr >> 16
                    if (
                        path.is_absolute()
                        or path.as_posix() != entry.filename
                        or len(parts) < 2
                        or parts[0] != name
                        or any(
                            part in {"", ".", ".."} or part.endswith((".", " ")) for part in parts
                        )
                        or any(char in entry.filename for char in '\\:<>"|?*')
                        or any(ord(char) < 32 for char in entry.filename)
                        or entry.filename.casefold() in seen
                        or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
                        or entry.flag_bits & 1
                    ):
                        raise AgentError("cache.invalidContent", "Invalid skill archive path")
                    seen.add(entry.filename.casefold())
                    target = temporary.joinpath(*parts)
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with source.open(entry) as incoming, target.open("xb") as outgoing:
                        while chunk := incoming.read(min(64 * 1024, MAX_BYTES - total + 1)):
                            total += len(chunk)
                            if total > MAX_BYTES:
                                raise AgentError(
                                    "operation.tooLarge", "Skill package exceeds its limit"
                                )
                            outgoing.write(chunk)
                    if os.name != "nt":
                        target.chmod(0o700 if mode & 0o111 else 0o600)
            if f"{name}/skill.md" not in seen:
                raise AgentError("cache.invalidContent", "Skill definition is missing")
            if sum(size for _, size in self.prepared.values()) + total > self.cache.max_bytes:
                raise AgentError(
                    "operation.tooLarge", "Active skills exceed the session cache limit"
                )
            self.prepared[key] = (temporary / name, total)
            self.cache.evict()
            success = True
            return temporary / name
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
            raise AgentError("cache.invalidContent", "Skill package could not be prepared") from exc
        finally:
            if not success:
                self.prepared.pop(key, None)
                self.cache.protected_paths.discard(temporary)
                shutil.rmtree(temporary, ignore_errors=True)

    async def release(self, backend_id: str, session_id: str, run_id: str) -> dict:
        async with self.lock:
            await asyncio.to_thread(self._release, backend_id, session_id, run_id)
        return {}

    def _release(self, backend_id: str, session_id: str, run_id: str) -> None:
        with self.cache.lock:
            for key in list(self.prepared):
                if key[:3] == (backend_id, session_id, run_id):
                    path, _ = self.prepared.pop(key)
                    self.cache.protected_paths.discard(path.parent)
            self.cache.evict()

    def disconnect(self) -> None:
        with self.cache.lock:
            self.prepared.clear()
            self.cache.protected_paths.clear()
