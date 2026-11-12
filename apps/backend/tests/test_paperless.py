import hashlib
import json
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from pypdf import PdfWriter
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.integrations.paperless.adapter as paperless
from mato_loop_backend.models import ChatSession
from mato_loop_backend.permissions.policy import PermissionDecision
from mato_loop_backend.permissions.resolution import tool_execution_policy
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import (
    IntegrationInstance,
    IntegrationSettings,
    ToolExecutionPolicy,
    ToolSettings,
    TrustLevel,
)
from mato_loop_backend.tools.catalog import ToolRegistry
from mato_loop_backend.tools.context import ToolContext


def _account() -> IntegrationInstance:
    return IntegrationInstance(
        name="Archive",
        integration_type="documents",
        adapter_type="paperless",
        config={"base_url": "https://paperless.example.test"},
        credential_id=uuid4(),
        owner_id=uuid4(),
    )


def _revision(account: IntegrationInstance) -> str:
    return json.dumps(account.binding_data(), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _context(account: IntegrationInstance) -> SimpleNamespace:
    integrations = IntegrationSettings(instances=[account])
    return SimpleNamespace(
        bound_options={"integration_id": str(account.id), "binding_revision": _revision(account)},
        integration_settings=integrations,
        current_integrations=AsyncMock(return_value=integrations),
        ask_handler=AsyncMock(return_value=PermissionDecision.ALLOW),
    )


def _mock_client(monkeypatch, client) -> None:
    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client

    monkeypatch.setattr(paperless, "_selected_client", selected_client)


def _permissions(name, arguments, context):
    tool = next(tool for tool in paperless.PAPERLESS_ADAPTER.shared_tools if tool.name == name)
    return tool.authorization_factory(arguments, context)


def _access(name, arguments, context) -> set[str]:
    return {
        grant.access
        for request in _permissions(name, arguments, context)
        for grant in request.effective_session_grants
    }


@pytest.mark.parametrize(
    "model,payload",
    [
        (paperless.UpdateArguments, {"document_id": 7, "changes": {"owner": 9}}),
        (paperless.MetadataSaveArguments, {"kind": "tags", "fields": {"name": "Bills", "set_permissions": {"view": [9]}}}),
        (paperless.MetadataSaveArguments, {"kind": "correspondents", "fields": {"name": "Bank", "path": "injected"}}),
        (paperless.BulkArguments, {"document_ids": [7], "method": "modify_tags", "parameters": {"add_tags": [3], "delete_originals": True}}),
    ],
)
def test_mutations_reject_administrative_or_unsupported_parameters(model, payload) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"integration_id": uuid4(), **payload})


@pytest.mark.parametrize("changes", [{"tags": [3]}, {"custom_fields": [{"field": 2, "value": "paid"}]}])
def test_nonempty_list_replacement_requires_write_and_delete(changes) -> None:
    account = _account()
    arguments = paperless.UpdateArguments(integration_id=account.id, document_id=7, changes=changes)
    assert _access("paperless_update", arguments, _context(account)) == {"write", "delete"}


@pytest.mark.parametrize(
    "method,document_ids,parameters",
    [
        ("merge", [7, 8], {"delete_originals": True}),
        ("edit_pdf", [7], {"operations": [{"page": 1}], "delete_original": True}),
        ("edit_pdf", [7], {"operations": [{"page": 1}], "update_document": True}),
    ],
)
def test_destructive_pdf_operations_require_write_and_delete(method, document_ids, parameters) -> None:
    account = _account()
    arguments = paperless.BulkArguments(
        integration_id=account.id, document_ids=document_ids, method=method, parameters=parameters,
    )
    assert _access("paperless_bulk", arguments, _context(account)) == {"write", "delete"}


def test_notes_trash_and_versions_use_action_specific_categories() -> None:
    account = _account()
    context = _context(account)
    cases = [
        ("paperless_notes", paperless.NotesArguments, {"document_id": 7}, "read"),
        ("paperless_notes", paperless.NotesArguments, {"document_id": 7, "action": "add", "note": "Paid"}, "write"),
        ("paperless_notes", paperless.NotesArguments, {"document_id": 7, "action": "delete", "note_id": 2}, "delete"),
        ("paperless_trash", paperless.TrashArguments, {}, "read"),
        ("paperless_trash", paperless.TrashArguments, {"action": "restore", "document_ids": [7]}, "write"),
        ("paperless_trash", paperless.TrashArguments, {"action": "delete_permanently", "document_ids": [7]}, "delete"),
        ("paperless_version", paperless.VersionArguments, {"document_id": 7, "version_id": 2, "action": "label", "version_label": "Final"}, "write"),
        ("paperless_version", paperless.VersionArguments, {"document_id": 7, "version_id": 2, "action": "delete"}, "delete"),
    ]
    for name, model, payload, category in cases:
        arguments = model(integration_id=account.id, **payload)
        assert _access(name, arguments, context) == {category}, (name, payload)


def test_metadata_deletion_requires_delete_permission() -> None:
    account = _account()
    arguments = paperless.MetadataDeleteArguments(integration_id=account.id, kind="tags", object_id=3)
    assert _access("paperless_delete_metadata", arguments, _context(account)) == {"delete"}


@pytest.mark.parametrize("fields", [{"extra_data": {"select_options": []}}, {"data_type": "string"}])
@pytest.mark.parametrize("object_id", [None, 3])
def test_custom_field_definition_replacement_categories(fields, object_id) -> None:
    account = _account()
    arguments = paperless.MetadataSaveArguments(
        integration_id=account.id, kind="custom_fields", object_id=object_id,
        fields=fields if object_id is not None else {"name": "Status", "data_type": "select", **fields},
    )
    assert _access("paperless_save_metadata", arguments, _context(account)) == (
        {"write"} if object_id is None else {"write", "delete"}
    )


@pytest.mark.parametrize("value,access", [(None, {"write", "delete"}), ("", {"write", "delete"}), ([], {"write", "delete"}), (0, {"write"}), (False, {"write"})])
def test_bulk_custom_field_clearing_categories(value, access) -> None:
    account = _account()
    arguments = paperless.BulkArguments(
        integration_id=account.id, document_ids=[7], method="modify_custom_fields",
        parameters={"add_custom_fields": {"2": value}},
    )
    assert _access("paperless_bulk", arguments, _context(account)) == access


def test_authorization_binds_account_and_revision() -> None:
    account = _account()
    arguments = paperless.UpdateArguments(integration_id=account.id, document_id=7, changes={"title": "Invoice"})
    request, = _permissions("paperless_update", arguments, _context(account))
    scope = json.loads(request.resource_scope)
    grant_scope = json.loads(request.effective_session_grants[0].resource_scope)
    expected_revision = hashlib.sha256(_revision(account).encode()).hexdigest()
    assert request.policy_integration_id == account.id
    assert (scope["integrationId"], scope["bindingRevision"]) == (str(account.id), expected_revision)
    assert (grant_scope["integrationId"], grant_scope["bindingRevision"]) == (str(account.id), expected_revision)


@pytest.mark.parametrize("stale", ["account", "revision"])
async def test_stale_binding_prevents_remote_mutation(monkeypatch, stale) -> None:
    account = _account()
    context = _context(account)
    if stale == "account":
        context.bound_options["integration_id"] = str(uuid4())
    else:
        account.config = {"base_url": "https://changed.example.test"}
    client = AsyncMock()
    _mock_client(monkeypatch, client)
    arguments = paperless.UpdateArguments(integration_id=account.id, document_id=7, changes={"title": "Invoice"})

    with pytest.raises(ValueError):
        _permissions("paperless_update", arguments, context)
    result = await paperless.paperless_execute(arguments, context)

    assert result.metadata["errorCode"] == "integration.staleReference"
    client.update_document.assert_not_awaited()


async def test_classification_update_targets_only_selected_document(monkeypatch) -> None:
    account = _account()
    client = AsyncMock()
    client.update_document.return_value = {"id": 7, "correspondent": 3, "document_type": 4}
    _mock_client(monkeypatch, client)
    arguments = paperless.UpdateArguments(
        integration_id=account.id, document_id=7, changes={"correspondent": 3, "document_type": 4},
    )

    result = await paperless.paperless_execute(arguments, _context(account))

    assert not result.is_error, result.content
    client.update_document.assert_awaited_once_with(7, {"correspondent": 3, "document_type": 4})


@pytest.mark.parametrize("receipt,completed", [({"result": "OK"}, True), ({"result": "OK", "task_id": str(uuid4())}, False)])
async def test_bulk_additive_tags_preserve_unrelated_tags(monkeypatch, receipt, completed) -> None:
    account = _account()
    client = AsyncMock()
    client.request.return_value = receipt
    _mock_client(monkeypatch, client)
    arguments = paperless.BulkArguments(
        integration_id=account.id, document_ids=[7, 12], method="modify_tags", parameters={"add_tags": [3]},
    )

    result = await paperless.paperless_execute(arguments, _context(account))

    assert not result.is_error, result.content
    client.request.assert_awaited_once_with(
        "POST", "documents/bulk_edit/",
        json={"documents": [7, 12], "method": "modify_tags", "parameters": {"add_tags": [3], "remove_tags": []}},
    )
    assert _access("paperless_bulk", arguments, _context(account)) == {"write"}
    assert json.loads(result.content)["completed"] is completed


async def test_search_requests_sparse_document_fields(monkeypatch) -> None:
    account = _account()
    client = AsyncMock()
    client.list.return_value = {"count": 1, "results": [{"id": 7, "title": "Invoice"}]}
    _mock_client(monkeypatch, client)

    result = await paperless.paperless_execute(paperless.SearchArguments(integration_id=account.id), _context(account))

    assert not result.is_error, result.content
    fields = client.list.call_args.kwargs["params"]["fields"].split(",")
    assert "id" in fields and "content" not in fields


async def test_delete_approval_cannot_revive_revoked_write_permission(monkeypatch) -> None:
    account = _account()
    integrations = IntegrationSettings(instances=[account])
    call = ToolCall("update", "paperless_update", {
        "integration_id": str(account.id), "document_id": 7, "changes": {"tags": [3]},
    })
    context = ToolContext(
        db=AsyncMock(spec=AsyncSession), run_id=uuid4(),
        session=ChatSession(id=uuid4(), user_id=account.owner_id), tool_call=call,
        root_files=Mock(), integration_settings=integrations,
        tool_settings=ToolSettings(private_permission_preset="ask_all", public_permission_preset="ask_all"),
        ask_handler=AsyncMock(),
    )
    context.db.scalar.return_value = None
    monkeypatch.setattr(ToolContext, "current_integrations", AsyncMock(return_value=integrations))
    monkeypatch.setattr("mato_loop_backend.tools.access.profile_tool_settings", AsyncMock(return_value=context.tool_settings))

    async def authorize(request, *, check_only=False):
        if not check_only and request.capability == "documents.delete":
            context.tool_settings.permissions.integration_policies[str(account.id)] = {"documents.write": ToolExecutionPolicy.DISABLED}
        if check_only and tool_execution_policy(
            context.tool_settings, request.capability,
            integration_id=request.policy_integration_id, resource_key=request.policy_resource_key,
        ) == ToolExecutionPolicy.DISABLED:
            return PermissionDecision.DENY
        return PermissionDecision.ALLOW

    context.ask_handler.side_effect = authorize
    client = AsyncMock()
    _mock_client(monkeypatch, client)

    result = await ToolRegistry([]).effective(integrations).execute(call, context)

    assert [call.args[0].capability for call in context.ask_handler.call_args_list if not call.kwargs.get("check_only")] == ["documents.write", "documents.delete"]
    assert result.metadata["errorCode"] == "permission.denied"
    client.update_document.assert_not_awaited()


async def test_document_content_pagination_returns_remaining_text(monkeypatch) -> None:
    account = _account()
    client = AsyncMock()
    client.get_document.side_effect = [{"id": 7, "content": "abcdefgh"}, {"id": 7, "content": "abcdefgh"}]
    _mock_client(monkeypatch, client)
    arguments = paperless.ReadArguments(integration_id=account.id, document_id=7, content_offset=2, content_limit=3)

    first = json.loads((await paperless.paperless_execute(arguments, _context(account))).content)
    following = arguments.model_copy(update={"content_offset": first["nextContentOffset"]})
    last = json.loads((await paperless.paperless_execute(following, _context(account))).content)

    assert (first["document"]["content"], first["contentLength"], first["contentOffset"], first["nextContentOffset"]) == ("cde", 8, 2, 5)
    assert (last["document"]["content"], last["contentOffset"], last["nextContentOffset"]) == ("fgh", 5, None)


async def test_task_results_do_not_expose_error_details(monkeypatch) -> None:
    account = _account()
    client = AsyncMock()
    client.list_tasks.return_value = {
        "count": 3,
        "results": [
            {"id": 1, "status": "FAILURE", "result": "private-server-error-token", "traceback": "private-trace"},
            {"id": 2, "status": "SUCCESS", "result": "7"},
            {"id": 3, "status": "SUCCESS", "result": "private-nonnumeric-output"},
        ],
    }
    _mock_client(monkeypatch, client)

    result = await paperless.paperless_execute(paperless.TasksArguments(integration_id=account.id), _context(account))

    assert json.loads(result.content)["results"] == [
        {"id": 1, "status": "FAILURE"},
        {"id": 2, "status": "SUCCESS", "document_id": 7},
        {"id": 3, "status": "SUCCESS"},
    ]


def _file_context(account):
    context = _context(account)
    context.session = SimpleNamespace(id=uuid4(), parent_session_id=None)
    context.root_files = Mock()
    context.root_files.normalize_relative_path.side_effect = lambda path: path
    context.root_files.root_identity.return_value = "session-files"
    context.tool_call = SimpleNamespace(name="paperless_upload")
    return context


async def test_upload_source_read_denial_prevents_remote_call(monkeypatch) -> None:
    account = _account()
    context = _file_context(account)
    client = AsyncMock()
    _mock_client(monkeypatch, client)
    allow = AsyncMock(return_value=False)
    monkeypatch.setattr("mato_loop_backend.tools.access.allow", allow)
    arguments = paperless.UploadArguments(integration_id=account.id, file={"root": "session", "path": "invoice.pdf"})

    result = await paperless.paperless_execute(arguments, context)

    assert result.metadata["errorCode"] == "permission.denied"
    assert allow.call_args.args[1].capability == "session.files.read"
    client.upload.assert_not_awaited()
    context.root_files.read_bytes.assert_not_called()


async def test_download_destination_denial_prevents_remote_call(monkeypatch) -> None:
    account = _account()
    context = _file_context(account)
    client = AsyncMock()
    _mock_client(monkeypatch, client)
    allow = AsyncMock(return_value=False)
    monkeypatch.setattr("mato_loop_backend.tools.access.allow", allow)
    arguments = paperless.DownloadArguments(integration_id=account.id, document_id=7, path="invoice.pdf")

    result = await paperless.paperless_execute(arguments, context)

    assert result.metadata["errorCode"] == "permission.denied"
    assert allow.call_args.args[1].capability == "session.files.write"
    client.download.assert_not_awaited()
    context.root_files.create_file.assert_not_called()


def _approved_file_context(account):
    context = _file_context(account)
    context.run_id = uuid4()
    context.db = AsyncMock()
    context.model_trust_level = TrustLevel.PUBLIC
    context.ask = context.ask_handler
    return context


async def test_upload_reads_ready_pdf_document_attachment(monkeypatch) -> None:
    account = _account()
    context = _approved_file_context(account)
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    data = output.getvalue()
    context.root_files.read_bytes.return_value = data
    attachment = SimpleNamespace(
        metadata_json={}, processing_status="ready", kind="document",
        sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
        mime_type="application/pdf", message=SimpleNamespace(trust_level="public"),
    )
    monkeypatch.setattr(
        "mato_loop_backend.media.files.AttachmentService.resolve_path",
        AsyncMock(return_value=attachment),
    )
    client = AsyncMock()
    client.upload.return_value = "queued-task"
    _mock_client(monkeypatch, client)
    arguments = paperless.UploadArguments(integration_id=account.id, file={"root": "session", "path": "invoice.pdf"})

    await paperless.paperless_execute(arguments, context)

    client.upload.assert_awaited_once_with("invoice.pdf", data, "application/pdf", fields={}, document_id=None)


@pytest.mark.parametrize("transfer", ["upload", "download"])
async def test_file_approval_cannot_revive_revoked_integration_permission(monkeypatch, transfer) -> None:
    account = _account()
    context = _approved_file_context(account)
    context.root_files.read_bytes.return_value = b"document bytes"
    monkeypatch.setattr(
        "mato_loop_backend.media.files.AttachmentService.resolve_path",
        AsyncMock(return_value=None),
    )

    async def authorize(request, *, check_only=False):
        if check_only and request.policy_integration_id == account.id:
            return PermissionDecision.DENY
        return PermissionDecision.ALLOW

    context.ask_handler.side_effect = authorize
    client = AsyncMock()
    _mock_client(monkeypatch, client)
    arguments = (
        paperless.UploadArguments(integration_id=account.id, file={"root": "session", "path": "invoice.pdf"})
        if transfer == "upload" else
        paperless.DownloadArguments(integration_id=account.id, document_id=7, path="invoice.pdf")
    )

    result = await paperless.paperless_execute(arguments, context)

    assert result.metadata["errorCode"] == "permission.denied"
    getattr(client, transfer).assert_not_awaited()
