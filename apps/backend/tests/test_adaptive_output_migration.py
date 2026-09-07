import importlib.util
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa


def test_adaptive_output_migration_updates_only_ai_models_in_all_scopes(monkeypatch) -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/49e718887770_enable_adaptive_model_output_budgets.py"
    )
    spec = importlib.util.spec_from_file_location("adaptive_output_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    metadata = sa.MetaData()
    settings = sa.Table(
        "settings", metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String()),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("data", sa.JSON()),
    )
    model = {
        "model_id": "opaque", "max_output_tokens": 16_384, "maxOutputTokens": 32_768,
        "reasoning_effort": "high", "reasoningSummary": True,
        "capabilities": ["chat", "image_generation"],
        "config": {
            "max_output_tokens": 512, "maxOutputTokens": 1_024,
            "temperature": 0.7, "reasoning_effort": "low", "unknown": {"x": [1, None]},
        },
        "unknown": [True, "preserve", {"max_output_tokens": 777}],
    }
    data = {
        "models": [model, {"maxOutputTokens": 4_096}, {"config": {"maxOutputTokens": 8_192}},
                   {"model_id": "absent"}, {"max_output_tokens": None},
                   {"model_id": "bad-config", "config": ["unchanged"]}, None, 42, "bad", []],
        "providers": [{"config": {"max_output_tokens": 123}}],
        "reasoning": "unchanged", "unknown": {"maxOutputTokens": 999},
    }
    rows = [
        {"id": uuid4(), "key": "ai", "user_id": owner, "data": deepcopy(data)}
        for owner in (None, uuid4(), uuid4())
    ]
    rows += [
        {"id": uuid4(), "key": "ai", "user_id": uuid4(), "data": bad}
        for bad in (None, [], 123, "invalid", {}, {"models": None}, {"models": {"x": model}})
    ]
    rows += [
        {"id": uuid4(), "key": key, "user_id": owner, "data": deepcopy(data)}
        for key in ("tools", "agent-runtime", "general") for owner in (None, uuid4())
    ]
    before = deepcopy(rows)
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(settings.insert(), rows)
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        migration.upgrade()
        actual = list(connection.execute(sa.select(settings)).mappings())
        expected = deepcopy(before)
        for row in expected[:3]:
            for item in row["data"]["models"]:
                if not isinstance(item, dict):
                    continue
                item.pop("maxOutputTokens", None)
                item["max_output_tokens"] = None
                config = item.get("config")
                if isinstance(config, dict) and (
                    "maxOutputTokens" in config or "max_output_tokens" in config
                ):
                    config.pop("maxOutputTokens", None)
                    config["max_output_tokens"] = None
        assert actual == expected
        assert rows == before
        migration.upgrade()
        assert list(connection.execute(sa.select(settings)).mappings()) == expected
        migration.downgrade()
        for row in expected[:3]:
            for item in row["data"]["models"]:
                if isinstance(item, dict):
                    item.pop("max_output_tokens")
                    if isinstance(item.get("config"), dict):
                        item["config"].pop("max_output_tokens", None)
        assert list(connection.execute(sa.select(settings)).mappings()) == expected
    engine.dispose()
