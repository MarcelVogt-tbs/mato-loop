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
_AUTO_LOCATION = object()


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

    def __init__(
        self,
        sessions_dir: Path | None = None,
        projects_dir: Path | None = None,
    ) -> None:
        config = get_runtime_config()
        self.sessions_dir = (sessions_dir or config.sessions_dir).absolute()
        self.projects_dir = (
            projects_dir
            or (self.sessions_dir.parent / "projects" if sessions_dir else config.projects_dir)
        ).absolute()
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
    def normalize_project_id(project_id: UUID | str) -> UUID:
        try:
            normalized = project_id if isinstance(project_id, UUID) else UUID(project_id)
        except (AttributeError, TypeError, ValueError) as exc:
            raise SessionStorageError(
                "sessionStorage.invalidProjectId", "Invalid project ID"
            ) from exc
        if isinstance(project_id, str) and str(normalized) != project_id:
            raise SessionStorageError(
                "sessionStorage.invalidProjectId", "Invalid project ID"
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
        self._prepare_directory(self.projects_dir)
        self._prepare_directory(self.staging_dir)
        self._prepare_directory(self.trash_dir)

    @staticmethod
    def _validate_directory(directory: Path) -> None:
        if not directory.exists() and not directory.is_symlink():
            return
        metadata = directory.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise SessionStorageError(
                "sessionStorage.invalidDirectory", "Invalid storage directory"
            )

    def _project_sessions_dir(
        self, project_id: UUID | str, *, prepare: bool = False
    ) -> Path:
        normalized = self.normalize_project_id(project_id)
        project_root = self.projects_dir / str(normalized)
        sessions = project_root / "sessions"
        if prepare:
            self._prepare_directory(self.projects_dir)
            self._prepare_directory(project_root)
            self._prepare_directory(sessions)
        else:
            self._validate_directory(self.projects_dir)
            self._validate_directory(project_root)
            self._validate_directory(sessions)
        return sessions

    def _exact_path(
        self, session_id: UUID | str, project_id: UUID | str | None
    ) -> Path:
        normalized = self.normalize_session_id(session_id)
        if project_id is None:
            return self.sessions_dir / str(normalized)
        return self._project_sessions_dir(project_id) / str(normalized)

    def _located_paths(self, session_id: UUID | str) -> list[Path]:
        normalized = self.normalize_session_id(session_id)
        paths = []
        standalone = self.sessions_dir / str(normalized)
        if standalone.exists() or standalone.is_symlink():
            paths.append(standalone)
        self._validate_directory(self.projects_dir)
        if self.projects_dir.exists():
            for project_root in self.projects_dir.iterdir():
                if project_root.is_symlink() or not project_root.is_dir():
                    continue
                try:
                    self.normalize_project_id(project_root.name)
                except SessionStorageError:
                    continue
                candidate = project_root / "sessions" / str(normalized)
                if candidate.exists() or candidate.is_symlink():
                    paths.append(candidate)
        return paths

    def session_path(
        self,
        session_id: UUID | str,
        *,
        project_id: UUID | str | None | object = _AUTO_LOCATION,
        require_exists: bool = False,
    ) -> Path:
        if project_id is _AUTO_LOCATION:
            located = self._located_paths(session_id)
            if len(located) > 1:
                raise SessionStorageError(
                    "sessionStorage.duplicateSessionTree", "Duplicate session storage"
                )
            target = located[0] if located else self._exact_path(session_id, None)
        else:
            target = self._exact_path(session_id, project_id)
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

    def provision(
        self, session_id: UUID | str, project_id: UUID | str | None = None
    ) -> Path:
        normalized = self.normalize_session_id(session_id)
        self.prepare()
        if project_id is not None:
            self._project_sessions_dir(project_id, prepare=True)
        target = self._exact_path(normalized, project_id)
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

    def remove_session(
        self,
        session_id: UUID | str,
        project_id: UUID | str | None | object = _AUTO_LOCATION,
    ) -> bool:
        target = self.session_path(session_id, project_id=project_id)
        if not target.exists() and not target.is_symlink():
            return False
        self._remove_path(target)
        return True

    def trash_session(
        self,
        session_id: UUID | str,
        project_id: UUID | str | None | object = _AUTO_LOCATION,
    ) -> Path | None:
        normalized = self.normalize_session_id(session_id)
        self.prepare()
        target = self.session_path(normalized, project_id=project_id)
        if not target.exists() and not target.is_symlink():
            return None
        self._validate_tree(target)
        trashed = self.trash_dir / f"{normalized}.{uuid4().hex}"
        target.rename(trashed)
        return trashed

    def restore_session(
        self,
        session_id: UUID | str,
        trashed: Path,
        project_id: UUID | str | None = None,
    ) -> Path:
        normalized = self.normalize_session_id(session_id)
        self.prepare()
        if trashed.parent != self.trash_dir or not trashed.name.startswith(f"{normalized}."):
            raise SessionStorageError("sessionStorage.invalidTrashEntry", "Invalid trash entry")
        self._validate_tree(trashed)
        if project_id is not None:
            self._project_sessions_dir(project_id, prepare=True)
        target = self._exact_path(normalized, project_id)
        if target.exists() or target.is_symlink():
            raise SessionStorageError(
                "sessionStorage.sessionAlreadyExists", "Session storage already exists"
            )
        trashed.rename(target)
        return target

    def move_session(
        self,
        session_id: UUID | str,
        source_project_id: UUID | str | None,
        target_project_id: UUID | str | None,
    ) -> Path:
        normalized = self.normalize_session_id(session_id)
        self.prepare()
        source = self._exact_path(normalized, source_project_id)
        target = self._exact_path(normalized, target_project_id)
        if source == target:
            self._validate_tree(source)
            return source
        self._validate_tree(source)
        if target.exists() or target.is_symlink():
            raise SessionStorageError(
                "sessionStorage.sessionAlreadyExists", "Session storage already exists"
            )
        if target_project_id is not None:
            self._project_sessions_dir(target_project_id, prepare=True)
        source.rename(target)
        return target

    def remove_project(self, project_id: UUID | str) -> bool:
        normalized = self.normalize_project_id(project_id)
        project_root = self.projects_dir / str(normalized)
        if not project_root.exists() and not project_root.is_symlink():
            return False
        if project_root.is_symlink() or not project_root.is_dir():
            raise SessionStorageError(
                "sessionStorage.invalidDirectory", "Invalid project storage"
            )
        sessions = project_root / "sessions"
        self._validate_directory(sessions)
        if sessions.exists() and any(sessions.iterdir()):
            raise SessionStorageError(
                "sessionStorage.projectNotEmpty", "Project storage is not empty"
            )
        self._remove_path(project_root)
        return True

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
        sessions: (
            dict[UUID, UUID | None]
            | set[UUID]
            | list[UUID]
            | tuple[UUID, ...]
        ),
        *,
        stale_staging_after_seconds: float = 3_600,
    ) -> ReconciliationResult:
        if stale_staging_after_seconds < 0:
            raise ValueError("stale_staging_after_seconds must not be negative")
        if isinstance(sessions, dict):
            expected = {
                self.normalize_session_id(session_id): (
                    self.normalize_project_id(project_id)
                    if project_id is not None
                    else None
                )
                for session_id, project_id in sessions.items()
            }
        else:
            expected = {self.normalize_session_id(item): None for item in sessions}
        self.prepare()

        provisioned: list[UUID] = []
        restored: list[UUID] = []
        for session_id, project_id in sorted(expected.items(), key=lambda item: str(item[0])):
            target = self._exact_path(session_id, project_id)
            located = self._located_paths(session_id)
            if len(located) > 1:
                raise SessionStorageError(
                    "sessionStorage.duplicateSessionTree", "Duplicate session storage"
                )
            if located and located[0] != target:
                if project_id is not None:
                    self._project_sessions_dir(project_id, prepare=True)
                located[0].rename(target)
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
                        self.restore_session(session_id, candidate, project_id)
                    except SessionStorageError:
                        continue
                    restored.append(session_id)
                    recovered = True
                    break
                if not recovered:
                    self.provision(session_id, project_id)
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
            if session_id not in expected:
                self.trash_session(session_id, None)
                trashed.append(session_id)

        for project_root in self.projects_dir.iterdir():
            if project_root.is_symlink() or not project_root.is_dir():
                continue
            try:
                project_id = self.normalize_project_id(project_root.name)
            except SessionStorageError:
                continue
            project_sessions = project_root / "sessions"
            if not project_sessions.is_dir() or project_sessions.is_symlink():
                continue
            for entry in list(project_sessions.iterdir()):
                try:
                    session_id = self.normalize_session_id(entry.name)
                except SessionStorageError:
                    continue
                if expected.get(session_id, _AUTO_LOCATION) != project_id:
                    self.trash_session(session_id, project_id)
                    trashed.append(session_id)

        return ReconciliationResult(
            provisioned=tuple(provisioned),
            restored=tuple(restored),
            trashed=tuple(sorted(trashed, key=str)),
            removed_staging_entries=self._remove_stale_staging(stale_staging_after_seconds),
            removed_trash_entries=self.purge_trash(),
        )
