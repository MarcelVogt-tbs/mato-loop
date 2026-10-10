import io
import stat
import zipfile
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from skill_helpers import store_skill
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import register_error_handlers
from mato_loop_backend.api.routes import skills as skill_routes
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.models.setting import SettingRecord
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    DEFAULT_SKILLS,
    WEATHER_SKILL_ID,
    InstanceSkillsSettings,
    SettingsStore,
    SkillDefinition,
    SkillsSettings,
)
from mato_loop_backend.skill_migration import initialize_skill_library
from mato_loop_backend.skill_packages import (
    SkillError,
    SkillFile,
    SkillPackage,
    parse_skill_md,
    skill_md,
)
from mato_loop_backend.skills import SkillService

pytestmark = pytest.mark.usefixtures("skill_library", "mock_db_settings")


def package():
    return SkillPackage({
        "SKILL.md": SkillFile(skill_md("pdf-processing", "Process PDF files.", "Run scripts/extract.py; see references/guide.md.", {"license": "MIT", "metadata": {"vendor": {"hint": "inert"}}})),
        "scripts/extract.py": SkillFile(b"print('done')\n", True),
        "references/guide.md": SkillFile(b"Read this only when needed.\n"),
        "assets/template.bin": SkillFile(b"\x00\xff\x80"),
    })


def test_package_round_trip_keeps_all_files_metadata_and_modes():
    original = package()
    assert SkillPackage.import_file(original.archive(), archive=True) == original


@pytest.mark.parametrize("name", ["../outside", "/absolute", "C:/outside", "x/../../outside", "x\\outside", "x/CON", "x/file.", "a" * 241, "bad\x80file"])
def test_package_rejects_nonportable_or_escaping_paths(name):
    with pytest.raises(SkillError):
        SkillPackage({**package().files, name: SkillFile(b"bad")})


@pytest.mark.parametrize("paths", [
    ["a/" * 17 + "example.txt"],
    [f"d{index}/a/b/example.txt" for index in range(255)],
], ids=["depth", "directory-count"])
def test_import_rejects_trees_the_storage_reader_cannot_read(paths):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("SKILL.md", package().files["SKILL.md"].content)
        for path in paths:
            archive.writestr(path, b"small file")
    with pytest.raises(SkillError, match="file.tooLarge"):
        SkillPackage.import_file(content.getvalue(), archive=True)


def test_import_rejects_links_and_duplicate_members():
    for path, mode in (("pdf-processing/link", stat.S_IFLNK), ("pdf-processing/SKILL.md", stat.S_IFREG)):
        content = io.BytesIO(package().archive())
        with zipfile.ZipFile(content, "a") as archive:
            info = zipfile.ZipInfo(path)
            info.external_attr = mode << 16
            with pytest.warns(UserWarning) if path.endswith("SKILL.md") else nullcontext():
                archive.writestr(info, b"outside")
        with pytest.raises(SkillError):
            SkillPackage.import_file(content.getvalue(), archive=True)


@pytest.mark.parametrize("header", ["name: Bad", "name: con", "name: good\nname: duplicate", "name: good\nx: &a [*a]", "name: good\ndescription: [not, text]"])
def test_invalid_frontmatter_is_controlled(header):
    with pytest.raises(SkillError):
        parse_skill_md(f"---\ndescription: Description\n{header}\n---\nBody")


def test_reference_registry_has_no_inline_content():
    skill = SkillDefinition(name="research", description="Sources", instructions="Check sources.")
    reference = store_skill(skill)
    assert set(reference.model_dump()) == {"id", "name", "revision", "enabled"}
    with pytest.raises(ValidationError):
        SkillsSettings(skills=[skill])


def test_storage_verifies_content_and_rejects_symlinks(skill_library):
    skill_id = uuid4()
    original = package()
    skill_library.publish(None, skill_id, original)
    assert skill_library.read(None, skill_id, original.revision, original.name) == original
    resource = skill_library.root / "global" / str(skill_id) / original.revision / original.name / "references/guide.md"
    resource.unlink()
    resource.symlink_to("/etc/passwd")
    with pytest.raises(SkillError):
        skill_library.read(None, skill_id, original.revision, original.name)


@pytest.mark.parametrize(("old_name", "expected_name"), [("Mein Skill", "mein-skill"), ("CON", "skill-con")])
async def test_migration_preserves_ownership_ids_enabled_and_removes_inline_payload(monkeypatch, skill_library, old_name, expected_name):
    user_id, skill_id = uuid4(), uuid4()
    record = SettingRecord(key="skills", user_id=user_id, data={"skills": [{"id": str(skill_id), "name": old_name, "description": "Beschreibung", "instructions": "Unverändert", "enabled": False}]})
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = SimpleNamespace(all=lambda: [record])
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    await initialize_skill_library(db)
    reference = SkillsSettings.model_validate(record.data).skills[0]
    assert (reference.id, reference.name, reference.enabled) == (skill_id, expected_name, False)
    assert "instructions" not in record.data["skills"][0]
    assert parse_skill_md(skill_library.read(user_id, skill_id, reference.revision, reference.name).files["SKILL.md"].content)[1] == "Unverändert"
    await initialize_skill_library(db)
    assert SkillsSettings.model_validate(record.data).skills[0] == reference


async def test_failed_migration_keeps_original_database_content(monkeypatch):
    original = {"skills": [{"id": str(uuid4()), "name": "Original", "description": "Description", "instructions": "Original"}]}
    record = SettingRecord(key="skills", user_id=uuid4(), data=original)
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = SimpleNamespace(all=lambda: [record])
    from mato_loop_backend.skill_storage import SkillStorage
    monkeypatch.setattr(SkillStorage, "publish", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        await initialize_skill_library(db)
    assert record.data == original
    db.commit.assert_not_awaited()


async def test_migration_retires_builder_settings_without_widening_tool_access(monkeypatch):
    records = [
        SettingRecord(key="model-defaults", data={"skill_creation_model_id": str(uuid4()), "default_model_id": None}),
        SettingRecord(key="model-defaults", user_id=uuid4(), data={"skillCreationModelId": str(uuid4())}),
        SettingRecord(key="tools", data={"tool_availability": {"build_skill": False, "read": True}}),
    ]
    db = AsyncMock(spec=AsyncSession)
    db.scalars.side_effect = [SimpleNamespace(all=lambda: records), SimpleNamespace(all=lambda: [])]
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    await initialize_skill_library(db)
    assert [record.data for record in records] == [{"default_model_id": None}, {}, {"tool_availability": {"save_skill": False, "read": True}}]


async def test_service_conflict_preserves_current_package(monkeypatch):
    owner = uuid4()
    skill = SkillDefinition(name="research", description="Old", instructions="Old")
    settings = SkillsSettings(skills=[store_skill(skill, owner)])
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    db = AsyncMock(spec=AsyncSession)
    replacement = await SkillService.save_package(db, owner, skill.id, SkillPackage({"SKILL.md": SkillFile(skill_md("research", "New", "New"))}), expected_revision=skill.revision)
    with pytest.raises(SkillError, match="skill.changed"):
        await SkillService.save_package(db, owner, skill.id, SkillPackage({"SKILL.md": SkillFile(skill_md("research", "Stale", "Stale"))}), expected_revision=skill.revision)
    assert (await SkillService.package(replacement)).metadata["description"] == "New"
    db.commit.assert_not_awaited()


@pytest.mark.parametrize("admin", [False, True])
async def test_personal_gate_and_global_admin_authority(monkeypatch, admin):
    user = UserAccount(id=uuid4(), username="owner", password_hash="hash", is_admin=admin, is_active=True)
    shared = DEFAULT_SKILLS[WEATHER_SKILL_ID].model_copy(update={"enabled": True})
    own = SkillDefinition(name="own", description="Own", instructions="Personal")
    instance = InstanceSkillsSettings(personal_skills_enabled=False, skills=[store_skill(shared)])
    personal = SkillsSettings(skills=[store_skill(own, user.id)])
    async def load(_db, model, **kwargs):
        return instance if model is InstanceSkillsSettings else personal
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "load_optional", load)
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = db.scalar.return_value = user
    effective = await SkillService.list(db, user.id)
    assert [skill.id for skill in effective] == ([shared.id, own.id] if admin else [shared.id])
    if admin:
        await SkillService.require_write(db, user.id, "instance")
    else:
        with pytest.raises(SkillError, match="auth.adminRequired"):
            await SkillService.require_write(db, user.id, "instance")
        with pytest.raises(SkillError, match="skill.personalDisabled"):
            await SkillService.set_enabled(db, user.id, own.id, False)


async def test_default_personal_override_is_separate(monkeypatch):
    user_id = uuid4()
    personal, instance = SkillsSettings(), InstanceSkillsSettings()
    async def load(_db, model, **kwargs):
        return instance if model is InstanceSkillsSettings else personal
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    skill = await SkillService.set_enabled(AsyncMock(spec=AsyncSession), user_id, WEATHER_SKILL_ID, True)
    assert skill.owner_id == user_id and skill.enabled
    assert not instance.skills[0].enabled


def test_skill_api_uses_package_service_and_requires_admin(monkeypatch):
    user = UserAccount(id=uuid4(), username="test", password_hash="hash", is_admin=False, is_active=True)
    db = AsyncMock(spec=AsyncSession)
    created = SkillDefinition(name="test", description="Test", instructions="Do it")
    save = AsyncMock(return_value=created)
    monkeypatch.setattr(SkillService, "save_package", save)
    async def override_db():
        yield db
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(skill_routes.router, prefix="/api")
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[require_user] = lambda: user
    with TestClient(app) as client:
        response = client.post("/api/skills", json={"document": skill_md("test", "Test", "Do it").decode()})
        denied = client.post("/api/skills/import?scope=instance", files={"file": ("skill.zip", package().archive())})
    assert response.status_code == 201
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "auth.adminRequired")
    save.assert_awaited_once()
    db.commit.assert_awaited_once()


async def test_computer_packages_use_owner_bound_transfers_and_release_every_selected_agent(monkeypatch):
    from uuid import UUID

    from mato_loop_backend.execution.client import ExecutionClient
    from mato_loop_backend.execution.manager import execution_agent_connections
    from mato_loop_backend.execution.protocol import ResponseMessage
    from mato_loop_backend.execution.transfers import transfer_tickets
    from mato_loop_backend.skill_resources import prepare_computer, release_computers

    run = SimpleNamespace(id=uuid4(), session_id=uuid4())
    original = package()
    released = []
    agents = [uuid4(), uuid4()]
    monkeypatch.setattr(execution_agent_connections, "transfer_url", lambda owner, ticket: f"https://backend/{ticket}")

    async def request(owner, operation, arguments, **kwargs):
        assert arguments["sessionId"] == str(run.session_id)
        assert arguments["runId"] == str(run.id)
        if operation == "cache.skills.prepare":
            transfer = arguments["transfer"]
            ticket = transfer_tickets.claim(UUID(transfer["url"].rsplit("/", 1)[1]), transfer["token"], "download", owner_id=owner)
            with ticket.content as source:
                assert SkillPackage.import_file(source.read(), archive=True) == original
            result = {"path": f"/cache/{owner}/pdf-processing"}
        else:
            assert operation == "cache.skills.release"
            released.append(owner)
            result = {}
        return ResponseMessage(type="response", id=uuid4(), ok=True, result=result)

    monkeypatch.setattr(execution_agent_connections, "request", request)
    for agent_id in agents:
        client = ExecutionClient(agent_id, frozenset({"cache.skills.prepare", "cache.skills.release"}))
        assert str(agent_id) in await prepare_computer(client, run, original)
    await release_computers(run)
    assert released == agents
