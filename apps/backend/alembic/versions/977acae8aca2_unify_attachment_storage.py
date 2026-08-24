"""unify attachment storage

Revision ID: 977acae8aca2
Revises: 68a861e6d6f0
Create Date: 2026-08-24 14:08:22.637986
"""

import hashlib
import json
import os
from datetime import UTC
from pathlib import Path, PurePosixPath
from typing import Sequence, Union
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from alembic import op
import sqlalchemy as sa


revision: str = '977acae8aca2'
down_revision: Union[str, None] = '68a861e6d6f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _roots(session_id, project_id) -> tuple[Path, Path]:
    data = Path(os.environ.get("MATO_LOOP_DATA_DIR", "/data"))
    if project_id is None:
        session = data / "sessions" / str(session_id)
        return session, session
    project = data / "projects" / str(project_id)
    return project / "sessions" / str(session_id), project


def _logical_root(session_id, file_root_id) -> Path:
    data = Path(os.environ.get("MATO_LOOP_DATA_DIR", "/data"))
    if file_root_id == session_id:
        return data / "sessions" / str(session_id)
    return data / "projects" / str(file_root_id)


def _safe_child(root: Path, *parts: str) -> Path:
    current = root
    if current.is_symlink():
        raise RuntimeError("Managed attachment root must not be a symlink")
    for part in parts:
        current /= part
        if current.is_symlink():
            raise RuntimeError("Managed attachment path must not contain symlinks")
    return current


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write(root: Path, path: Path, data: bytes) -> None:
    missing = []
    parent = path.parent
    while parent != root and not parent.exists():
        missing.append(parent)
        parent = parent.parent
    if parent != root and not parent.is_dir():
        raise RuntimeError("Managed attachment parent is invalid")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        _fsync_directory(directory.parent)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _managed_file(
    root: Path,
    *,
    file_id: UUID,
    path: str,
    revision: int,
    data: bytes,
    created_at,
) -> None:
    relative = PurePosixPath(path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise RuntimeError("Invalid managed attachment path")
    target = _safe_child(root, "files", *relative.parts)
    history = _safe_child(root, "revisions", str(file_id))
    revision_path = _safe_child(root, "revisions", str(file_id), str(revision))
    digest = hashlib.sha256(data).hexdigest()
    index_path = _safe_child(root, "revisions", str(file_id), "index.json")
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if index.get("fileId") != str(file_id):
            raise RuntimeError("Managed attachment history conflicts with existing data")
        item = next(
            (item for item in index.get("revisions", []) if item.get("revision") == revision),
            None,
        )
        if item is None or item.get("sizeBytes") != len(data) or item.get("sha256") != digest:
            raise RuntimeError("Managed attachment revision metadata is invalid")
        if not revision_path.is_file() or revision_path.read_bytes() != data:
            raise RuntimeError("Managed attachment revision content is invalid")
        return
    if target.exists() and target.read_bytes() != data:
        raise RuntimeError("Managed attachment path already contains different data")
    if revision_path.exists() and revision_path.read_bytes() != data:
        raise RuntimeError("Managed attachment revision already contains different data")
    if not target.exists():
        _write(root, target, data)
    if not revision_path.exists():
        _write(root, revision_path, data)
    item = {
        "revision": revision,
        "createdAt": created_at.astimezone(UTC).isoformat(),
        "sizeBytes": len(data),
        "sha256": digest,
    }
    _write(
        root,
        index_path,
        json.dumps(
            {
                "fileId": str(file_id),
                "path": path,
                "currentRevision": revision,
                "maxRevision": revision,
                "revisions": [item],
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode(),
    )


def _source_data(row, private_root: Path, logical_root: Path) -> tuple[UUID, int, str, bytes]:
    if row["storage_key"] is not None:
        path = PurePosixPath(row["storage_key"])
        if (
            path.is_absolute()
            or len(path.parts) != 3
            or path.parts[0] not in {"attachments", "generated", "downloads"}
            or path.parts[1] != str(row["id"])
        ):
            raise RuntimeError("Invalid private attachment path")
        file_id = UUID(str(row["id"]))
        revision = 1
        source = _safe_child(private_root, *path.parts)
        fallback = _safe_child(logical_root, "revisions", str(file_id), "1")
        data = source.read_bytes() if source.is_file() else fallback.read_bytes()
        return file_id, revision, str(path), data
    source = _safe_child(
        logical_root,
        "revisions",
        str(row["file_id"]),
        str(row["file_revision"]),
    )
    return UUID(str(row["file_id"])), row["file_revision"], row["file_path"], source.read_bytes()


def _companion_data(row, private_root: Path, logical_root: Path) -> bytes | None:
    metadata = row["metadata_json"] or {}
    old_path = metadata.get("derivedPath")
    if not isinstance(old_path, str):
        return None
    name = PurePosixPath(old_path).name
    if name in {"", ".", ".."}:
        raise RuntimeError("Invalid attachment processing companion path")
    candidates = [
        _safe_child(private_root, "attachments", str(row["id"]), "derived", name),
        _safe_child(
            logical_root,
            "revisions",
            str(row["file_id"] or row["id"]),
            "derived",
            str(row["file_revision"] or 1),
            name,
        ),
        _safe_child(
            logical_root,
            "revisions",
            str(uuid5(NAMESPACE_URL, f"mato-loop:attachment:{row['id']}:companion")),
            "1",
        ),
    ]
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        raise RuntimeError("Attachment processing companion is missing")
    data = source.read_bytes()
    if (
        metadata.get("derivedSizeBytes") != len(data)
        or metadata.get("derivedSha256") != hashlib.sha256(data).hexdigest()
    ):
        raise RuntimeError("Attachment processing companion is invalid")
    return data


def _migrate_attachments(connection) -> None:
    rows = list(
        connection.execute(
            sa.text(
                """
                SELECT ma.*, m.session_id, cs.project_id
                FROM message_attachments ma
                JOIN messages m ON m.id = ma.message_id
                JOIN chat_sessions cs ON cs.id = m.session_id
                ORDER BY ma.created_at, ma.id
                """
            )
        ).mappings()
    )
    for row in rows:
        private_root, logical_root = _roots(row["session_id"], row["project_id"])
        file_id, revision, path, data = _source_data(row, private_root, logical_root)
        if len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise RuntimeError("Attachment source content is invalid")
        _managed_file(
            logical_root,
            file_id=file_id,
            path=path,
            revision=revision,
            data=data,
            created_at=row["created_at"],
        )

        metadata = dict(row["metadata_json"] or {})
        for key in ("derivedPath", "derivedName", "derivedSizeBytes", "derivedSha256"):
            metadata.pop(key, None)
        companion_id = companion_revision = companion_path = companion = None
        if row["kind"] == "text":
            companion_id, companion_revision, companion_path, companion = (
                file_id,
                revision,
                path,
                data,
            )
        else:
            companion = _companion_data(row, private_root, logical_root)
            if companion is not None:
                suffix = ".wav" if row["kind"] == "audio" else ".txt"
                source_path = PurePosixPath(path)
                companion_path = str(source_path.with_name(f"{source_path.stem}{suffix}"))
                if companion_path == path or (
                    _safe_child(
                        logical_root,
                        "files",
                        *PurePosixPath(companion_path).parts,
                    ).exists()
                ):
                    companion_path = str(
                        source_path.with_name(
                            f"{source_path.stem}-{str(row['id']).replace('-', '')[:8]}{suffix}"
                        )
                    )
                companion_id = uuid5(NAMESPACE_URL, f"mato-loop:attachment:{row['id']}:companion")
                companion_revision = 1
                _managed_file(
                    logical_root,
                    file_id=companion_id,
                    path=companion_path,
                    revision=1,
                    data=companion,
                    created_at=row["created_at"],
                )
        if companion is not None:
            metadata.update(
                companionFileRootId=str(row["project_id"] or row["session_id"]),
                companionFileId=str(companion_id),
                companionFileRevision=companion_revision,
                companionFilePath=companion_path,
                companionSizeBytes=len(companion),
                companionSha256=hashlib.sha256(companion).hexdigest(),
            )
        connection.execute(
            sa.text(
                """
                UPDATE message_attachments
                SET file_root_id = :root_id, file_id = :file_id,
                    file_revision = :revision, file_path = :path,
                    metadata_json = CAST(:metadata AS jsonb)
                WHERE id = :id
                """
            ),
            {
                "root_id": row["project_id"] or row["session_id"],
                "file_id": file_id,
                "revision": revision,
                "path": path,
                "metadata": json.dumps(metadata),
                "id": row["id"],
            },
        )


def _restore_companions(connection) -> None:
    rows = connection.execute(
        sa.text(
            """
            SELECT ma.*, m.session_id, cs.project_id
            FROM message_attachments ma
            JOIN messages m ON m.id = ma.message_id
            JOIN chat_sessions cs ON cs.id = m.session_id
            ORDER BY ma.created_at, ma.id
            """
        )
    ).mappings()
    companion_keys = (
        "companionFileRootId",
        "companionFileId",
        "companionFileRevision",
        "companionFilePath",
        "companionSizeBytes",
        "companionSha256",
    )
    for row in rows:
        metadata = dict(row["metadata_json"] or {})
        if not any(key in metadata for key in companion_keys):
            continue
        try:
            companion_id = UUID(metadata["companionFileId"])
            companion_revision = int(metadata["companionFileRevision"])
            companion_size = int(metadata["companionSizeBytes"])
            companion_sha256 = metadata["companionSha256"]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Invalid attachment processing companion metadata") from exc
        try:
            companion_root_id = UUID(metadata["companionFileRootId"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Invalid attachment processing companion metadata") from exc
        logical_root = _logical_root(row["session_id"], companion_root_id)
        source = _safe_child(
            logical_root,
            "revisions",
            str(companion_id),
            str(companion_revision),
        )
        try:
            data = source.read_bytes()
        except FileNotFoundError:
            continue
        if len(data) != companion_size or hashlib.sha256(data).hexdigest() != companion_sha256:
            raise RuntimeError("Attachment processing companion is invalid")
        name = "audio.wav" if row["kind"] == "audio" else "content.txt"
        target = _safe_child(
            logical_root,
            "revisions",
            str(row["file_id"]),
            "derived",
            str(row["file_revision"]),
            name,
        )
        if target.exists():
            if target.read_bytes() != data:
                raise RuntimeError("Attachment processing companion conflicts with existing data")
        else:
            _write(logical_root, target, data)
        for key in companion_keys:
            metadata.pop(key, None)
        metadata.update(
            derivedPath=(
                f"revisions/{row['file_id']}/derived/{row['file_revision']}/{name}"
            ),
            derivedName=name,
            derivedSizeBytes=len(data),
            derivedSha256=hashlib.sha256(data).hexdigest(),
        )
        connection.execute(
            sa.text(
                """
                UPDATE message_attachments
                SET metadata_json = CAST(:metadata AS jsonb)
                WHERE id = :id
                """
            ),
            {"metadata": json.dumps(metadata), "id": row["id"]},
        )


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(
        op.f('ck_message_attachments_message_attachment_source_valid'),
        'message_attachments',
        type_='check',
    )
    _migrate_attachments(op.get_bind())
    op.alter_column('message_attachments', 'file_root_id',
               existing_type=sa.UUID(),
               nullable=False)
    op.alter_column('message_attachments', 'file_id',
               existing_type=sa.UUID(),
               nullable=False)
    op.alter_column('message_attachments', 'file_revision',
               existing_type=sa.INTEGER(),
               nullable=False)
    op.alter_column('message_attachments', 'file_path',
               existing_type=sa.VARCHAR(length=1000),
               nullable=False)
    op.drop_constraint(op.f('uq_message_attachments_storage_key'), 'message_attachments', type_='unique')
    op.drop_column('message_attachments', 'storage_key')
    op.create_check_constraint(
        op.f('ck_message_attachments_message_attachment_file_valid'),
        'message_attachments',
        'file_root_id IS NOT NULL AND file_id IS NOT NULL '
        'AND file_revision IS NOT NULL AND file_path IS NOT NULL',
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    _restore_companions(op.get_bind())
    op.drop_constraint(
        op.f('ck_message_attachments_message_attachment_file_valid'),
        'message_attachments',
        type_='check',
    )
    op.add_column('message_attachments', sa.Column('storage_key', sa.VARCHAR(length=500), autoincrement=False, nullable=True))
    op.create_unique_constraint(op.f('uq_message_attachments_storage_key'), 'message_attachments', ['storage_key'], postgresql_nulls_not_distinct=False)
    op.alter_column('message_attachments', 'file_path',
               existing_type=sa.VARCHAR(length=1000),
               nullable=True)
    op.alter_column('message_attachments', 'file_revision',
               existing_type=sa.INTEGER(),
               nullable=True)
    op.alter_column('message_attachments', 'file_id',
               existing_type=sa.UUID(),
               nullable=True)
    op.alter_column('message_attachments', 'file_root_id',
               existing_type=sa.UUID(),
               nullable=True)
    op.create_check_constraint(
        op.f('ck_message_attachments_message_attachment_source_valid'),
        'message_attachments',
        '(storage_key IS NOT NULL AND file_root_id IS NULL AND file_id IS NULL '
        'AND file_revision IS NULL AND file_path IS NULL) OR '
        '(storage_key IS NULL AND file_root_id IS NOT NULL AND file_id IS NOT NULL '
        'AND file_revision IS NOT NULL AND file_path IS NOT NULL)',
    )
    # ### end Alembic commands ###
