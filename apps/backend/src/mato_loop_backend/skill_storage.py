"""Owner-scoped immutable package directories, published by the database registry."""

from __future__ import annotations

import os
import re
import shutil
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.skill_packages import (
    MAX_DIRECTORY_DEPTH,
    MAX_FILE_BYTES,
    MAX_PACKAGE_BYTES,
    MAX_PACKAGE_DIRECTORIES,
    MAX_PACKAGE_FILES,
    SkillError,
    SkillFile,
    SkillPackage,
    skill_path,
)


class SkillStorage:
    def __init__(self, root: Path | None = None):
        self.root = root or get_runtime_config().skills_dir

    @contextmanager
    def directory(self, parts: tuple[str, ...] = (), *, create: bool = False):
        """Walk only real directories; never follow a link in a package path."""
        if create:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = None
        try:
            descriptor = os.open(self.root, flags)
            for part in parts:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                child = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            yield descriptor
        except OSError as exc:
            raise SkillError("skill.unavailable") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def scope(owner_id: UUID | None) -> tuple[str, ...]:
        return ("global",) if owner_id is None else ("users", str(owner_id))

    def publish(self, owner_id: UUID | None, skill_id: UUID, package: SkillPackage) -> str:
        revision = package.revision
        name = package.name
        parent = (*self.scope(owner_id), str(skill_id))
        temporary = f".staging-{uuid4()}"
        with self.directory(parent, create=True) as descriptor:
            try:
                os.stat(revision, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if self.read(owner_id, skill_id, revision, name).revision != revision:
                    raise SkillError("skill.changed")
                return revision
            try:
                for path, entry in package.files.items():
                    parts = tuple(path.split("/"))
                    with self.directory((*parent, temporary, name, *parts[:-1]), create=True) as target:
                        fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700 if entry.executable else 0o600, dir_fd=target)
                        with os.fdopen(fd, "wb") as handle:
                            handle.write(entry.content)
                            handle.flush()
                            os.fsync(handle.fileno())
                os.rename(temporary, revision, src_dir_fd=descriptor, dst_dir_fd=descriptor)
                os.fsync(descriptor)
            finally:
                # This private staging path is never exposed to model-facing file tools.
                shutil.rmtree(self.root.joinpath(*parent, temporary), ignore_errors=True)
        return revision

    def read(self, owner_id: UUID | None, skill_id: UUID, revision: str, name: str) -> SkillPackage:
        if not re.fullmatch(r"[a-f0-9]{64}", revision) or "/" in skill_path(name):
            raise SkillError("skill.draftInvalid")
        files: dict[str, SkillFile] = {}
        total = 0
        directories = 0

        def walk(descriptor: int, prefix: str = "") -> None:
            nonlocal total, directories
            directories += 1
            if directories > MAX_PACKAGE_DIRECTORIES or prefix.count("/") > MAX_DIRECTORY_DEPTH:
                raise SkillError("file.tooLarge")
            for filename in sorted(os.listdir(descriptor)):
                path = skill_path(prefix + filename)
                metadata = os.stat(filename, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISDIR(metadata.st_mode):
                    child = os.open(filename, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                    try:
                        walk(child, path + "/")
                    finally:
                        os.close(child)
                elif stat.S_ISREG(metadata.st_mode):
                    if len(files) >= MAX_PACKAGE_FILES or metadata.st_size > MAX_FILE_BYTES:
                        raise SkillError("file.tooLarge")
                    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
                    with os.fdopen(fd, "rb") as handle:
                        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                            raise SkillError("skill.draftInvalid")
                        content = handle.read(min(MAX_FILE_BYTES, MAX_PACKAGE_BYTES - total) + 1)
                    total += len(content)
                    if total > MAX_PACKAGE_BYTES:
                        raise SkillError("file.tooLarge")
                    files[path] = SkillFile(content, bool(metadata.st_mode & 0o111))
                else:
                    raise SkillError("file.unsupportedType")

        with self.directory((*self.scope(owner_id), str(skill_id), revision, name)) as descriptor:
            walk(descriptor)
        package = SkillPackage(files)
        if package.name != name or package.revision != revision:
            raise SkillError("skill.changed")
        return package

    def reconcile(self, live: set[tuple[UUID | None, UUID, str]]) -> None:
        """Reclaim abandoned publications on startup, with a day for concurrent writers."""
        scopes = [(None, ("global",))]
        with self.directory(create=True):
            pass
        users = self.root / "users"
        if users.exists():
            with self.directory(("users",)) as descriptor:
                for name in os.listdir(descriptor):
                    try:
                        owner = UUID(name)
                    except ValueError:
                        continue
                    scopes.append((owner, ("users", str(owner))))
        for owner, scope in scopes:
            with self.directory(scope, create=True) as descriptor:
                for name in os.listdir(descriptor):
                    try:
                        skill_id = UUID(name)
                    except ValueError:
                        continue
                    with self.directory((*scope, name)) as skill_dir:
                        for revision in os.listdir(skill_dir):
                            metadata = os.stat(revision, dir_fd=skill_dir, follow_symlinks=False)
                            if (owner, skill_id, revision) in live or time.time() - metadata.st_mtime < 86400:
                                continue
                            if stat.S_ISDIR(metadata.st_mode):
                                shutil.rmtree(self.root.joinpath(*scope, name, revision))
                            else:
                                os.unlink(revision, dir_fd=skill_dir)
