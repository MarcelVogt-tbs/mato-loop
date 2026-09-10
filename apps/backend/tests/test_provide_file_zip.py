import hashlib
import json
import os
from io import BytesIO
from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
from zipfile import ZipFile

import pytest
from pydantic import ValidationError
from test_agent_tools_contract import _context

import mato_loop_backend.agent.tools as tools_module
from mato_loop_backend.agent.permissions import PermissionDecision
from mato_loop_backend.agent.tools import ToolAttachment, tool_registry
from mato_loop_backend.execution.client import WorkspaceListResult, WorkspaceStatResult
from mato_loop_backend.providers import ToolCall
from mato_loop_backend.workspace import RootFileService


def _zip_contents(result, filename):
    assert not result.is_error, result.content
    assert len(result.attachments) == 1
    attachment = result.attachments[0]
    assert isinstance(attachment, ToolAttachment)
    assert (attachment.kind, attachment.mime_type, attachment.filename) == (
        "file", "application/zip", filename
    )
    assert attachment.file_id is None
    assert attachment.file_revision is None
    payload = json.loads(result.content)
    assert payload["filename"] == filename
    assert payload["sizeBytes"] == len(attachment.data)
    assert payload["sha256"] == hashlib.sha256(attachment.data).hexdigest()
    with ZipFile(BytesIO(attachment.data)) as archive:
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
    context.execution_client = SimpleNamespace(
        agent_id=uuid4(), current_directory="/work", supports=lambda _: True,
        request=AsyncMock(side_effect=request),
        read_bytes=AsyncMock(side_effect=lambda path, *, limit: nodes[path][:limit + 1]),
    )
    context.root_files.open_file = Mock(side_effect=AssertionError("must not read locally"))
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

    contents = _zip_contents(result, "reports.zip" if selection == "folder" else "download.zip")
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

    assert _zip_contents(result, "download.zip") == ({} if empty else {"a.txt": b"a"})


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
@pytest.mark.parametrize("deny_secret", [False, True])
async def test_computer_pagination_and_authorization_before_reads(tmp_path, selection, deny_secret):
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
        assert request.capability == "filesystem.read"
        assert request.action_kind == "provide_file"
        if path.endswith("/.env"):
            assert request.available_decisions == ("allow_once", "deny")
            if deny_secret:
                return PermissionDecision.DENY
        authorized.add(path)
        return PermissionDecision.ALLOW

    async def read(path, *, limit):
        assert set(paths) <= authorized
        assert len(nodes[path]) <= limit
        return nodes[path]

    context.ask_handler.side_effect = authorize
    client.read_bytes.side_effect = read

    result = await tool_registry.execute(call, context)

    if deny_secret:
        assert result.is_error
        assert result.metadata["errorCode"] == "permission.denied"
        assert not result.attachments
        client.read_bytes.assert_not_awaited()
    else:
        contents = _zip_contents(result, "reports.zip" if selection == "folder" else "download.zip")
        prefix = "reports/" if selection == "folder" else ""
        assert {name: data for name, data in contents.items() if not name.endswith("/")} == {
            prefix + "a.txt": b"public", prefix + "nested/.env": b"confidential",
        }
        assert client.read_bytes.await_count == 2
        if selection == "folder":
            assert contents["reports/empty/"] == b""
    if selection == "folder" and not deny_secret:
        offsets = [item.args[1].get("offset", 0) for item in client.request.await_args_list
                   if item.args[0] == "filesystem.list" and item.args[1]["path"] == "/work/reports"]
        assert offsets == [0, 1, 2]
    context.root_files.open_file.assert_not_called()


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
    client.read_bytes.assert_not_awaited()


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
        monkeypatch.setattr(RootFileService, "MAX_OPERATION_BYTES", 12)
    elif limit == "entries":
        monkeypatch.setattr(RootFileService, "MAX_OPERATION_ENTRIES", 1)
    else:
        monkeypatch.setattr(tools_module, "MAX_FILE_ATTACHMENT_BYTES", 64)

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
