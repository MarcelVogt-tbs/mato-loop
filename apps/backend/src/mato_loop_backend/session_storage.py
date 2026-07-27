from __future__ import annotations

import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from mato_loop_backend.config import get_runtime_config

SESSION_DIRECTORIES = (
    "files",
    "attachments",
    "generated",
    "downloads",
    "derived",
)


class SessionStorageError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    provisioned: tuple[UUID, ...]
    restored: tuple[UUID, ...]
    trashed: tuple[UUID, ...]
    removed_staging_entries: int
    removed_trash_entries: int


class SessionStorage:
    """Manage the filesystem lifecycle of session directories."""

    def __init__(self, sessions_dir: Path | None = None) -> None:
        self.sessions_dir = (sessions_dir or get_runtime_config().sessions_dir).absolute()
        self.staging_dir = self.sessions_dir / ".staging"
        self.trash_dir = self.sessions_dir / ".trash"

    @staticmethod
    def normalize_session_id(session_id: UUID | str) -> UUID:
        if isinstance(session_id, UUID):
            return session_id
        try:
            normalized = UUID(session_id)
        except (AttributeError, TypeError, ValueError) as exc:
            raise SessionStorageError(
                "sessionStorage.invalidSessionId", "Invalid session ID"
            ) from exc
        if str(normalized) != session_id:
            raise SessionStorageError(
                "sessionStorage.invalidSessionId", "Invalid session ID"
            )
        return normalized

    @staticmethod
    def _prepare_directory(directory: Path) -> None:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            metadata = directory.lstat()
        except OSError as exc:
            raise SessionStorageError(
                "sessionStorage.invalidDirectory", "Invalid storage directory"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise SessionStorageError(
                "sessionStorage.invalidDirectory", "Invalid storage directory"
            )
        directory.chmod(0o700)

    def prepare(self) -> None:
        self._prepare_directory(self.sessions_dir)
        self._prepare_directory(self.staging_dir)
        self._prepare_directory(self.trash_dir)

    def session_path(self, session_id: UUID | str, *, require_exists: bool = False) -> Path:
        normalized = self.normalize_session_id(session_id)
        target = self.sessions_dir / str(normalized)
        if require_exists:
            self._validate_tree(target)
        return target

    def _validate_tree(self, target: Path) -> None:
        try:
            target_metadata = target.lstat()
        except FileNotFoundError as exc:
            raise SessionStorageError(
                "sessionStorage.sessionNotFound", "Session storage not found"
            ) from exc
        if stat.S_ISLNK(target_metadata.st_mode) or not stat.S_ISDIR(target_metadata.st_mode):
            raise SessionStorageError(
                "sessionStorage.invalidSessionTree", "Invalid session storage"
            )
        target.chmod(0o700)
        for relative_path in SESSION_DIRECTORIES:
            directory = target / relative_path
            try:
                metadata = directory.lstat()
            except FileNotFoundError as exc:
                raise SessionStorageError(
                    "sessionStorage.invalidSessionTree", "Incomplete session storage"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise SessionStorageError(
                    "sessionStorage.invalidSessionTree", "Invalid session storage"
                )
            directory.chmod(0o700)

    def provision(self, session_id: UUID | str) -> Path:
        normalized = self.normalize_session_id(session_id)
        self.prepare()
        target = self.sessions_dir / str(normalized)
        if target.exists() or target.is_symlink():
            self._validate_tree(target)
            return target

        staged = self.staging_dir / f"{normalized}.{uuid4().hex}"
        staged.mkdir(mode=0o700)
        try:
            for relative_path in SESSION_DIRECTORIES:
                (staged / relative_path).mkdir(mode=0o700, parents=True, exist_ok=True)
            self._validate_tree(staged)
            try:
                staged.rename(target)
            except FileExistsError:
                self._validate_tree(target)
            return target
        finally:
            if staged.exists() or staged.is_symlink():
                self._remove_path(staged)

    @staticmethod
    def _remove_path(target: Path) -> None:
        if target.is_symlink() or target.is_file():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            shutil.rmtree(target)

    def remove_session(self, session_id: UUID | str) -> bool:
        target = self.session_path(session_id)
        if not target.exists() and not target.is_symlink():
            return False
        self._remove_path(target)
        return True

    def trash_session(self, session_id: UUID | str) -> Path | None:
        normalized = self.normalize_session_id(session_id)
        self.prepare()
        target = self.sessions_dir / str(normalized)
        if not target.exists() and not target.is_symlink():
            return None
        self._validate_tree(target)
        trashed = self.trash_dir / f"{normalized}.{uuid4().hex}"
        target.rename(trashed)
        return trashed

    def restore_session(self, session_id: UUID | str, trashed: Path) -> Path:
        normalized = self.normalize_session_id(session_id)
        self.prepare()
        if trashed.parent != self.trash_dir or not trashed.name.startswith(f"{normalized}."):
            raise SessionStorageError("sessionStorage.invalidTrashEntry", "Invalid trash entry")
        self._validate_tree(trashed)
        target = self.sessions_dir / str(normalized)
        if target.exists() or target.is_symlink():
            raise SessionStorageError(
                "sessionStorage.sessionAlreadyExists", "Session storage already exists"
            )
        trashed.rename(target)
        return target

    def purge_trashed(self, trashed: Path | None) -> bool:
        if trashed is None:
            return False
        if trashed.parent != self.trash_dir:
            raise SessionStorageError("sessionStorage.invalidTrashEntry", "Invalid trash entry")
        if not trashed.exists() and not trashed.is_symlink():
            return False
        self._remove_path(trashed)
        return True

    def purge_trash(self, *, older_than_seconds: float = 0) -> int:
        if older_than_seconds < 0:
            raise ValueError("older_than_seconds must not be negative")
        self.prepare()
        cutoff = time.time() - older_than_seconds
        removed = 0
        for entry in self.trash_dir.iterdir():
            if entry.stat(follow_symlinks=False).st_mtime > cutoff:
                continue
            self._remove_path(entry)
            removed += 1
        return removed

    def _remove_stale_staging(self, stale_after_seconds: float) -> int:
        cutoff = time.time() - stale_after_seconds
        removed = 0
        for entry in self.staging_dir.iterdir():
            if entry.stat(follow_symlinks=False).st_mtime > cutoff:
                continue
            self._remove_path(entry)
            removed += 1
        return removed

    def reconcile(
        self,
        session_ids: set[UUID] | list[UUID] | tuple[UUID, ...],
        *,
        stale_staging_after_seconds: float = 3_600,
    ) -> ReconciliationResult:
        if stale_staging_after_seconds < 0:
            raise ValueError("stale_staging_after_seconds must not be negative")
        normalized_ids = {self.normalize_session_id(item) for item in session_ids}
        self.prepare()

        provisioned: list[UUID] = []
        restored: list[UUID] = []
        for session_id in sorted(normalized_ids, key=str):
            target = self.session_path(session_id)
            if not target.exists() and not target.is_symlink():
                trash_candidates = sorted(
                    (
                        entry
                        for entry in self.trash_dir.iterdir()
                        if entry.name.startswith(f"{session_id}.")
                    ),
                    key=lambda entry: entry.name,
                    reverse=True,
                )
                recovered = False
                for candidate in trash_candidates:
                    try:
                        self.restore_session(session_id, candidate)
                    except SessionStorageError:
                        continue
                    restored.append(session_id)
                    recovered = True
                    break
                if not recovered:
                    self.provision(session_id)
                    provisioned.append(session_id)
            else:
                self._validate_tree(target)

        trashed: list[UUID] = []
        for entry in self.sessions_dir.iterdir():
            if entry.name in {self.staging_dir.name, self.trash_dir.name}:
                continue
            try:
                session_id = self.normalize_session_id(entry.name)
            except SessionStorageError:
                continue
            if session_id not in normalized_ids:
                self.trash_session(session_id)
                trashed.append(session_id)

        return ReconciliationResult(
            provisioned=tuple(provisioned),
            restored=tuple(restored),
            trashed=tuple(sorted(trashed, key=str)),
            removed_staging_entries=self._remove_stale_staging(stale_staging_after_seconds),
            removed_trash_entries=self.purge_trash(),
        )
