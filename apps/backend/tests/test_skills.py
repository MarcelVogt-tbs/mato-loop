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
    SettingsScope,
    SettingsStore,
    SkillDefinition,
    SkillsSettings,
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

    assert listed.json() == {"items": [created.model_dump(mode="json")]}
    assert response.status_code == 201
    assert response.json()["id"] == str(created.id)
    create.assert_awaited_once()
    assert create.await_args.args[:2] == (db, user.id)
    db.commit.assert_awaited_once()
