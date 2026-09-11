import asyncio
import hashlib
import json
import os
from io import BytesIO
from pathlib import PurePosixPath
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
from zipfile import ZipFile

import pytest
from pydantic import ValidationError
from test_agent_tools_contract import _context

import mato_loop_backend.agent.tools as tools_module
import mato_loop_backend.file_archives as archive_module
from mato_loop_backend.agent.permissions import PermissionDecision
from mato_loop_backend.agent.tools import ToolAttachment, tool_registry
from mato_loop_backend.execution.client import WorkspaceListResult, WorkspaceStatResult
from mato_loop_backend.file_archives import ArchiveEntry, ArchivePage, FileArchiveError
from mato_loop_backend.providers import ToolCall
from mato_loop_backend.workspace import RootFileService


def _zip_contents(result, filename, context):
    assert not result.is_error, result.content
    assert len(result.attachments) == 1
    attachment = result.attachments[0]
    assert isinstance(attachment, ToolAttachment)
    assert (attachment.kind, attachment.mime_type, attachment.filename) == (
        "file", "application/zip", filename
    )
    assert attachment.file_id is not None
    payload = json.loads(result.content)
    assert payload["filename"] == filename
    files = RootFileService(
        context.session.id,
        project_id=context.session.project_id,
        run_id=context.run_id,
        session_storage=context.root_files.session_storage,
    )
    if attachment.file_revision is None:
        handle = files.open_working_file(
            attachment.file_id, expected_sha256=payload["sha256"]
        )
    else:
        handle = files.open_revision_by_id(
            attachment.file_id, attachment.file_revision
        )
    with handle:
        data = handle.read()
    assert payload["sizeBytes"] == len(data)
    assert payload["sha256"] == hashlib.sha256(data).hexdigest()
    with ZipFile(BytesIO(data)) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(set(archive.namelist()))
        return {name: archive.read(name) for name in archive.namelist()}


def _computer(context, nodes):
    """Serve one entry per page; bytes are files and None denotes a directory."""
    async def request(operation, arguments, result_model):
        path = arguments["path"]
        if operation == "filesystem.stat":
            assert result_model is WorkspaceStatResult
            data = nodes[path]
            return WorkspaceStatResult(
                path=path, kind="directory" if data is None else "file",
                size=None if data is None else len(data), modified_at=0.0,
            )
        assert operation == "filesystem.list"
        assert result_model is WorkspaceListResult
        entries = [
            {"path": name, "name": PurePosixPath(name).name,
             "kind": "directory" if data is None else "file",
             "size": None if data is None else len(data), "modifiedAt": 0.0}
            for name, data in nodes.items()
            if str(PurePosixPath(name).parent) == path
        ]
        offset = arguments.get("offset", 0)
        has_more = offset + 1 < len(entries)
        return WorkspaceListResult.model_validate({
            "entries": entries[offset:offset + 1], "hasMore": has_more,
            "nextOffset": offset + 1 if has_more else None,
        })

    context.session.current_directory = "/work"
    async def open_export(path, *, limit):
        assert len(nodes[path]) <= limit
        return BytesIO(nodes[path])

    context.execution_client = SimpleNamespace(
        agent_id=uuid4(), current_directory="/work", supports=lambda _: True,
        request=AsyncMock(side_effect=request),
        open_export=AsyncMock(side_effect=open_export),
    )
    return context.execution_client


@pytest.mark.parametrize("selection", ["folder", "root", "list", "single-item-list"])
async def test_managed_zip_contents_and_names(tmp_path, selection):
    arguments = {
        "folder": {"path": "reports"},
        "root": {"path": "."},
        "list": {"paths": [
            "reports/a.txt", "reports/a.txt", "reports/nested/a.txt",
            "reports/.hidden", "reports/zero.txt",
        ]},
        "single-item-list": {"paths": ["reports/a.txt"]},
    }[selection]
    call = ToolCall("zip", "provide_file", {"root": "files", **arguments})
    context = _context(tmp_path, call)
    for path in ("reports", "reports/nested", "reports/empty"):
        context.root_files.create_directory("session", path)
    files = {"a.txt": b"\x00\xff\x80binary", "nested/a.txt": b"nested",
             ".hidden": b"hidden", "zero.txt": b""}
    for path, data in files.items():
        context.root_files.create_file(
            "session", f"reports/{path}", BytesIO(data), max_bytes=100
        )

    result = await tool_registry.execute(call, context)

    contents = _zip_contents(result, "reports.zip" if selection == "folder" else "download.zip", context)
    prefix = "reports/" if selection in {"folder", "root"} else ""
    expected = {prefix + path: data for path, data in files.items()}
    if selection == "single-item-list":
        expected = {"a.txt": files["a.txt"]}
    assert {name: data for name, data in contents.items() if not name.endswith("/")} == expected
    if selection in {"folder", "root"}:
        assert contents["reports/empty/"] == b""
    assert json.loads(result.content)["root"] == "files"


@pytest.mark.parametrize("arguments", [
    {}, {"path": "a", "paths": ["b"]}, {"paths": []},
    {"path": ""}, {"paths": [""]}, {"paths": "a"}, {"paths": [42]},
])
def test_provide_file_rejects_invalid_choices(arguments):
    with pytest.raises(ValidationError):
        tool_registry._definitions["provide_file"].validate_arguments({"root": "files", **arguments})


@pytest.mark.parametrize("root", ["files", "computer"])
@pytest.mark.parametrize("empty", [False, True])
async def test_zip_root_contents_have_no_root_prefix(tmp_path, root, empty):
    call = ToolCall("zip", "provide_file", {"root": root, "path": "."})
    context = _context(tmp_path, call)
    if root == "computer":
        _computer(context, {"/work": None, **({} if empty else {"/work/a.txt": b"a"})})
    elif not empty:
        context.root_files.write_file("session", "a.txt", "a")

    result = await tool_registry.execute(call, context)

    assert _zip_contents(result, "download.zip", context) == ({} if empty else {"a.txt": b"a"})


def test_provide_file_list_has_schema_bound():
    definition = tool_registry._definitions["provide_file"]
    field = definition.schema()["parameters"]["properties"]["paths"]
    array = next(item for item in field.get("anyOf", [field]) if item.get("type") == "array")
    assert array["minItems"] == 1
    assert 1 <= array["maxItems"] <= RootFileService.MAX_OPERATION_ENTRIES
    with pytest.raises(ValidationError):
        definition.validate_arguments({"root": "files", "paths": ["a"] * (array["maxItems"] + 1)})


async def test_file_list_rejects_directories_before_reading(tmp_path):
    call = ToolCall("zip", "provide_file", {"root": "files", "paths": ["a.txt", "folder"]})
    context = _context(tmp_path, call)
    context.root_files.write_file("session", "a.txt", "a")
    context.root_files.create_directory("session", "folder")
    context.root_files.open_file = Mock(side_effect=AssertionError("must not read contents"))

    result = await tool_registry.execute(call, context)

    assert result.is_error
    assert not result.attachments
    context.root_files.open_file.assert_not_called()


@pytest.mark.parametrize("selection", ["folder", "list"])
@pytest.mark.parametrize("deny_source", [False, True])
async def test_computer_pagination_and_authorization_before_reads(tmp_path, selection, deny_source):
    nodes = {
        "/work/reports": None,
        "/work/reports/a.txt": b"public",
        "/work/reports/nested": None,
        "/work/reports/nested/.env": b"confidential",
        "/work/reports/empty": None,
    }
    paths = [path for path, data in nodes.items() if data is not None]
    arguments = {"path": "reports"} if selection == "folder" else {"paths": paths}
    call = ToolCall("zip", "provide_file", {"root": "computer", **arguments})
    context = _context(tmp_path, call)
    client = _computer(context, nodes)
    authorized = set()

    async def authorize(request):
        path = json.loads(request.resource_scope)["path"]
        assert request.capability == "file.provide"
        assert request.action_kind == "provide_file"
        assert request.available_decisions == ("allow_once", "allow_session", "deny")
        if deny_source and (selection == "folder" or path.endswith("/.env")):
            return PermissionDecision.DENY
        authorized.add(path)
        return PermissionDecision.ALLOW

    async def open_export(path, *, limit):
        assert "/work/reports" in authorized or path in authorized
        assert len(nodes[path]) <= limit
        return BytesIO(nodes[path])

    context.ask_handler.side_effect = authorize
    client.open_export.side_effect = open_export

    result = await tool_registry.execute(call, context)

    if deny_source:
        assert result.is_error
        assert result.metadata["errorCode"] == "permission.denied"
        assert not result.attachments
        client.open_export.assert_not_awaited()
    else:
        contents = _zip_contents(result, "reports.zip" if selection == "folder" else "download.zip", context)
        prefix = "reports/" if selection == "folder" else ""
        assert {name: data for name, data in contents.items() if not name.endswith("/")} == {
            prefix + "a.txt": b"public", prefix + "nested/.env": b"confidential",
        }
        assert client.open_export.await_count == 2
        if selection == "folder":
            assert contents["reports/empty/"] == b""
    if selection == "folder" and not deny_source:
        offsets = [item.args[1].get("offset", 0) for item in client.request.await_args_list
                   if item.args[0] == "filesystem.list" and item.args[1]["path"] == "/work/reports"]
        assert offsets == [0, 1, 2]


async def test_computer_rejects_empty_single_file_before_import(tmp_path):
    call = ToolCall("provide", "provide_file", {"root": "computer", "path": "empty.txt"})
    context = _context(tmp_path, call)
    client = _computer(context, {"/work/empty.txt": b""})
    context.root_files.create_output_stream = Mock(
        side_effect=AssertionError("must not create an output")
    )

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "attachment.empty"}
    client.open_export.assert_not_awaited()
    context.root_files.create_output_stream.assert_not_called()


@pytest.mark.parametrize("selection", ["zip", "single"])
async def test_cancelled_import_discards_only_the_new_run_output(
    tmp_path, monkeypatch, selection
):
    path = "reports" if selection == "zip" else "report.txt"
    call = ToolCall("provide", "provide_file", {"root": "computer", "path": path})
    context = _context(tmp_path, call)
    nodes = (
        {"/work/reports": None, "/work/reports/a.txt": b"a"}
        if selection == "zip"
        else {"/work/report.txt": b"report"}
    )
    client = _computer(context, nodes)
    source = BytesIO(b"report")
    if selection == "single":
        client.open_export.return_value = source
        client.open_export.side_effect = None
    context.root_files.write_file("session", "keep.txt", "keep")
    created, release = Event(), Event()
    archives = []
    create_output = RootFileService.create_output_stream
    create_archive = tools_module.create_zip_archive

    def blocked_create(files, *args, **kwargs):
        revision = create_output(files, *args, **kwargs)
        created.set()
        assert release.wait(5)
        return revision

    async def capture_archive(*args, **kwargs):
        archive = await create_archive(*args, **kwargs)
        archives.append(archive)
        return archive

    monkeypatch.setattr(RootFileService, "create_output_stream", blocked_create)
    monkeypatch.setattr(tools_module, "create_zip_archive", capture_archive)
    task = asyncio.create_task(tool_registry.execute(call, context))
    try:
        assert await asyncio.to_thread(created.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    if selection == "zip":
        assert archives[0].content.closed
    else:
        assert source.closed
    assert [entry.path for entry in context.root_files.list_entries("session").entries] == [
        "keep.txt"
    ]
    assert [index["path"] for _, index in context.root_files._revision_indexes()] == [
        "keep.txt"
    ]


@pytest.mark.parametrize(("entry_path", "kind"), [
    ("/outside/a.txt", "file"),
    ("/work/reports-other/a.txt", "file"),
    ("/work/reports/nested/a.txt", "file"),
    ("/work/reports/b.txt", "file"),
    ("/work/reports/a.txt", "symlink"),
    ("/work/reports/a.txt", "other"),
])
async def test_computer_rejects_unsafe_listing_before_reads(tmp_path, entry_path, kind):
    call = ToolCall("zip", "provide_file", {"root": "computer", "path": "reports"})
    context = _context(tmp_path, call)
    client = _computer(context, {"/work/reports": None})
    stat_request = client.request.side_effect

    async def request(operation, arguments, result_model):
        if operation == "filesystem.stat":
            return await stat_request(operation, arguments, result_model)
        assert operation == "filesystem.list"
        return WorkspaceListResult.model_validate({
            "entries": [{"name": "a.txt", "path": entry_path, "kind": kind,
                         "size": 1, "modifiedAt": 0.0}],
            "hasMore": False,
        })

    client.request.side_effect = request

    result = await tool_registry.execute(call, context)

    assert result.is_error
    assert not result.attachments
    client.open_export.assert_not_awaited()


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
async def test_managed_zip_rejects_symlinks_and_special_files(tmp_path, kind):
    call = ToolCall("zip", "provide_file", {"root": "files", "path": "reports"})
    context = _context(tmp_path, call)
    context.root_files.create_directory("session", "reports")
    context.root_files.write_file("session", "secret.txt", "confidential")
    path = context.root_files.resolve("session", "reports") / "unsafe"
    if kind == "symlink":
        path.symlink_to(context.root_files.resolve("session", "secret.txt"))
    else:
        os.mkfifo(path)
    context.root_files.open_file = Mock(side_effect=AssertionError("must not open unsafe entry"))

    result = await tool_registry.execute(call, context)

    assert result.is_error
    assert not result.attachments
    context.root_files.open_file.assert_not_called()


@pytest.mark.parametrize("limit", ["source", "entries", "archive"])
async def test_zip_limits_use_small_budgets(tmp_path, monkeypatch, limit):
    call = ToolCall("zip", "provide_file", {"root": "files", "path": "reports"})
    context = _context(tmp_path, call)
    context.root_files.create_directory("session", "reports")
    for path in ("a.txt", "b.txt"):
        context.root_files.write_file("session", f"reports/{path}", "12345678")
    if limit == "source":
        monkeypatch.setattr(tools_module, "MAX_EXPORT_BYTES", 12)
    elif limit == "entries":
        monkeypatch.setattr(tools_module, "MAX_EXPORT_ENTRIES", 1)
    else:
        monkeypatch.setattr(tools_module, "MAX_EXPORT_BYTES", 64)

    result = await tool_registry.execute(call, context)

    assert result.is_error
    assert not result.attachments


@pytest.mark.parametrize("replacement", ["symlink", "fifo"])
async def test_zip_rejects_file_replaced_after_listing(tmp_path, monkeypatch, replacement):
    call = ToolCall("zip", "provide_file", {"root": "files", "path": "reports"})
    context = _context(tmp_path, call)
    context.root_files.create_directory("session", "reports")
    context.root_files.write_file("session", "reports/a.txt", "public")
    source = context.root_files.resolve("session", "reports/a.txt")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    original_open = os.open

    def replace_before_open(path, flags, *args, **kwargs):
        if path == "a.txt":
            source.unlink()
            if replacement == "symlink":
                source.symlink_to(secret)
            else:
                assert flags & os.O_NONBLOCK, "FIFO rejection must not block"
                os.mkfifo(source)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_before_open)
    result = await tool_registry.execute(call, context)

    assert result.is_error
    assert not result.attachments


async def test_archive_streams_a_simulated_source_above_64_mib_in_bounded_chunks():
    class Source(BytesIO):
        def read(self, size=-1):
            assert 0 < size <= archive_module.EXPORT_CHUNK_BYTES
            return super().read(size)

    source = Source(bytes(range(128)) * 16_384)

    async def stat(_path):
        return ArchiveEntry("large.bin", "large.bin", "file", 65 * 1024 * 1024)

    async def unused_list(_path, _offset):
        raise AssertionError("file selection must not list")

    async def open_file(_path, _limit):
        return source

    result = await archive_module.create_zip_archive(
        ["large.bin"], stat_path=stat, list_directory=unused_list,
        open_file=open_file, file_list=True, max_source_bytes=66 * 1024 * 1024,
    )
    try:
        assert result.size > 0
        assert source.closed
    finally:
        result.content.close()


async def test_archive_limit_failure_closes_source_and_temporary_output(monkeypatch):
    source = BytesIO(bytes(range(256)) * 16)
    outputs = []

    class Output(BytesIO):
        closed_by_archive = False

        def close(self):
            self.closed_by_archive = True
            super().close()

    def temporary(**_kwargs):
        output = Output()
        outputs.append(output)
        return output

    monkeypatch.setattr(archive_module, "SpooledTemporaryFile", temporary)

    async def stat(_path):
        return ArchiveEntry("data.bin", "data.bin", "file", len(source.getvalue()))

    async def open_file(_path, _limit):
        return source

    with pytest.raises(FileArchiveError, match="file.operationTooLarge"):
        await archive_module.create_zip_archive(
            ["data.bin"], stat_path=stat,
            list_directory=lambda *_args: ArchivePage((), False),
            open_file=open_file, file_list=True, max_output_bytes=32,
        )
    assert source.closed
    assert outputs[0].closed_by_archive


@pytest.mark.parametrize("read_fails", [False, True])
async def test_archive_cancellation_waits_for_read_and_closes_handles(
    monkeypatch, read_fails
):
    started, release = Event(), Event()
    outputs = []

    class Source(BytesIO):
        def read(self, size=-1):
            started.set()
            assert release.wait(5)
            if read_fails:
                raise RuntimeError("read")
            return super().read(size)

    class Output(BytesIO):
        closed_by_archive = False

        def close(self):
            self.closed_by_archive = True
            super().close()

    source = Source(b"content")
    monkeypatch.setattr(
        archive_module,
        "SpooledTemporaryFile",
        lambda **_kwargs: outputs.append(Output()) or outputs[-1],
    )

    async def stat(_path):
        return ArchiveEntry("data.bin", "data.bin", "file", len(source.getvalue()))

    async def open_file(_path, _limit):
        return source

    task = asyncio.create_task(
        archive_module.create_zip_archive(
            ["data.bin"],
            stat_path=stat,
            list_directory=lambda *_args: ArchivePage((), False),
            open_file=open_file,
            file_list=True,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert source.closed
    assert outputs[0].closed_by_archive


@pytest.mark.parametrize("open_fails", [False, True])
async def test_archive_cancellation_closes_handle_returned_by_blocked_opener(open_fails):
    started, release = Event(), Event()
    source = BytesIO(b"content")

    async def stat(_path):
        return ArchiveEntry("data.bin", "data.bin", "file", len(source.getvalue()))

    async def open_file(_path, _limit):
        def blocking_open():
            started.set()
            assert release.wait(5)
            if open_fails:
                raise RuntimeError("open")
            return source

        return await asyncio.to_thread(blocking_open)

    task = asyncio.create_task(
        archive_module.create_zip_archive(
            ["data.bin"],
            stat_path=stat,
            list_directory=lambda *_args: ArchivePage((), False),
            open_file=open_file,
            file_list=True,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert source.closed is not open_fails


@pytest.mark.parametrize("failure", ["constructor", "close"])
async def test_archive_closes_output_when_zip_lifecycle_fails(monkeypatch, failure):
    outputs = []

    class Output(BytesIO):
        closed_by_archive = False

        def close(self):
            self.closed_by_archive = True
            super().close()

    monkeypatch.setattr(
        archive_module,
        "SpooledTemporaryFile",
        lambda **_kwargs: outputs.append(Output()) or outputs[-1],
    )
    original = archive_module.zipfile.ZipFile

    if failure == "constructor":
        monkeypatch.setattr(
            archive_module.zipfile,
            "ZipFile",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("constructor")),
        )
    else:
        class CloseFailure(original):
            failed = False

            def close(self):
                super().close()
                if not self.failed:
                    self.failed = True
                    raise RuntimeError("close")

        monkeypatch.setattr(archive_module.zipfile, "ZipFile", CloseFailure)

    async def stat(_path):
        return ArchiveEntry("empty", "empty", "directory", None)

    async def list_directory(_path, _offset):
        return ArchivePage((), False)

    with pytest.raises(RuntimeError, match=failure):
        await archive_module.create_zip_archive(
            ["empty"],
            stat_path=stat,
            list_directory=list_directory,
            open_file=lambda *_args: BytesIO(),
        )
    assert outputs[0].closed_by_archive
