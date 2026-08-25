import hashlib
import json
import math
import struct
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from PIL import Image, ImageChops
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.tools import tool_registry
from mato_loop_backend.api.routes.integrations import (
    OpenScadIntegrationRequest,
    create_openscad_integration,
    delete_openscad_integration,
    openscad_settings_payload,
    update_openscad_integration,
)
from mato_loop_backend.execution.client import ExecutionClientError
from mato_loop_backend.integrations import openscad as openscad_module
from mato_loop_backend.integrations.openscad import (
    OPENSCAD_ADAPTER,
    OpenScadConfig,
    OpenScadRenderArguments,
    OpenScadRenderResult,
    _preview_contact_sheet,
    _revision,
    _zip_project,
    render_openscad,
    render_project,
    validate_binary_stl,
    validate_off,
)
from mato_loop_backend.integrations.registry import (
    effective_integration_contributions,
    integration_adapter,
)
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.settings import (
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolSettings,
    UserIntegrationSettings,
)
from mato_loop_backend.workspace import RootFileError, RootFileService


def _instance(agent_id=None) -> IntegrationInstance:
    return IntegrationInstance(
        name="OpenSCAD",
        integration_type="openscad",
        adapter_type="openscad",
        execution_agent_id=agent_id or uuid4(),
        config={"binary_path": "/usr/bin/openscad"},
    )


def _artifact(agent_id, session_id, name, content_type, size=100) -> dict:
    return {
        "artifactId": name,
        "backendId": str(agent_id),
        "sessionId": str(session_id),
        "path": f"generated/{name}",
        "filename": name,
        "size": size,
        "sha256": "a" * 64,
        "contentType": content_type,
    }


def _png(color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", (800, 600), color).save(output, format="PNG")
    return output.getvalue()


def _render_result(
    agent_id,
    session_id,
    output="preview",
    views=("isometric", "front", "right", "top"),
) -> OpenScadRenderResult:
    suffix = "off" if output == "preview" else "stl"
    content_type = "model/vnd.off" if output == "preview" else "model/stl"
    return OpenScadRenderResult.model_validate(
        {
            "output": output,
            "artifact": _artifact(agent_id, session_id, f"model.{suffix}", content_type),
            "previews": (
                [
                    {
                        "view": view,
                        "artifact": _artifact(
                            agent_id,
                            session_id,
                            f"model-{view}.png",
                            "image/png",
                        ),
                    }
                    for view in views
                ]
                if output == "preview"
                else []
            ),
            "diagnostics": "rendered",
            "diagnosticsTruncated": False,
        }
    )


def _tool_context(instance: IntegrationInstance):
    user_id = uuid4()
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = SimpleNamespace(
        execution_agent_id=instance.execution_agent_id,
        execution_operations=[
            "integration.openscad.render",
            "integration.openscad.render6",
            "cache.export",
        ],
    )
    db.scalars.return_value = SimpleNamespace(
        all=lambda: [instance.execution_agent_id]
    )
    return SimpleNamespace(
        db=db,
        run_id=uuid4(),
        session=SimpleNamespace(id=uuid4(), user_id=user_id),
        root_files=RootFileService(uuid4()),
        execution_client=SimpleNamespace(
            agent_id=instance.execution_agent_id,
            operations=frozenset(
                {
                    "integration.openscad.render",
                    "integration.openscad.render6",
                    "cache.export",
                }
            ),
        ),
        bound_options={
            "integration_id": str(instance.id),
            "binding_revision": _revision(instance),
        },
    )


def test_openscad_adapter_requires_render_and_export_and_hides_cli_arguments() -> None:
    assert integration_adapter("openscad", "openscad") is OPENSCAD_ADAPTER
    assert OpenScadConfig(binaryPath="/opt/OpenSCAD/openscad").binary_path.startswith("/")
    with pytest.raises(ValidationError):
        OpenScadConfig(binaryPath="openscad")

    tool = OPENSCAD_ADAPTER.execution_tools[0]
    assert tool.name == "render_openscad"
    assert tool.execution_operation == "integration.openscad.render"
    assert tool.required_execution_operations == ("cache.export",)
    assert [
        action.capability
        for action in tool_registry.integration_resource_actions(_instance())
    ] == ["cad.render"]
    assert set(tool.arguments_model.model_json_schema()["properties"]) == {
        "root",
        "path",
        "projectPath",
        "output",
    }
    properties = tool.arguments_model.model_json_schema()["properties"]
    assert "storage root" in properties["root"]["description"].casefold()
    assert ".scad entrypoint" in properties["path"]["description"]
    assert "every relative dependency" in properties["projectPath"]["description"]

    instance = _instance()
    instance._execution_operations = frozenset({"integration.openscad.render"})
    assert effective_integration_contributions(
        IntegrationSettings(instances=[instance])
    ).tools == ()
    instance._execution_operations = frozenset(
        {"integration.openscad.render", "cache.export"}
    )
    assert [
        item.name
        for item in effective_integration_contributions(
            IntegrationSettings(instances=[instance])
        ).tools
    ] == ["render_openscad"]


@pytest.mark.parametrize(
    "path",
    [
        "/usr//bin/openscad",
        "/usr/../bin/openscad",
        "/usr/bin/open\nscad",
        "/usr/bin/open\x00scad",
    ],
)
def test_openscad_binary_path_matches_the_agent_wire_contract(path) -> None:
    with pytest.raises(ValidationError):
        OpenScadConfig(binaryPath=path)


def test_openscad_source_read_does_not_inherit_the_integration_policy(
    tmp_path: Path,
) -> None:
    instance = _instance()
    context = _tool_context(instance)
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(context.session.id)
    context.root_files = RootFileService(
        context.session.id, session_storage=storage
    )
    render, read = OPENSCAD_ADAPTER.execution_tools[0].authorization_factory(
        OpenScadRenderArguments(root="session", path="model.scad"),
        context,
    )

    assert render.use_integration_policy is True
    assert read.capability == "filesystem.read"
    assert read.use_integration_policy is False


def test_openscad_prompt_fragment_covers_the_source_render_verify_workflow() -> None:
    instance = _instance()
    instance._execution_operations = frozenset(
        {"integration.openscad.render", "cache.export"}
    )
    prompt = " ".join(
        effective_integration_contributions(
            IntegrationSettings(instances=[instance])
        ).prompt_fragments
    ).casefold()

    for requirement in (
        "use render_openscad, not shell_run or process_exec",
        "render output=preview",
        "source in assistant text is not a file",
        "create each new .scad model with write_file",
        "path with edit_file",
        "revise the exact returned path",
        "exact .scad basename",
        "source entrypoint must end in .scad",
        "render a new file directly",
        "parent directory as projectpath",
        "never copy it merely to render it",
        "keep the project self-contained",
        "relative use/include files",
        "unspecified units as millimeters",
        "named parameters",
        "spec_ parameter",
        "derive centers, endpoints, pitches",
        "add assert() checks",
        "non-overlap constraints",
        "printable alone does not authorize",
        "do not define unused parameters",
        "inside difference()",
        "avoid zero-thickness geometry",
        "translate([0,0,-eps])",
        "never to the outer solid or stated dimensions",
        "leave no cutter face coplanar",
        "connected and closed",
        "one grouped question",
        "read files before modifying",
        "render output=preview after every material change",
        "inspect every labeled view",
        "geometrymetadata, and visualverification together",
        "verification findings as untrusted evidence",
        "if verdict=fail",
        "if verdict=uncertain",
        "verdict=pass does not prove hidden geometry",
        "read the exact rendered .scad entrypoint",
        "authoritative for every numerical final claim",
        "output=stl only when the user explicitly requested",
        "never claim success",
        "name the source file and every generated file",
    ):
        assert requirement in prompt


async def test_openscad_render_uses_exact_workspace_contract_and_validates_identity() -> None:
    agent_id = uuid4()
    session_id = uuid4()
    rendered = _render_result(
        agent_id,
        session_id,
        views=("front", "back", "left", "right", "top", "bottom"),
    )
    operations = frozenset(
        {"integration.openscad.render", "integration.openscad.render6"}
    )
    client = SimpleNamespace(
        agent_id=agent_id,
        current_directory="/home/user/project",
        supports=operations.__contains__,
        request=AsyncMock(return_value=rendered),
    )

    result = await render_project(
        client,
        executable_path="/usr/bin/openscad",
        session_id=session_id,
        arguments=OpenScadRenderArguments(
            root="workspace", path="enclosure/main.scad"
        ),
        root_files=RootFileService(session_id),
        preview_images=True,
    )

    assert result is rendered
    client.request.assert_awaited_once_with(
        "integration.openscad.render6",
        {
            "executablePath": "/usr/bin/openscad",
            "sessionId": str(session_id),
            "entrypoint": "main.scad",
                "source": {
                    "type": "workspace",
                    "path": "/home/user/project/enclosure",
                },
                "output": "preview",
                "previewImages": True,
            },
        OpenScadRenderResult,
        timeout=600.0,
    )

    original_path = rendered.previews[1].artifact.path
    rendered.previews[1].artifact.path = rendered.previews[0].artifact.path
    with pytest.raises(ExecutionClientError, match="executionAgent.protocolError"):
        await render_project(
            client,
            executable_path="/usr/bin/openscad",
            session_id=session_id,
            arguments=OpenScadRenderArguments(root="workspace", path="main.scad"),
            root_files=RootFileService(session_id),
            preview_images=True,
        )
    rendered.previews[1].artifact.path = original_path
    rendered.artifact.backend_id = str(uuid4())
    with pytest.raises(ExecutionClientError, match="executionAgent.protocolError"):
        await render_project(
            client,
            executable_path="/usr/bin/openscad",
            session_id=session_id,
            arguments=OpenScadRenderArguments(root="workspace", path="main.scad"),
            root_files=RootFileService(session_id),
            preview_images=True,
        )


async def test_openscad_render_falls_back_to_legacy_views() -> None:
    agent_id = uuid4()
    session_id = uuid4()
    rendered = _render_result(agent_id, session_id)
    operations = frozenset({"integration.openscad.render"})
    client = SimpleNamespace(
        agent_id=agent_id,
        current_directory="/home/user/project",
        supports=operations.__contains__,
        request=AsyncMock(return_value=rendered),
    )

    await render_project(
        client,
        executable_path="/usr/bin/openscad",
        session_id=session_id,
        arguments=OpenScadRenderArguments(root="workspace", path="model.scad"),
        root_files=RootFileService(session_id),
        preview_images=True,
    )

    assert client.request.await_args.args[0] == "integration.openscad.render"


def test_session_project_archive_is_relative_bounded_and_rejects_symlinks(
    tmp_path: Path,
) -> None:
    session_id = uuid4()
    storage = SessionStorage(tmp_path / "sessions")
    root = storage.provision(session_id)
    project = root / "files" / "project"
    project.mkdir()
    (project / "main.scad").write_text("include <part.scad>;", encoding="utf-8")
    (project / "part.scad").write_text("cube(1);", encoding="utf-8")
    service = RootFileService(session_id, session_storage=storage)

    archive, size, digest = _zip_project(service, "project")
    with archive:
        data = archive.read()
    assert len(data) == size
    assert hashlib.sha256(data).hexdigest() == digest
    with zipfile.ZipFile(BytesIO(data)) as zipped:
        assert set(zipped.namelist()) == {"main.scad", "part.scad"}

    (project / "link.scad").symlink_to(project / "part.scad")
    with pytest.raises(RootFileError, match="unsupported"):
        _zip_project(service, "project")


def test_binary_stl_validation_checks_size_and_finite_coordinates() -> None:
    triangle = struct.pack(
        "<12fH",
        0,
        0,
        1,
        1,
        2,
        3,
        5,
        -1,
        7,
        -2,
        4,
        0,
        0,
    )
    valid = bytes(80) + struct.pack("<I", 1) + triangle
    metadata = validate_binary_stl(valid)
    assert metadata == {
        "triangleCount": 1,
        "bounds": {
            "min": {"x": -2.0, "y": -1.0, "z": 0.0},
            "max": {"x": 5.0, "y": 4.0, "z": 7.0},
        },
        "dimensions": {"x": 7.0, "y": 5.0, "z": 7.0},
    }

    with pytest.raises(ValueError, match="structure"):
        validate_binary_stl(valid + b"x")
    invalid = (
        bytes(80)
        + struct.pack("<I", 1)
        + struct.pack("<12fH", math.inf, *(0.0,) * 11, 0)
    )
    with pytest.raises(ValueError, match="non-finite"):
        validate_binary_stl(invalid)


def test_off_validation_checks_structure_indices_and_geometry_metadata() -> None:
    valid = b"OFF 3 1 0\n-2 0 1\n5 4 7\n1 -1 0\n3 0 1 2 66 165 245\n"
    assert validate_off(valid) == {
        "triangleCount": 1,
        "bounds": {
            "min": {"x": -2.0, "y": -1.0, "z": 0.0},
            "max": {"x": 5.0, "y": 4.0, "z": 7.0},
        },
        "dimensions": {"x": 7.0, "y": 5.0, "z": 7.0},
        "componentCount": 1,
        "boundaryEdgeCount": 3,
        "nonManifoldEdgeCount": 0,
    }
    with pytest.raises(ValueError, match="index"):
        validate_off(b"OFF\n3 1 0\n0 0 0\n1 0 0\n0 1 0\n3 0 1 3\n")
    with pytest.raises(ValueError, match="face"):
        validate_off(b"OFF\n3 1 0\n0 0 0\n1 0 0\n0 1 0\n3 0 1 1\n")
    with pytest.raises(ValueError, match="non-finite"):
        validate_off(b"OFF\n3 1 0\n0 0 0\ninf 0 0\n0 1 0\n3 0 1 2\n")
    with pytest.raises(ValueError, match="line exceeds"):
        validate_off(b"OFF " + b" " * (1024 * 1024))


def test_off_validation_reports_components_and_non_manifold_edges() -> None:
    disconnected = (
        b"OFF\n6 2 0\n"
        b"0 0 0\n1 0 0\n0 1 0\n2 0 0\n3 0 0\n2 1 0\n"
        b"3 0 1 2\n3 3 4 5\n"
    )
    metadata = validate_off(disconnected)
    assert metadata["componentCount"] == 2
    assert metadata["boundaryEdgeCount"] == 6
    assert metadata["nonManifoldEdgeCount"] == 0

    non_manifold = (
        b"OFF\n5 3 0\n"
        b"0 0 0\n1 0 0\n0 1 0\n0 -1 0\n0 0 1\n"
        b"3 0 1 2\n3 1 0 3\n3 0 1 4\n"
    )
    metadata = validate_off(non_manifold)
    assert metadata["componentCount"] == 1
    assert metadata["boundaryEdgeCount"] == 6
    assert metadata["nonManifoldEdgeCount"] == 1


def test_preview_contact_sheet_rejects_unexpected_dimensions() -> None:
    output = BytesIO()
    Image.new("RGB", (8, 6), (1, 2, 3)).save(output, format="PNG")
    with pytest.raises(ValueError, match="dimensions"):
        _preview_contact_sheet(
            [(view, output.getvalue()) for view in ("isometric", "front", "right", "top")]
        )


async def test_render_tool_returns_preview_and_geometry_metadata(
    monkeypatch,
) -> None:
    instance = _instance()
    context = _tool_context(instance)
    views = ("front", "back", "left", "right", "top", "bottom")
    rendered = _render_result(
        instance.execution_agent_id, context.session.id, views=views
    )
    off = b"OFF\n3 1 0\n0 0 0\n2 0 0\n0 3 4\n3 0 1 2\n"
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(return_value=IntegrationSettings(instances=[instance])),
    )
    monkeypatch.setattr(openscad_module, "render_project", AsyncMock(return_value=rendered))
    colors = [
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (255, 255, 0),
        (255, 0, 255),
        (0, 255, 255),
    ]
    exported = AsyncMock(side_effect=[off, *(_png(color) for color in colors)])
    monkeypatch.setattr(openscad_module, "export_artifact", exported)

    result = await render_openscad(
        OpenScadRenderArguments(root="workspace", path="model.scad"), context
    )

    assert result.is_error is False
    openscad_module.render_project.assert_awaited_once_with(
        context.execution_client,
        executable_path="/usr/bin/openscad",
        session_id=context.session.id,
        arguments=OpenScadRenderArguments(root="workspace", path="model.scad"),
        root_files=context.root_files,
        preview_images=True,
    )
    assert [attachment.kind for attachment in result.attachments] == ["file", "image"]
    assert [attachment.filename for attachment in result.attachments] == [
        "model.off",
        "model-views.png",
    ]
    assert json.loads(result.content)["geometryMetadata"] == {
        "triangleCount": 1,
        "bounds": {
            "min": {"x": 0.0, "y": 0.0, "z": 0.0},
            "max": {"x": 2.0, "y": 3.0, "z": 4.0},
        },
        "dimensions": {"x": 2.0, "y": 3.0, "z": 4.0},
        "componentCount": 1,
        "boundaryEdgeCount": 3,
        "nonManifoldEdgeCount": 0,
    }
    payload = json.loads(result.content)
    assert payload["previewLayout"] == list(views)
    assert exported.await_count == 7
    with Image.open(BytesIO(result.attachments[1].data)) as sheet:
        assert sheet.size == (1600, 1920)
        assert [
            sheet.getpixel(point)
            for point in (
                (400, 340),
                (1200, 340),
                (400, 980),
                (1200, 980),
                (400, 1620),
                (1200, 1620),
            )
        ] == colors
        label = sheet.crop((0, 0, 800, 40))
        assert ImageChops.difference(label, Image.new("RGB", label.size, "white")).getbbox()


async def test_render_tool_returns_generated_attachments(
    monkeypatch, tmp_path: Path
) -> None:
    instance = _instance()
    context = _tool_context(instance)
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(context.session.id)
    context.root_files = RootFileService(
        context.session.id,
        session_storage=storage,
    )
    context.root_files.create_file(
        "session", "model.scad", BytesIO(b"cube(1);\n"), max_bytes=100
    )
    rendered = _render_result(instance.execution_agent_id, context.session.id)
    off = b"OFF\n3 1 0\n0 0 0\n2 0 0\n0 3 4\n3 0 1 2\n"
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(return_value=IntegrationSettings(instances=[instance])),
    )
    monkeypatch.setattr(openscad_module, "render_project", AsyncMock(return_value=rendered))
    monkeypatch.setattr(
        openscad_module,
        "export_artifact",
        AsyncMock(side_effect=[off, *(_png((1, 2, 3)) for _ in rendered.previews)]),
    )

    result = await render_openscad(
        OpenScadRenderArguments(
            root="session",
            path="model.scad",
            projectPath=".",
        ),
        context,
    )

    assert len(result.attachments) == 2
    assert result.attachments[0].mime_type == "model/vnd.off"
    assert result.attachments[1].mime_type == "image/png"


async def test_render_tool_exposes_only_controlled_syntax_diagnostics(
    monkeypatch,
) -> None:
    instance = _instance()
    context = _tool_context(instance)
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(return_value=IntegrationSettings(instances=[instance])),
    )
    monkeypatch.setattr(
        openscad_module,
        "render_project",
        AsyncMock(
            side_effect=ExecutionClientError(
                "openscad.renderFailed",
                {
                    "diagnostics": "ERROR: syntax error in model.scad, line 4",
                    "diagnosticsTruncated": False,
                    "rawException": "must not escape",
                },
            )
        ),
    )

    result = await render_openscad(
        OpenScadRenderArguments(root="workspace", path="model.scad"), context
    )
    error = json.loads(result.content)["error"]

    assert result.is_error is True
    assert error["code"] == "openscad.renderFailed"
    assert error["params"] == {
        "diagnostics": "ERROR: syntax error in model.scad, line 4",
        "diagnosticsTruncated": False,
    }


async def test_render_tool_rejects_oversized_artifact_before_export(
    monkeypatch,
) -> None:
    instance = _instance()
    context = _tool_context(instance)
    rendered = _render_result(instance.execution_agent_id, context.session.id)
    rendered.artifact.size = 65 * 1024 * 1024
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(return_value=IntegrationSettings(instances=[instance])),
    )
    monkeypatch.setattr(openscad_module, "render_project", AsyncMock(return_value=rendered))
    exported = AsyncMock()
    monkeypatch.setattr(openscad_module, "export_artifact", exported)

    result = await render_openscad(
        OpenScadRenderArguments(root="workspace", path="model.scad"), context
    )

    assert result.metadata == {"errorCode": "integration.attachmentTooLarge"}
    exported.assert_not_awaited()


async def test_openscad_crud_payload_and_ownership_shape(monkeypatch) -> None:
    settings = UserIntegrationSettings()
    instance_settings = IntegrationSettings()
    tools = ToolSettings()
    agent_id = uuid4()
    user = SimpleNamespace(id=uuid4())
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent_id
    db.scalars.return_value = SimpleNamespace(all=lambda: [agent_id])

    async def load(_db, settings_type, **_kwargs):
        return (
            instance_settings
            if settings_type is IntegrationSettings
            else settings
            if settings_type is UserIntegrationSettings
            else tools
        )

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    request = OpenScadIntegrationRequest.model_validate(
        {
            "executionAgentId": str(agent_id),
            "config": {"binaryPath": "/usr/bin/openscad"},
        }
    )

    created = await create_openscad_integration(request, user, db)
    integration_id = settings.instances[0].id
    foreign = _instance()
    settings.instances.append(foreign)
    assert created == openscad_settings_payload(settings, {agent_id})
    settings.instances.remove(foreign)
    assert created["instances"][0]["config"] == {
        "binaryPath": "/usr/bin/openscad"
    }

    request.config.binary_path = "/opt/openscad"
    updated = await update_openscad_integration(integration_id, request, user, db)
    assert updated["instances"][0]["config"] == {"binaryPath": "/opt/openscad"}
    await delete_openscad_integration(integration_id, user, db)
    assert settings.instances == []
    assert db.commit.await_count == 3
