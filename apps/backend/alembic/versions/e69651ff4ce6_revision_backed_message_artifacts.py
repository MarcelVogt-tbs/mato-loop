"""revision backed message artifacts

Revision ID: e69651ff4ce6
Revises: 757219dcf563
Create Date: 2026-08-22 19:39:07.538510
"""

import hashlib
import json
import os
import shutil
from datetime import UTC
from pathlib import Path, PurePosixPath
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e69651ff4ce6"
down_revision: Union[str, None] = "757219dcf563"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _roots(session_id, project_id) -> tuple[Path, Path]:
    data = Path(os.environ.get("MATO_LOOP_DATA_DIR", "/data"))
    if project_id is None:
        private = data / "sessions" / str(session_id)
        return private, private
    project = data / "projects" / str(project_id)
    return project / "sessions" / str(session_id), project


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.migration.tmp")
    temporary.write_bytes(data)
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _rewrite_session_paths(value):
    if isinstance(value, list):
        return [_rewrite_session_paths(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _rewrite_session_paths(item) for key, item in value.items()}
    if result.get("root") == "session":
        for key in ("path", "sourcePath", "destinationPath"):
            path = result.get(key)
            if isinstance(path, str) and path.startswith("files/"):
                result[key] = path.removeprefix("files/") or "."
    return result


def _remove_empty_tree(root: Path) -> None:
    if not root.is_dir():
        return
    for directory, _subdirectories, _files in os.walk(root, topdown=False):
        try:
            Path(directory).rmdir()
        except OSError:
            pass


def _migrate_storage(connection) -> None:
    rows = connection.execute(
        sa.text(
            """
            SELECT ma.id, ma.storage_key, ma.filename, ma.metadata_json, ma.size_bytes,
                   ma.sha256, ma.created_at, m.session_id, cs.project_id
            FROM message_attachments ma
            JOIN messages m ON m.id = ma.message_id
            JOIN chat_sessions cs ON cs.id = m.session_id
            WHERE ma.category IN ('generated', 'downloaded')
            """
        )
    ).mappings()
    for row in rows:
        private_root, logical_root = _roots(row["session_id"], row["project_id"])
        storage_path = PurePosixPath(row["storage_key"])
        if (
            storage_path.is_absolute()
            or len(storage_path.parts) != 3
            or storage_path.parts[0] not in {"generated", "downloads"}
            or storage_path.parts[1] != str(row["id"])
        ):
            raise RuntimeError("Invalid legacy artifact path")
        source = private_root.joinpath(*storage_path.parts)
        suffix = PurePosixPath(row["filename"] or source.name).suffix
        file_path = f"artifact-{str(row['id']).replace('-', '')[:8]}{suffix}"
        target = logical_root / "files" / file_path
        revision_root = logical_root / "revisions" / str(row["id"])
        revision = revision_root / "1"
        logical_root.joinpath("files").mkdir(mode=0o700, parents=True, exist_ok=True)
        logical_root.joinpath("revisions").mkdir(mode=0o700, parents=True, exist_ok=True)
        if source.exists():
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(source, target)
        if not target.is_file():
            raise RuntimeError("Legacy artifact file is missing")
        data = target.read_bytes()
        if len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise RuntimeError("Legacy artifact content is invalid")
        revision_root.mkdir(mode=0o700, exist_ok=True)
        if not revision.exists():
            shutil.copyfile(target, revision)
            revision.chmod(0o600)
        index = {
            "fileId": str(row["id"]),
            "path": file_path,
            "currentRevision": 1,
            "revisions": [
                {
                    "revision": 1,
                    "createdAt": row["created_at"].astimezone(UTC).isoformat(),
                    "sizeBytes": row["size_bytes"],
                    "sha256": row["sha256"],
                }
            ],
        }
        _write(
            revision_root / "index.json",
            json.dumps(index, ensure_ascii=True, separators=(",", ":")).encode(),
        )
        metadata = dict(row["metadata_json"] or {})
        derived_path = metadata.get("derivedPath")
        if isinstance(derived_path, str) and derived_path.startswith(
            f"derived/{row['id']}/"
        ):
            name = PurePosixPath(derived_path).name
            old_derived = private_root / "derived" / str(row["id"])
            new_derived = revision_root / "derived" / "1"
            if old_derived.exists() and not new_derived.exists():
                new_derived.parent.mkdir(mode=0o700, exist_ok=True)
                old_derived.rename(new_derived)
            metadata["derivedPath"] = f"revisions/{row['id']}/derived/1/{name}"
        connection.execute(
            sa.text(
                """
                UPDATE message_attachments
                SET storage_key = NULL, file_root_id = :root_id, file_id = :file_id,
                    file_revision = 1, file_path = :file_path, purpose = 'artifact',
                    metadata_json = CAST(:metadata AS jsonb)
                WHERE id = :id
                """
            ),
            {
                "root_id": row["project_id"] or row["session_id"],
                "file_id": row["id"],
                "file_path": file_path,
                "metadata": json.dumps(metadata),
                "id": row["id"],
            },
        )

    uploads = connection.execute(
        sa.text(
            """
            SELECT ma.id, ma.metadata_json, m.session_id, cs.project_id
            FROM message_attachments ma
            JOIN messages m ON m.id = ma.message_id
            JOIN chat_sessions cs ON cs.id = m.session_id
            WHERE ma.category = 'user_upload'
            """
        )
    ).mappings()
    for row in uploads:
        metadata = dict(row["metadata_json"] or {})
        derived_path = metadata.get("derivedPath")
        if not isinstance(derived_path, str) or not derived_path.startswith(
            f"derived/{row['id']}/"
        ):
            continue
        private_root, _logical_root = _roots(row["session_id"], row["project_id"])
        old = private_root / "derived" / str(row["id"])
        new = private_root / "attachments" / str(row["id"]) / "derived"
        if old.exists() and not new.exists():
            old.rename(new)
        metadata["derivedPath"] = (
            f"attachments/{row['id']}/derived/{PurePosixPath(derived_path).name}"
        )
        connection.execute(
            sa.text(
                "UPDATE message_attachments SET metadata_json = CAST(:metadata AS jsonb) "
                "WHERE id = :id"
            ),
            {"metadata": json.dumps(metadata), "id": row["id"]},
        )

    roots = {
        _roots(row.session_id, row.project_id)[1]
        for row in connection.execute(
            sa.text("SELECT id AS session_id, project_id FROM chat_sessions")
        )
    }
    for root in roots:
        revisions = root / "revisions"
        if not revisions.is_dir():
            continue
        for index_path in revisions.glob("*/index.json"):
            index = json.loads(index_path.read_text(encoding="utf-8"))
            path = index.get("path")
            if isinstance(path, str) and path.startswith("files/"):
                index["path"] = path.removeprefix("files/")
                _write(
                    index_path,
                    json.dumps(index, ensure_ascii=True, separators=(",", ":")).encode(),
                )

    for table, column in (("messages", "provider_data"), ("chat_sessions", "control_center")):
        for row in connection.execute(
            sa.text(f"SELECT id, {column} AS value FROM {table} WHERE {column} IS NOT NULL")
        ).mappings():
            value = _rewrite_session_paths(row["value"])
            connection.execute(
                sa.text(
                    f"UPDATE {table} SET {column} = CAST(:value AS jsonb) WHERE id = :id"
                ),
                {"value": json.dumps(value), "id": row["id"]},
            )
    connection.execute(sa.text("DELETE FROM context_summaries"))
    session_roots = {
        (_roots(row.session_id, row.project_id)[0], row.project_id is not None)
        for row in connection.execute(
            sa.text("SELECT id AS session_id, project_id FROM chat_sessions")
        )
    }
    for root, in_project in session_roots:
        for name in ("generated", "downloads", "derived"):
            _remove_empty_tree(root / name)
        if in_project:
            for name in ("files", "revisions"):
                _remove_empty_tree(root / name)


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column("message_attachments", sa.Column("file_root_id", sa.Uuid(), nullable=True))
    op.add_column("message_attachments", sa.Column("file_id", sa.Uuid(), nullable=True))
    op.add_column("message_attachments", sa.Column("file_revision", sa.Integer(), nullable=True))
    op.add_column("message_attachments", sa.Column("file_path", sa.String(length=1000), nullable=True))
    op.alter_column(
        "message_attachments",
        "storage_key",
        existing_type=sa.VARCHAR(length=500),
        nullable=True,
    )
    _migrate_storage(op.get_bind())
    op.create_index(op.f("ix_message_attachments_file_id"), "message_attachments", ["file_id"], unique=False)
    op.create_index(op.f("ix_message_attachments_file_root_id"), "message_attachments", ["file_root_id"], unique=False)
    op.create_check_constraint(
        op.f("ck_message_attachments_file_revision_positive"),
        "message_attachments",
        "file_revision IS NULL OR file_revision > 0",
    )
    op.create_check_constraint(
        op.f("ck_message_attachments_message_attachment_source_valid"),
        "message_attachments",
        "(storage_key IS NOT NULL AND file_root_id IS NULL AND file_id IS NULL "
        "AND file_revision IS NULL AND file_path IS NULL) OR "
        "(storage_key IS NULL AND file_root_id IS NOT NULL AND file_id IS NOT NULL "
        "AND file_revision IS NOT NULL AND file_path IS NOT NULL)",
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(
        op.f("ck_message_attachments_message_attachment_source_valid"),
        "message_attachments",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_message_attachments_file_revision_positive"),
        "message_attachments",
        type_="check",
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT ma.id, ma.file_id, ma.file_revision, ma.file_path, ma.filename,
                   m.session_id, cs.project_id
            FROM message_attachments ma
            JOIN messages m ON m.id = ma.message_id
            JOIN chat_sessions cs ON cs.id = m.session_id
            WHERE ma.storage_key IS NULL
            """
        )
    ).mappings()
    for row in rows:
        private_root, logical_root = _roots(row["session_id"], row["project_id"])
        filename = row["filename"] or PurePosixPath(row["file_path"]).name
        storage_key = f"generated/{row['id']}/{filename}"
        source = logical_root / "revisions" / str(row["file_id"]) / str(row["file_revision"])
        target = private_root.joinpath(*PurePosixPath(storage_key).parts)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o600)
        connection.execute(
            sa.text(
                "UPDATE message_attachments SET storage_key = :storage_key WHERE id = :id"
            ),
            {"storage_key": storage_key, "id": row["id"]},
        )
    op.drop_index(op.f("ix_message_attachments_file_root_id"), table_name="message_attachments")
    op.drop_index(op.f("ix_message_attachments_file_id"), table_name="message_attachments")
    op.alter_column(
        "message_attachments",
        "storage_key",
        existing_type=sa.VARCHAR(length=500),
        nullable=False,
    )
    op.drop_column("message_attachments", "file_path")
    op.drop_column("message_attachments", "file_revision")
    op.drop_column("message_attachments", "file_id")
    op.drop_column("message_attachments", "file_root_id")
    # ### end Alembic commands ###
