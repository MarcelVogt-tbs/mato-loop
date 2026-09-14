"""move project files to sessions

Revision ID: db0ed8f44f4e
Revises: fcb30fdb20f7
Create Date: 2026-09-14 18:43:53.110243
"""

import json
import os
import stat
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "db0ed8f44f4e"
down_revision: Union[str, None] = "fcb30fdb20f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IMPORT_MARKER = ".legacy-project-import.json"


def _present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _validate_tree(path: Path) -> None:
    if not _present(path):
        return
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"Legacy project storage root is not a directory: {path}")
    pending = list(path.iterdir())
    while pending:
        current = pending.pop()
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError(f"Legacy project storage contains a symlink: {current}")
        if stat.S_ISDIR(metadata.st_mode):
            pending.extend(current.iterdir())
        elif not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(f"Legacy project storage contains an unsupported entry: {current}")


def _reject_pending_runs(revisions: Path) -> None:
    if not revisions.is_dir():
        return
    for history in revisions.iterdir():
        index_path = history / "index.json"
        if not index_path.is_file():
            continue
        try:
            owner = json.loads(index_path.read_text(encoding="utf-8")).get("run")
        except (AttributeError, json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"Invalid managed revision index: {index_path}") from exc
        if owner is None:
            continue
        if not isinstance(owner, dict) or owner.get("state") not in {
            "pending",
            "publishing",
            "final",
        }:
            raise RuntimeError(f"Invalid managed run state: {index_path}")
        if owner["state"] != "final":
            raise RuntimeError(f"Legacy project storage has pending managed run state: {index_path}")


def _has_entries(path: Path) -> bool:
    return path.is_dir() and next(path.iterdir(), None) is not None


def _fsync(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_import_marker(target: Path, project_id) -> dict | None:
    marker = target / _IMPORT_MARKER
    if not _present(marker):
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Invalid project import marker: {marker}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid project import marker: {marker}")
    sources = payload.get("sources")
    if (
        payload.get("projectId") != str(project_id)
        or not isinstance(sources, list)
        or not isinstance(payload.get("targetExisted"), bool)
        or not isinstance(payload.get("sessionsExisted"), bool)
    ):
        raise RuntimeError(f"Invalid project import marker: {marker}")
    source_names = set(sources)
    if len(source_names) != len(sources) or not source_names <= {"files", "revisions"}:
        raise RuntimeError(f"Invalid project import marker: {marker}")
    payload["sources"] = source_names
    return payload


def _write_import_marker(
    target: Path,
    project_id,
    sources: set[str],
    *,
    target_existed: bool = True,
    sessions_existed: bool = True,
) -> None:
    marker = target / _IMPORT_MARKER
    with marker.open("x", encoding="utf-8") as handle:
        json.dump(
            {
                "projectId": str(project_id),
                "sources": sorted(sources),
                "targetExisted": target_existed,
                "sessionsExisted": sessions_existed,
            },
            handle,
            separators=(",", ":"),
        )
        handle.flush()
        os.fchmod(handle.fileno(), 0o600)
        os.fsync(handle.fileno())
    _fsync(target)


def _prepare_project(project_root: Path, project_id) -> Path | None:
    if not _present(project_root):
        return None
    metadata = project_root.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"Legacy project storage root is not a directory: {project_root}")
    sessions = project_root / "sessions"
    target = sessions / str(project_id)
    sources = [project_root / name for name in ("files", "revisions")]
    destinations = [target / name for name in ("files", "revisions")]
    if _present(sessions):
        metadata = sessions.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError(f"Project sessions storage is not a directory: {sessions}")
    for path in (*sources, target):
        _validate_tree(path)
    if _present(target):
        unexpected = {entry.name for entry in target.iterdir()} - {
            "files",
            "revisions",
            _IMPORT_MARKER,
        }
        if unexpected:
            raise RuntimeError(f"Imported session storage contains unexpected entries: {target}")
        marker_sources = _read_import_marker(target, project_id)
        if marker_sources is None and any(_present(path) for path in destinations):
            raise RuntimeError(f"Unmarked imported session storage conflicts with legacy storage: {target}")
    for source, destination in zip(sources, destinations, strict=True):
        if _present(source) and _present(destination):
            raise RuntimeError(f"Legacy project storage conflicts with imported session storage: {source}")
    for revisions in (sources[1], destinations[1]):
        _reject_pending_runs(revisions)
    if not any(_has_entries(path) for path in (*sources, *destinations)):
        if _present(target):
            raise RuntimeError(f"Empty imported session storage conflicts with legacy storage: {target}")
        return None
    return target


def _move_project(project_root: Path, target: Path, project_id) -> None:
    sessions = target.parent
    sessions_existed = _present(sessions)
    target_existed = _present(target)
    if not _present(sessions):
        sessions.mkdir(mode=0o700)
        _fsync(project_root)
    if not _present(target):
        target.mkdir(mode=0o700)
        _fsync(sessions)
    if _read_import_marker(target, project_id) is None:
        _write_import_marker(
            target,
            project_id,
            {name for name in ("files", "revisions") if _present(project_root / name)},
            target_existed=target_existed,
            sessions_existed=sessions_existed,
        )
    for name in ("files", "revisions"):
        source = project_root / name
        destination = target / name
        if _present(source):
            if _present(destination):
                raise RuntimeError(f"Refusing to overwrite imported session storage: {destination}")
            source.rename(destination)
            _fsync(project_root)
            _fsync(target)
        elif not _present(destination):
            destination.mkdir(mode=0o700)
            _fsync(target)


def _rollback_project(
    project_root: Path,
    target: Path,
    project_id,
    *,
    target_existed: bool,
    sessions_existed: bool,
) -> None:
    marker = _read_import_marker(target, project_id)
    if marker is None:
        if not target_existed and _present(target):
            target.rmdir()
            _fsync(target.parent)
        if not sessions_existed and _present(target.parent):
            target.parent.rmdir()
            _fsync(project_root)
        return
    original_sources = marker["sources"]
    for name in reversed(("files", "revisions")):
        source = project_root / name
        destination = target / name
        if name in original_sources:
            if _present(source):
                if _present(destination):
                    raise RuntimeError(f"Cannot roll back conflicting project storage: {source}")
                continue
            if not _present(destination):
                raise RuntimeError(f"Cannot roll back missing project storage: {destination}")
            destination.rename(source)
            _fsync(target)
            _fsync(project_root)
        elif _present(destination):
            destination.rmdir()
            _fsync(target)
    (target / _IMPORT_MARKER).unlink()
    _fsync(target)
    if not marker["targetExisted"]:
        target.rmdir()
        _fsync(target.parent)
    if not marker["sessionsExisted"]:
        target.parent.rmdir()
        _fsync(project_root)


def _project_session_rows(connection):
    return list(
        connection.execute(
            sa.text(
                """
                SELECT id, project_id
                FROM chat_sessions
                ORDER BY project_id, id
                """
            )
        ).mappings()
    )


def _initialize_project_sessions(projects_root: Path, rows, created: list[Path]) -> None:
    for row in rows:
        if row["project_id"] is None:
            continue
        root = projects_root / str(row["project_id"]) / "sessions" / str(row["id"])
        if not _present(root):
            continue
        _validate_tree(root)
        for name in ("files", "revisions"):
            directory = root / name
            if _present(directory):
                metadata = directory.lstat()
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                    raise RuntimeError(f"Project session storage is not a directory: {directory}")
                continue
            directory.mkdir(mode=0o700)
            created.append(directory)
            _fsync(root)


def _rollback_initialized_directories(created: list[Path]) -> None:
    for directory in reversed(created):
        directory.rmdir()
        _fsync(directory.parent)


def _read_revision_index(path: Path) -> tuple[dict, bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Invalid managed revision index: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("path"), str):
        raise RuntimeError(f"Invalid managed revision index: {path}")
    return payload, raw


def _revision_indexes(root: Path):
    if not _present(root):
        return
    _validate_tree(root)
    for history in root.iterdir():
        if history.name.startswith("."):
            continue
        index_path = history / "index.json"
        if index_path.is_file():
            payload, raw = _read_revision_index(index_path)
            yield history, payload, raw


def _prepare_plan_moves(data: Path, rows) -> list[dict]:
    moves = []
    for row in rows:
        session_id = str(row["id"])
        project_id = row["project_id"]
        source_root = (
            data / "sessions" / session_id
            if project_id is None
            else data / "projects" / str(project_id)
        )
        target_root = (
            source_root
            if project_id is None
            else source_root / "sessions" / session_id
        )
        old_path = f"plans/{session_id}/plan.md"
        source_file = source_root / "files" / old_path
        target_file = target_root / "files" / "plan.md"
        source_revisions = source_root / "revisions"
        target_revisions = target_root / "revisions"
        histories = []
        canonical_histories = []
        scanned = set()
        for revisions in (source_revisions, target_revisions):
            if revisions in scanned:
                continue
            scanned.add(revisions)
            for history, payload, raw in _revision_indexes(revisions):
                if revisions == target_revisions and payload["path"] == "plan.md":
                    canonical_histories.append(history)
                if payload["path"] == old_path:
                    histories.append((history, payload, raw))
        if len(histories) > 1:
            raise RuntimeError(f"Multiple legacy plan revision histories exist: {source_file}")
        if len(canonical_histories) > 1:
            raise RuntimeError(f"Multiple canonical plan revision histories exist: {target_root}")
        if _present(source_file):
            metadata = source_file.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError(f"Legacy plan is not a regular file: {source_file}")
            if _present(target_file) or canonical_histories:
                raise RuntimeError(f"Canonical plan already exists: {target_file}")
        elif not histories and not (_present(target_file) or canonical_histories):
            continue
        if histories and canonical_histories:
            raise RuntimeError(f"Canonical plan revision history already exists: {target_root}")
        history = histories[0] if histories else None
        if history is not None:
            target_history = target_revisions / history[0].name
            if history[0] != target_history and _present(target_history):
                raise RuntimeError(f"Canonical plan revision history already exists: {target_history}")
        moves.append(
            {
                "session_id": row["id"],
                "source_root_id": row["id"] if project_id is None else project_id,
                "old_path": old_path,
                "source_file": source_file,
                "target_file": target_file,
                "source_history": history[0] if history else None,
                "target_history": target_revisions / history[0].name if history else None,
                "index": history[1] if history else None,
                "raw_index": history[2] if history else None,
                "restore_file": _present(source_file) or bool(histories),
            }
        )
    return moves


def _mkdir(path: Path, created: list[Path]) -> None:
    if _present(path):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError(f"Session storage path is not a directory: {path}")
        return
    path.mkdir(mode=0o700)
    created.append(path)
    _fsync(path.parent)


def _replace_file(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.migration")
    if _present(temporary):
        temporary.unlink()
    with temporary.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fchmod(handle.fileno(), 0o600)
        os.fsync(handle.fileno())
    temporary.replace(path)
    _fsync(path.parent)


def _move_plans(
    connection, moves: list[dict], created: list[Path], completed: list[dict]
) -> None:
    for move in moves:
        move["removed"] = []
        completed.append(move)
        target_root = move["target_file"].parents[1]
        for directory in (target_root.parent, target_root, target_root / "files", target_root / "revisions"):
            _mkdir(directory, created)
        if _present(move["source_file"]):
            move["source_file"].rename(move["target_file"])
            _fsync(move["source_file"].parent)
            _fsync(move["target_file"].parent)
        if move["source_history"] is not None:
            if move["source_history"] != move["target_history"]:
                move["source_history"].rename(move["target_history"])
                _fsync(move["source_history"].parent)
                _fsync(move["target_history"].parent)
            updated = {**move["index"], "path": "plan.md"}
            _replace_file(
                move["target_history"] / "index.json",
                json.dumps(updated, separators=(",", ":")).encode(),
            )
        removed = []
        for directory in (move["source_file"].parent, move["source_file"].parent.parent):
            if directory.is_dir() and next(directory.iterdir(), None) is None:
                directory.rmdir()
                removed.append(directory)
                _fsync(directory.parent)
        move["removed"] = removed
        connection.execute(
            sa.text(
                """
                UPDATE message_attachments
                SET file_root_id = :session_id, file_path = 'plan.md'
                WHERE file_root_id = :source_root_id AND file_path = :old_path
                """
            ),
            {
                "session_id": move["session_id"],
                "source_root_id": move["source_root_id"],
                "old_path": move["old_path"],
            },
        )


def _rollback_plan_moves(moves: list[dict]) -> None:
    for move in reversed(moves):
        for directory in reversed(move["removed"]):
            directory.mkdir(mode=0o700)
            _fsync(directory.parent)
        if move["target_history"] is not None:
            current_history = (
                move["target_history"]
                if _present(move["target_history"])
                else move["source_history"]
            )
            _replace_file(current_history / "index.json", move["raw_index"])
            if (
                move["source_history"] != move["target_history"]
                and current_history == move["target_history"]
            ):
                move["target_history"].rename(move["source_history"])
                _fsync(move["target_history"].parent)
                _fsync(move["source_history"].parent)
        if (
            move["restore_file"]
            and _present(move["target_file"])
            and not _present(move["source_file"])
        ):
            move["target_file"].rename(move["source_file"])
            _fsync(move["target_file"].parent)
            _fsync(move["source_file"].parent)


def _migrate(connection) -> None:
    data = Path(os.environ.get("MATO_LOOP_DATA_DIR", "/data"))
    projects_root = data / "projects"
    _validate_tree(projects_root)
    rows = list(
        connection.execute(
            sa.text(
                """
                SELECT project.id, project.user_id,
                       session.id AS existing_session_id
                FROM projects AS project
                LEFT JOIN chat_sessions AS session ON session.id = project.id
                ORDER BY project.id
                """
            )
        ).mappings()
    )
    session_rows = _project_session_rows(connection)
    plan_moves = _prepare_plan_moves(data, session_rows)
    prepared = []
    for row in rows:
        project_root = projects_root / str(row["id"])
        target = _prepare_project(project_root, row["id"])
        if target is None:
            continue
        if row["existing_session_id"] is not None:
            raise RuntimeError(f"Project import session ID already exists: {row['id']}")
        prepared.append((row, project_root, target))

    initialized = []
    migrated_plans = []
    touched = []
    try:
        _initialize_project_sessions(projects_root, session_rows, initialized)
        _move_plans(connection, plan_moves, initialized, migrated_plans)
        prepared = []
        for row in rows:
            project_root = projects_root / str(row["id"])
            target = _prepare_project(project_root, row["id"])
            if target is None:
                continue
            if row["existing_session_id"] is not None:
                raise RuntimeError(f"Project import session ID already exists: {row['id']}")
            prepared.append((row, project_root, target))
        for row, project_root, target in prepared:
            touched.append(
                (
                    project_root,
                    target,
                    row["id"],
                    _present(target),
                    _present(target.parent),
                )
            )
            _move_project(project_root, target, row["id"])
            connection.execute(
                sa.text(
                    """
                    INSERT INTO chat_sessions (
                        id, user_id, project_id, title, session_type,
                        title_generation_status, is_pinned, memory_mode,
                        web_search_policy
                    ) VALUES (
                        :id, :user_id, :id, :title, 'chat',
                        'skipped', false, 'full', 'available'
                    )
                    """
                ),
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "title": "Übernommene Projektdateien",
                },
            )
    except BaseException:
        rollback_errors = []
        for project_root, target, project_id, target_existed, sessions_existed in reversed(
            touched
        ):
            try:
                _rollback_project(
                    project_root,
                    target,
                    project_id,
                    target_existed=target_existed,
                    sessions_existed=sessions_existed,
                )
            except BaseException as rollback_exc:
                rollback_errors.append(rollback_exc)
        try:
            _rollback_plan_moves(migrated_plans)
        except BaseException as rollback_exc:
            rollback_errors.append(rollback_exc)
        try:
            _rollback_initialized_directories(initialized)
        except BaseException as rollback_exc:
            rollback_errors.append(rollback_exc)
        if rollback_errors:
            raise RuntimeError("Project file migration rollback failed") from rollback_errors[0]
        raise


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    _migrate(op.get_bind())
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    raise RuntimeError(
        "Downgrade is not supported because imported project files may have changed"
    )
    # ### end Alembic commands ###
