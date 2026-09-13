from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.routes import skills as skill_routes
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    DEFAULT_SKILLS,
    WEATHER_SKILL_ID,
    InstanceSkillsSettings,
    SettingsScope,
    SettingsStore,
    SkillDefinition,
    SkillsSettings,
    load_effective_skills,
)
from mato_loop_backend.skills import (
    SkillDraft,
    SkillEditPatch,
    SkillError,
    SkillService,
    parse_skill_draft,
    serialize_skill_draft,
)


def _draft(name: str = "Recherche") -> SkillDraft:
    return SkillDraft(
        name=name,
        description="Sucht belastbare Quellen.",
        instructions="Pruefe Aussagen anhand aktueller Quellen.",
    )


def test_skill_settings_validate_and_normalize_definitions() -> None:
    skill = SkillDefinition(
        name="  Recherche  ",
        description="  Sucht Quellen.  ",
        instructions="  Pruefe die Quellen.  ",
    )

    assert SkillsSettings.settings_scope == SettingsScope.USER
    assert InstanceSkillsSettings.settings_scope == SettingsScope.GLOBAL
    assert skill.enabled is True
    assert (skill.name, skill.description, skill.instructions) == (
        "Recherche",
        "Sucht Quellen.",
        "Pruefe die Quellen.",
    )
    for values in (
        {"name": " "},
        {"name": "Bad\u202ename"},
        {"description": " "},
        {"instructions": " "},
        {"name": "x" * 65},
    ):
        with pytest.raises(ValidationError):
            SkillDefinition(
                name=values.get("name", "Name"),
                description=values.get("description", "Description"),
                instructions=values.get("instructions", "Instructions"),
            )
    with pytest.raises(ValidationError):
        SkillsSettings(skills=[skill.model_copy() for _ in range(21)])


def test_instance_settings_add_new_shipped_defaults(monkeypatch) -> None:
    existing = SkillDefinition(
        name="Existing", description="Already stored", instructions="Keep this skill."
    )
    shipped = SkillDefinition(
        name="Standard", description="Ausgeliefert", instructions="Arbeite so.", enabled=False
    )
    monkeypatch.setitem(DEFAULT_SKILLS, shipped.id, shipped)

    settings = InstanceSkillsSettings(skills=[existing])

    assert [skill.id for skill in settings.skills] == [existing.id, *DEFAULT_SKILLS]
    assert all(
        skill is not DEFAULT_SKILLS[skill.id]
        for skill in settings.skills
        if skill.id in DEFAULT_SKILLS
    )


def test_shipped_weather_skill_is_disabled_and_model_facing() -> None:
    weather = DEFAULT_SKILLS[WEATHER_SKILL_ID]

    assert weather.name == "Weather"
    assert weather.enabled is False
    assert "Do not use for translation" in weather.description
    assert "geocoding-api.open-meteo.com" in weather.instructions
    assert "Percent-encode the complete place value" in weather.instructions
    assert "api.open-meteo.com/v1/forecast" in weather.instructions
    assert "Markdown table" in weather.instructions
    assert "maximum hourly probability" in weather.instructions


def test_skill_draft_json_is_strict_stable_and_controlled() -> None:
    draft = _draft("Uebersicht")
    serialized = serialize_skill_draft(draft)

    assert serialized.startswith('{\n  "name": "Uebersicht"')
    assert serialized.endswith("\n")
    assert parse_skill_draft(serialized) == draft
    for value in (
        '{"name":"x","description":"y","instructions":"z","extra":true}',
        '{"name":1,"description":"y","instructions":"z"}',
        "not-json",
    ):
        with pytest.raises(SkillError, match="skill.draftInvalid") as exc_info:
            parse_skill_draft(value)
        assert exc_info.value.code == "skill.draftInvalid"


def test_skill_edit_patch_is_partial_strict_and_requires_a_change() -> None:
    assert SkillEditPatch(description="  Neue Beschreibung  ").model_dump() == {
        "name": None,
        "description": "Neue Beschreibung",
        "instructions": None,
    }
    for value in (
        {},
        {"description": None},
        {"description": " "},
        {"description": "Neu", "extra": True},
    ):
        with pytest.raises(ValidationError):
            SkillEditPatch.model_validate(value)


async def test_skill_service_locks_mutations_preserves_id_and_does_not_commit(
    monkeypatch,
) -> None:
    user_id = uuid4()
    existing = SkillDefinition(
        name="Recherche", description="Alt", instructions="Alte Anweisung"
    )
    settings = SkillsSettings(skills=[existing])
    load = AsyncMock(return_value=settings)
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "save", save)
    db = AsyncMock(spec=AsyncSession)

    replacement = await SkillService.replace(
        db,
        user_id,
        existing.id,
        _draft("Quellenpruefung"),
        expected=existing,
    )

    assert replacement.id == existing.id
    assert replacement.enabled is True
    assert load.await_args.kwargs == {"user_id": user_id, "for_update": True}
    save.assert_awaited_once_with(db, settings, user_id=user_id)
    db.commit.assert_not_awaited()

    with pytest.raises(SkillError) as exc_info:
        await SkillService.replace(
            db,
            user_id,
            existing.id,
            _draft("Nochmals"),
            expected=existing,
        )

    assert exc_info.value.code == "skill.changed"
    save.assert_awaited_once()


async def test_skill_service_rejects_casefold_duplicate(monkeypatch) -> None:
    settings = SkillsSettings(
        skills=[SkillDefinition(name="STRASSE", description="Alt", instructions="Alt")]
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "save", save)

    with pytest.raises(SkillError) as exc_info:
        await SkillService.create(AsyncMock(spec=AsyncSession), uuid4(), _draft("strasse"))

    assert exc_info.value.code == "skill.duplicate"
    save.assert_not_awaited()


async def test_skill_delete_is_blocked_while_automation_references_it(
    monkeypatch,
) -> None:
    user_id = uuid4()
    skill = SkillDefinition(name="Bericht", description="Erstellt Berichte", instructions="Pruefe Daten")
    settings = SkillsSettings(skills=[skill])
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "save", save)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = uuid4()

    with pytest.raises(SkillError) as exc_info:
        await SkillService.delete(db, user_id, skill.id)

    assert exc_info.value.code == "skill.inUse"
    save.assert_not_awaited()


def test_authenticated_skill_api_creates_and_commits(monkeypatch) -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    created = SkillDefinition(**_draft().model_dump())
    create = AsyncMock(return_value=created)
    monkeypatch.setattr(SkillService, "create", create)
    monkeypatch.setattr(SkillService, "list", AsyncMock(return_value=[created]))

    async def override_db():
        yield db

    app = FastAPI()
    app.include_router(skill_routes.router, prefix="/api")
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[require_user] = lambda: user

    with TestClient(app) as client:
        listed = client.get("/api/skills")
        response = client.post("/api/skills", json=_draft().model_dump())

    assert listed.json() == {"items": [{
        **created.model_dump(mode="json"),
        "scope": "instance",
        "isDefault": False,
        "modified": False,
    }]}
    assert response.status_code == 201
    assert response.json()["id"] == str(created.id)
    create.assert_awaited_once()
    assert create.await_args.args[:2] == (db, user.id)
    db.commit.assert_awaited_once()


async def test_effective_skills_merge_scopes_and_hide_disabled(monkeypatch) -> None:
    user_id = uuid4()
    instance = SkillDefinition(
        name="Instanz", description="Global", instructions="Global", enabled=True
    )
    personal = SkillDefinition(
        name="Persoenlich", description="Privat", instructions="Privat", enabled=False
    )

    monkeypatch.setattr(
        SettingsStore, "load", AsyncMock(return_value=SkillsSettings(skills=[personal]))
    )
    monkeypatch.setattr(
        SettingsStore,
        "load_optional",
        AsyncMock(return_value=InstanceSkillsSettings(skills=[instance])),
    )
    skills = await load_effective_skills(
        AsyncMock(spec=AsyncSession), user_id, enabled_only=True
    )

    assert [skill.id for skill in skills] == [instance.id]
    assert skills[0].owner_id is None


async def test_personal_default_activation_creates_an_override(monkeypatch) -> None:
    user_id = uuid4()
    shipped = SkillDefinition(
        name="Standard", description="Ausgeliefert", instructions="Arbeite so.", enabled=False
    )
    monkeypatch.setitem(DEFAULT_SKILLS, shipped.id, shipped)
    personal = SkillsSettings()
    instance = InstanceSkillsSettings()

    async def load(_db, settings_type, **_kwargs):
        return instance if settings_type is InstanceSkillsSettings else personal

    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "save", save)
    db = AsyncMock(spec=AsyncSession)

    skill = await SkillService.set_enabled(
        db, user_id, shipped.id, True
    )

    assert skill.enabled is True
    assert personal.skills == [skill]
    assert next(skill for skill in instance.skills if skill.id == shipped.id).enabled is False
    save.assert_awaited_once_with(db, personal, user_id=user_id)


async def test_personal_reset_materializes_the_shipped_default(monkeypatch) -> None:
    user_id = uuid4()
    shipped = SkillDefinition(
        name="Standard", description="Ausgeliefert", instructions="Arbeite so.", enabled=False
    )
    monkeypatch.setitem(DEFAULT_SKILLS, shipped.id, shipped)
    personal = SkillsSettings()
    instance = InstanceSkillsSettings(
        skills=[shipped.model_copy(update={"description": "Instanzweit geändert", "enabled": True})]
    )

    async def load(_db, settings_type, **_kwargs):
        return instance if settings_type is InstanceSkillsSettings else personal

    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())

    reset = await SkillService.reset(
        AsyncMock(spec=AsyncSession), user_id, shipped.id
    )

    assert reset.description == shipped.description
    assert reset.enabled is True
    assert personal.skills == [reset]


def test_instance_skill_api_requires_admin(monkeypatch) -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(SkillService, "list_scoped", AsyncMock(return_value=[]))

    async def override_db():
        yield db

    app = FastAPI()
    app.include_router(skill_routes.router, prefix="/api")
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[require_user] = lambda: user

    with TestClient(app) as client:
        response = client.get("/api/skills?scope=instance")

    assert response.status_code == 403
    assert response.json()["detail"] == "auth.adminRequired"
