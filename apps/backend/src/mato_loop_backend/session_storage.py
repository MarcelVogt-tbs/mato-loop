from __future__ import annotations

import fcntl
import filecmp
import json
import os
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from mato_loop_backend.config import get_runtime_config

SESSION_DIRECTORIES = ("attachments",)
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


@dataclass(frozen=True, slots=True)
class SessionMoveReceipt:
    session_id: UUID
    source_project_id: UUID | None
    target_project_id: UUID | None
    target: Path
    journal: Path | None = None
    lock_descriptors: tuple[int, ...] = ()
    changed: bool = True


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
            self._prepare_directory(project_root / "files")
            self._prepare_directory(project_root / "revisions")
            self._prepare_directory(sessions)
        else:
            self._validate_directory(self.projects_dir)
            self._validate_directory(project_root)
            self._validate_directory(sessions)
        return sessions

    def logical_root_path(
        self,
        session_id: UUID | str,
        project_id: UUID | str | None,
        *,
        require_exists: bool = False,
    ) -> Path:
        if project_id is None:
            target = self.session_path(
                session_id, project_id=None, require_exists=require_exists
            )
        else:
            target = self.projects_dir / str(self.normalize_project_id(project_id))
        self._validate_directory(target)
        if require_exists:
            for name in ("files", "revisions"):
                directory = target / name
                self._validate_directory(directory)
                if not directory.is_dir():
                    raise SessionStorageError(
                        "sessionStorage.invalidProjectTree",
                        "Incomplete project storage",
                    )
        return target

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
            if project_id is None:
                for name in ("files", "revisions"):
                    self._prepare_directory(target / name)
            return target

        staged = self.staging_dir / f"{normalized}.{uuid4().hex}"
        staged.mkdir(mode=0o700)
        try:
            for relative_path in SESSION_DIRECTORIES:
                (staged / relative_path).mkdir(mode=0o700, parents=True, exist_ok=True)
            if project_id is None:
                (staged / "files").mkdir(mode=0o700)
                (staged / "revisions").mkdir(mode=0o700)
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

    @staticmethod
    def _release_locks(descriptors: tuple[int, ...]) -> None:
        for descriptor in reversed(descriptors):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    @staticmethod
    def _acquire_locks(*paths: Path) -> tuple[int, ...]:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptors: list[int] = []
        try:
            for path in sorted(set(paths), key=str):
                descriptor = os.open(path, flags)
                metadata = os.fstat(descriptor)
                if not stat.S_ISDIR(metadata.st_mode):
                    os.close(descriptor)
                    raise OSError("Storage root is not a directory")
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                descriptors.append(descriptor)
        except BaseException:
            SessionStorage._release_locks(tuple(descriptors))
            raise
        return tuple(descriptors)

    @staticmethod
    def _managed_entries(source: Path, target: Path) -> tuple[list[str], list[str]]:
        files: list[str] = []
        created_directories: list[str] = []
        pending = [(source, target, "")]
        while pending:
            source_directory, target_directory, prefix = pending.pop()
            for entry in source_directory.iterdir():
                relative = f"{prefix}/{entry.name}" if prefix else entry.name
                metadata = entry.lstat()
                destination = target_directory / entry.name
                if stat.S_ISLNK(metadata.st_mode):
                    raise SessionStorageError(
                        "file.symlinkForbidden", "Symlinks are not followed"
                    )
                if stat.S_ISREG(metadata.st_mode):
                    if destination.exists() or destination.is_symlink():
                        raise SessionStorageError(
                            "file.pathAlreadyExists", "Destination path already exists"
                        )
                    files.append(relative)
                    continue
                if not stat.S_ISDIR(metadata.st_mode):
                    raise SessionStorageError(
                        "file.unsupportedType", "Unsupported file type"
                    )
                if destination.exists() or destination.is_symlink():
                    destination_metadata = destination.lstat()
                    if stat.S_ISLNK(destination_metadata.st_mode) or not stat.S_ISDIR(
                        destination_metadata.st_mode
                    ):
                        raise SessionStorageError(
                            "file.pathAlreadyExists", "Destination path already exists"
                        )
                else:
                    created_directories.append(relative)
                pending.append((entry, destination, relative))
        return sorted(files), sorted(created_directories)

    @staticmethod
    def _tree_size(root: Path) -> int:
        size = 0
        pending = [root]
        while pending:
            path = pending.pop()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise SessionStorageError(
                    "file.symlinkForbidden", "Symlinks are not followed"
                )
            if stat.S_ISREG(metadata.st_mode):
                size += metadata.st_size
            elif stat.S_ISDIR(metadata.st_mode):
                pending.extend(path.iterdir())
            else:
                raise SessionStorageError(
                    "file.unsupportedType", "Unsupported file type"
                )
        return size

    @staticmethod
    def _copy_file_no_replace(source: Path, target: Path) -> None:
        created_identity: tuple[int, int] | None = None
        try:
            with source.open("rb") as source_file, target.open("xb") as target_file:
                metadata = os.fstat(target_file.fileno())
                created_identity = (metadata.st_dev, metadata.st_ino)
                shutil.copyfileobj(source_file, target_file)
                target_file.flush()
                os.fsync(target_file.fileno())
            shutil.copystat(source, target, follow_symlinks=False)
        except BaseException:
            if created_identity is not None:
                try:
                    metadata = target.lstat()
                    if (metadata.st_dev, metadata.st_ino) == created_identity:
                        target.unlink()
                except FileNotFoundError:
                    pass
            raise

    @staticmethod
    def _write_move_journal(journal: Path, payload: dict[str, object]) -> None:
        try:
            journal.mkdir(mode=0o700)
            manifest = journal / "move.json"
            staged = journal / "move.json.tmp"
            with staged.open("w", encoding="utf-8") as target:
                target.write(json.dumps(payload, separators=(",", ":")))
                target.flush()
                os.fsync(target.fileno())
            staged.replace(manifest)
            SessionStorage._fsync_directory(journal)
            SessionStorage._fsync_directory(journal.parent)
        except BaseException:
            SessionStorage._remove_path(journal)
            SessionStorage._fsync_directory(journal.parent)
            raise

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(
            directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @classmethod
    def _fsync_imported_tree(
        cls,
        root: Path,
        entries: list[str],
        created_directories: list[str],
    ) -> None:
        directories = {root}
        for relative in entries:
            current = (root / relative).parent
            while current != root:
                directories.add(current)
                current = current.parent
        directories.update(root / relative for relative in created_directories)
        for directory in sorted(
            directories, key=lambda item: (len(item.parts), str(item)), reverse=True
        ):
            cls._fsync_directory(directory)

    @classmethod
    def _fsync_existing_directories(cls, directories: set[Path]) -> None:
        for directory in sorted(
            directories, key=lambda item: (len(item.parts), str(item)), reverse=True
        ):
            if directory.is_dir() and not directory.is_symlink():
                cls._fsync_directory(directory)

    def _move_manifest(self, receipt: SessionMoveReceipt) -> dict[str, object]:
        if receipt.journal is None:
            raise SessionStorageError("sessionStorage.invalidMoveJournal", "Invalid move journal")
        try:
            payload = json.loads((receipt.journal / "move.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SessionStorageError(
                "sessionStorage.invalidMoveJournal", "Invalid move journal"
            ) from exc
        if not isinstance(payload, dict):
            raise SessionStorageError("sessionStorage.invalidMoveJournal", "Invalid move journal")
        if (
            payload.get("sessionId") != str(receipt.session_id)
            or payload.get("sourceProjectId")
            != (
                str(receipt.source_project_id)
                if receipt.source_project_id is not None
                else None
            )
            or payload.get("targetProjectId")
            != (
                str(receipt.target_project_id)
                if receipt.target_project_id is not None
                else None
            )
        ):
            raise SessionStorageError("sessionStorage.invalidMoveJournal", "Invalid move journal")
        return payload

    @staticmethod
    def _journal_relative(value: object) -> str:
        if not isinstance(value, str):
            raise SessionStorageError("sessionStorage.invalidMoveJournal", "Invalid move journal")
        parsed = PurePosixPath(value)
        if not value or parsed.is_absolute() or ".." in parsed.parts or str(parsed) != value:
            raise SessionStorageError("sessionStorage.invalidMoveJournal", "Invalid move journal")
        return value

    def _stage_standalone_import(
        self,
        session_id: UUID,
        target_project_id: UUID,
        *,
        quota_bytes: int | None,
    ) -> SessionMoveReceipt:
        source = self._exact_path(session_id, None)
        self._validate_tree(source)
        self._project_sessions_dir(target_project_id, prepare=True)
        project_root = self.logical_root_path(session_id, target_project_id, require_exists=True)
        target = self._exact_path(session_id, target_project_id)
        descriptors = self._acquire_locks(source, project_root)
        receipt: SessionMoveReceipt | None = None
        try:
            if target.exists() or target.is_symlink():
                raise SessionStorageError(
                    "sessionStorage.sessionAlreadyExists", "Session storage already exists"
                )
            files, file_directories = self._managed_entries(
                source / "files", project_root / "files"
            )
            revisions, revision_directories = self._managed_entries(
                source / "revisions", project_root / "revisions"
            )
            if quota_bytes is not None:
                used = self._tree_size(project_root / "files") + self._tree_size(
                    project_root / "revisions"
                )
                imported = self._tree_size(source / "files") + self._tree_size(
                    source / "revisions"
                )
                if used + imported > quota_bytes:
                    raise SessionStorageError(
                        "file.sessionQuotaExceeded", "Session storage limit has been reached"
                    )
            journal = self.staging_dir / f"move-{session_id}.{uuid4().hex}"
            payload: dict[str, object] = {
                "sessionId": str(session_id),
                "sourceProjectId": None,
                "targetProjectId": str(target_project_id),
                "files": files,
                "fileDirectories": file_directories,
                "revisions": revisions,
                "revisionDirectories": revision_directories,
            }
            self._write_move_journal(journal, payload)
            receipt = SessionMoveReceipt(
                session_id=session_id,
                source_project_id=None,
                target_project_id=target_project_id,
                target=target,
                journal=journal,
                lock_descriptors=descriptors,
            )
            for root_name, directories, entries in (
                ("files", file_directories, files),
                ("revisions", revision_directories, revisions),
            ):
                source_root = source / root_name
                target_root = project_root / root_name
                for relative in sorted(directories, key=lambda item: (item.count("/"), item)):
                    (target_root / relative).mkdir(mode=0o700)
                for relative in entries:
                    self._copy_file_no_replace(source_root / relative, target_root / relative)
                self._fsync_imported_tree(target_root, entries, directories)
            self.provision(session_id, target_project_id)
            self._remove_path(target / "attachments")
            (source / "attachments").rename(target / "attachments")
            self._fsync_directory(target)
            self._fsync_directory(target.parent)
            self._fsync_directory(source)
            return receipt
        except BaseException:
            if receipt is not None:
                self.rollback_session_move(receipt)
            else:
                self._release_locks(descriptors)
            raise

    def stage_session_move(
        self,
        session_id: UUID | str,
        source_project_id: UUID | str | None,
        target_project_id: UUID | str | None,
        *,
        quota_bytes: int | None = None,
    ) -> SessionMoveReceipt:
        normalized = self.normalize_session_id(session_id)
        normalized_source = (
            self.normalize_project_id(source_project_id)
            if source_project_id is not None
            else None
        )
        normalized_target = (
            self.normalize_project_id(target_project_id)
            if target_project_id is not None
            else None
        )
        self.prepare()
        source = self._exact_path(normalized, normalized_source)
        target = self._exact_path(normalized, normalized_target)
        if source == target:
            self._validate_tree(source)
            return SessionMoveReceipt(
                normalized,
                normalized_source,
                normalized_target,
                source,
                changed=False,
            )
        if normalized_source is None and normalized_target is not None:
            return self._stage_standalone_import(
                normalized, normalized_target, quota_bytes=quota_bytes
            )
        self._validate_tree(source)
        if target.exists() or target.is_symlink():
            raise SessionStorageError(
                "sessionStorage.sessionAlreadyExists", "Session storage already exists"
            )
        self.provision(normalized, normalized_target)
        target_attachments = target / "attachments"
        self._remove_path(target_attachments)
        try:
            (source / "attachments").rename(target_attachments)
            if normalized_source is None:
                (source / "files").rmdir()
                (source / "revisions").rmdir()
            source.rmdir()
            self._fsync_directory(target)
            self._fsync_directory(target.parent)
            self._fsync_directory(source.parent)
        except BaseException:
            if target_attachments.exists() and not (source / "attachments").exists():
                target_attachments.rename(source / "attachments")
            self._remove_path(target)
            raise
        return SessionMoveReceipt(
            normalized,
            normalized_source,
            normalized_target,
            target,
        )

    def finalize_session_move(self, receipt: SessionMoveReceipt) -> Path:
        try:
            if receipt.journal is not None:
                source = self._exact_path(receipt.session_id, receipt.source_project_id)
                self._remove_path(source)
                self._fsync_directory(source.parent)
                self._remove_path(receipt.journal)
                self._fsync_directory(receipt.journal.parent)
            return receipt.target
        finally:
            self._release_locks(receipt.lock_descriptors)

    def abandon_session_move(self, receipt: SessionMoveReceipt) -> None:
        self._release_locks(receipt.lock_descriptors)

    def rollback_session_move(self, receipt: SessionMoveReceipt) -> Path:
        if not receipt.changed:
            return self._exact_path(receipt.session_id, receipt.source_project_id)
        if receipt.journal is None:
            rolled_back = self.stage_session_move(
                receipt.session_id,
                receipt.target_project_id,
                receipt.source_project_id,
            )
            return self.finalize_session_move(rolled_back)
        try:
            payload = self._move_manifest(receipt)
            source = self._exact_path(receipt.session_id, receipt.source_project_id)
            target = self._exact_path(receipt.session_id, receipt.target_project_id)
            project_root = self.logical_root_path(
                receipt.session_id, receipt.target_project_id, require_exists=True
            )
            for root_name, entries in (
                ("files", payload.get("files")),
                ("revisions", payload.get("revisions")),
            ):
                if not isinstance(entries, list):
                    raise SessionStorageError(
                        "sessionStorage.invalidMoveJournal", "Invalid move journal"
                    )
                for relative in entries:
                    relative = self._journal_relative(relative)
                    imported = project_root / root_name / relative
                    original = source / root_name / relative
                    if not imported.exists():
                        continue
                    if (
                        imported.is_symlink()
                        or not imported.is_file()
                        or not original.is_file()
                        or not filecmp.cmp(original, imported, shallow=False)
                    ):
                        raise SessionStorageError(
                            "sessionStorage.moveDataChanged",
                            "Imported session data changed before rollback",
                        )
            source_attachments = source / "attachments"
            target_attachments = target / "attachments"
            if target_attachments.exists() and not source_attachments.exists():
                target_attachments.rename(source_attachments)
                self._fsync_directory(source)
                self._fsync_directory(target)
            for root_name, entries, directories in (
                ("files", payload.get("files"), payload.get("fileDirectories")),
                ("revisions", payload.get("revisions"), payload.get("revisionDirectories")),
            ):
                if not isinstance(entries, list) or not isinstance(directories, list):
                    raise SessionStorageError(
                        "sessionStorage.invalidMoveJournal", "Invalid move journal"
                    )
                target_root = project_root / root_name
                touched_directories = {target_root}
                for relative in entries:
                    relative = self._journal_relative(relative)
                    (target_root / relative).unlink(missing_ok=True)
                    parent = (target_root / relative).parent
                    while parent != target_root:
                        touched_directories.add(parent)
                        parent = parent.parent
                for relative in sorted(
                    directories,
                    key=lambda item: (str(item).count("/"), str(item)),
                    reverse=True,
                ):
                    relative = self._journal_relative(relative)
                    try:
                        (target_root / relative).rmdir()
                    except FileNotFoundError:
                        pass
                    touched_directories.add((target_root / relative).parent)
                self._fsync_existing_directories(touched_directories)
            self._remove_path(target)
            self._fsync_directory(target.parent)
            self._remove_path(receipt.journal)
            self._fsync_directory(receipt.journal.parent)
            return source
        finally:
            self._release_locks(receipt.lock_descriptors)

    def move_session(
        self,
        session_id: UUID | str,
        source_project_id: UUID | str | None,
        target_project_id: UUID | str | None,
        *,
        quota_bytes: int | None = None,
    ) -> Path:
        receipt = self.stage_session_move(
            session_id,
            source_project_id,
            target_project_id,
            quota_bytes=quota_bytes,
        )
        return self.finalize_session_move(receipt)

    def remove_project(self, project_id: UUID | str) -> bool:
        trashed = self.trash_project(project_id)
        return self.purge_trashed(trashed)

    def trash_project(self, project_id: UUID | str) -> Path | None:
        normalized = self.normalize_project_id(project_id)
        self.prepare()
        for journal in self.staging_dir.glob("move-*"):
            try:
                payload = json.loads((journal / "move.json").read_text(encoding="utf-8"))
                pending_target = self.normalize_project_id(payload["targetProjectId"])
            except (KeyError, OSError, TypeError, ValueError, SessionStorageError) as exc:
                raise SessionStorageError(
                    "sessionStorage.invalidMoveJournal", "Invalid move journal"
                ) from exc
            if pending_target == normalized:
                raise SessionStorageError(
                    "sessionStorage.projectMovePending",
                    "Project storage has a pending session move",
                )
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
        trashed = self.trash_dir / f"project-{normalized}.{uuid4().hex}"
        project_root.rename(trashed)
        return trashed

    def restore_project(self, project_id: UUID | str, trashed: Path) -> Path:
        normalized = self.normalize_project_id(project_id)
        self.prepare()
        if (
            trashed.parent != self.trash_dir
            or not trashed.name.startswith(f"project-{normalized}.")
        ):
            raise SessionStorageError("sessionStorage.invalidTrashEntry", "Invalid trash entry")
        if trashed.is_symlink() or not trashed.is_dir():
            raise SessionStorageError(
                "sessionStorage.invalidDirectory", "Invalid project storage"
            )
        target = self.projects_dir / str(normalized)
        if target.exists() or target.is_symlink():
            raise SessionStorageError(
                "sessionStorage.projectAlreadyExists", "Project storage already exists"
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
            if entry.name.startswith("move-"):
                continue
            if entry.stat(follow_symlinks=False).st_mtime > cutoff:
                continue
            self._remove_path(entry)
            removed += 1
        return removed

    def _recover_moves(self, expected: dict[UUID, UUID | None]) -> None:
        for journal in sorted(self.staging_dir.glob("move-*"), key=lambda item: item.name):
            try:
                payload = json.loads((journal / "move.json").read_text(encoding="utf-8"))
                session_id = self.normalize_session_id(payload["sessionId"])
                raw_source = payload.get("sourceProjectId")
                raw_target = payload.get("targetProjectId")
                source_project_id = (
                    self.normalize_project_id(raw_source) if raw_source is not None else None
                )
                target_project_id = (
                    self.normalize_project_id(raw_target) if raw_target is not None else None
                )
            except (KeyError, OSError, TypeError, ValueError, SessionStorageError) as exc:
                raise SessionStorageError(
                    "sessionStorage.invalidMoveJournal", "Invalid move journal"
                ) from exc
            if source_project_id is not None or target_project_id is None:
                raise SessionStorageError(
                    "sessionStorage.invalidMoveJournal", "Invalid move journal"
                )
            source = self._exact_path(session_id, source_project_id)
            project_root = self.logical_root_path(
                session_id, target_project_id, require_exists=True
            )
            lock_paths = [project_root]
            if source.exists() and not source.is_symlink():
                lock_paths.append(source)
            receipt = SessionMoveReceipt(
                session_id,
                source_project_id,
                target_project_id,
                self._exact_path(session_id, target_project_id),
                journal=journal,
                lock_descriptors=self._acquire_locks(*lock_paths),
            )
            if expected.get(session_id) == target_project_id:
                self.finalize_session_move(receipt)
            else:
                self.rollback_session_move(receipt)

    def reconcile(
        self,
        sessions: (
            dict[UUID, UUID | None]
            | set[UUID]
            | list[UUID]
            | tuple[UUID, ...]
        ),
        *,
        projects: set[UUID] | list[UUID] | tuple[UUID, ...] = (),
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
        expected_projects = {self.normalize_project_id(item) for item in projects}
        self.prepare()

        for project_id in sorted(expected_projects, key=str):
            target = self.projects_dir / str(project_id)
            if target.exists() or target.is_symlink():
                continue
            candidates = sorted(
                self.trash_dir.glob(f"project-{project_id}.*"),
                key=lambda entry: entry.name,
                reverse=True,
            )
            for candidate in candidates:
                try:
                    self.restore_project(project_id, candidate)
                except SessionStorageError:
                    continue
                break

        self._recover_moves(expected)

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
                source_project_id = (
                    None
                    if located[0].parent == self.sessions_dir
                    else self.normalize_project_id(located[0].parents[1].name)
                )
                self.move_session(session_id, source_project_id, project_id)
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
