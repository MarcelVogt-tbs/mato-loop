import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

import mato_loop_backend.integrations.mealie as mealie_module
import mato_loop_backend.integrations.service as service_module
from mato_loop_backend.integrations.mealie import (
    MEALIE_ADAPTER,
    MEALIE_CREDENTIAL_KIND,
    MealieConfig,
    MealieListAccountsArguments,
    MealPlanAddArguments,
    MealPlanDeleteArguments,
    MealPlanGetArguments,
    MealPlanUpdateArguments,
    RecipeGetArguments,
    RecipesSearchArguments,
    meal_plan_add,
    meal_plan_delete,
    meal_plan_get,
    meal_plan_update,
    mealie_list_accounts,
    recipe_get,
    recipes_search,
)
from mato_loop_backend.integrations.mealie_client import (
    MealieError,
    MealieMealPlanEntry,
    MealieRecipe,
    MealieRecipeIngredient,
    MealieRecipeInstruction,
    MealieRecipeSummary,
)
from mato_loop_backend.integrations.service import MealieIntegrationService
from mato_loop_backend.settings import (
    IntegrationInstance,
    IntegrationSettings,
    ToolExecutionPolicy,
)

RECIPE_ID = "12345678-1234-5678-9234-567812345678"
GROUP_ID = "22345678-1234-5678-9234-567812345678"
USER_ID = "32345678-1234-5678-9234-567812345678"
HOUSEHOLD_ID = "42345678-1234-5678-9234-567812345678"


def _account(*, name: str = "Familie") -> IntegrationInstance:
    return IntegrationInstance(
        name=name,
        integration_type="recipes",
        adapter_type="mealie",
        config={"base_url": "http://mealie.example.test/kitchen"},
        credential_id=uuid4(),
        owner_id=uuid4(),
    )


def _revision(account: IntegrationInstance) -> str:
    return json.dumps(
        account.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _context(account: IntegrationInstance, revision: str) -> SimpleNamespace:
    return SimpleNamespace(
        bound_options={
            "integration_id": str(account.id),
            "binding_revision": revision,
        },
        integration_settings=IntegrationSettings(instances=[account]),
    )


def _summary(**changes) -> MealieRecipeSummary:
    values = {
        "id": RECIPE_ID,
        "slug": "kartoffelsuppe",
        "name": "Kartoffelsuppe",
        "description": "Cremig",
        "recipe_servings": 4,
        "recipe_yield_quantity": 2,
        "recipe_yield": "Liter",
        "total_time": "45 Minuten",
        "prep_time": "15 Minuten",
        "cook_time": "30 Minuten",
        "perform_time": "45 Minuten",
        "categories": ("Suppe",),
        "tags": ("Vegetarisch",),
    }
    return MealieRecipeSummary(**(values | changes))


def _recipe(**changes) -> MealieRecipe:
    summary = _summary()
    values = {
        field: getattr(summary, field)
        for field in (
            "id",
            "slug",
            "name",
            "description",
            "recipe_servings",
            "recipe_yield_quantity",
            "recipe_yield",
            "total_time",
            "prep_time",
            "cook_time",
            "perform_time",
            "categories",
            "tags",
        )
    }
    values |= {
        "ingredients": (
            MealieRecipeIngredient(
                quantity=2.5,
                unit="kg",
                food="Kartoffeln",
                note="mehlig",
                display="2,5 kg Kartoffeln",
                title="Suppe",
                original_text="2,5 kg Kartoffeln",
            ),
        ),
        "instructions": (
            MealieRecipeInstruction(
                id=str(uuid4()),
                title="Kochen",
                summary="Langsam",
                text="30 Minuten kochen",
            ),
        ),
    }
    return MealieRecipe(**(values | changes))


def _entry(**changes) -> MealieMealPlanEntry:
    values = {
        "id": 7,
        "date": date(2026, 8, 20),
        "entry_type": "dinner",
        "title": "Abendessen",
        "text": "Mit Brot",
        "recipe_id": RECIPE_ID,
        "recipe": _summary(),
        "group_id": GROUP_ID,
        "user_id": USER_ID,
        "household_id": HOUSEHOLD_ID,
    }
    return MealieMealPlanEntry(**(values | changes))


def _mock_client(monkeypatch, client, revision: str) -> None:
    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(mealie_module, "_selected_client", selected_client)


def test_adapter_is_a_standalone_shared_tool_definition() -> None:
    tools = {tool.name: tool for tool in MEALIE_ADAPTER.shared_tools}

    assert MEALIE_ADAPTER.integration_type == "recipes"
    assert MEALIE_ADAPTER.adapter_type == "mealie"
    assert set(tools) == {
        "mealie_list_accounts",
        "recipes_search",
        "recipe_get",
        "meal_plan_get",
        "meal_plan_add",
        "meal_plan_update",
        "meal_plan_delete",
    }
    assert all(tool.releases_private_data for tool in tools.values())
    assert all(tool.resource_argument is None for tool in tools.values())
    assert tools["mealie_list_accounts"].instance_argument is None
    assert all(
        tool.instance_argument == "integration_id"
        for name, tool in tools.items()
        if name != "mealie_list_accounts"
    )
    actions = {action.capability: action for action in MEALIE_ADAPTER.resource_actions}
    assert set(actions) == {
        "mealie.accounts.read",
        "recipes.read",
        "meal_plan.read",
        "meal_plan.write",
        "meal_plan.delete",
    }
    assert all(action.default_policy == ToolExecutionPolicy.ASK for action in actions.values())
    assert actions["meal_plan.write"].mutates is True
    assert actions["meal_plan.delete"].mutates is True
    assert actions["meal_plan.delete"].allowed_policies == (
        ToolExecutionPolicy.DISABLED,
        ToolExecutionPolicy.ASK,
    )


@pytest.mark.parametrize(
    "value",
    [
        "https://user:secret@mealie.example.test",
        "https://mealie.example.test?x=1",
        "https://mealie.example.test#part",
        "ftp://mealie.example.test",
    ],
)
def test_config_rejects_non_http_or_credential_bearing_urls(value: str) -> None:
    with pytest.raises(ValidationError):
        MealieConfig(base_url=value)


def test_config_accepts_http_and_a_deployment_path() -> None:
    config = MealieConfig(base_url="http://mealie.example.test/kitchen")
    assert str(config.base_url) == "http://mealie.example.test/kitchen"
    with pytest.raises(ValidationError):
        MealieConfig.model_validate(
            {"base_url": "https://mealie.example.test", "unknown": True}
        )


def test_argument_models_are_strict_and_enforce_ranges_and_sparse_updates() -> None:
    account = _account()
    recipe_token = mealie_module._recipe_token(_summary(), account.id, _revision(account))
    meal_token = mealie_module._meal_plan_token(_entry(), account.id, _revision(account))

    assert RecipesSearchArguments(integration_id=account.id).limit == 20
    assert MealPlanAddArguments(
        integration_id=account.id,
        date=date(2026, 8, 20),
        title="Suppe",
    ).entry_type == "dinner"
    with pytest.raises(ValidationError):
        RecipesSearchArguments(
            integration_id=account.id, query="x" * 401, limit=51
        )
    with pytest.raises(ValidationError):
        RecipesSearchArguments.model_validate(
            {"integration_id": account.id, "extra": True}
        )
    with pytest.raises(ValidationError):
        MealPlanGetArguments(
            integration_id=account.id,
            start_date=date(2026, 1, 1),
            end_date=date(2027, 1, 2),
        )
    with pytest.raises(ValidationError):
        MealPlanAddArguments(
            integration_id=account.id, date=date(2026, 8, 20)
        )
    with pytest.raises(ValidationError):
        MealPlanAddArguments(
            integration_id=account.id,
            date=date(2026, 8, 20),
            text="Text ohne Titel",
        )
    with pytest.raises(ValidationError):
        MealPlanUpdateArguments(
            integration_id=account.id, meal_plan_token=meal_token
        )
    with pytest.raises(ValidationError):
        MealPlanUpdateArguments(
            integration_id=account.id,
            meal_plan_token=meal_token,
            title=None,
        )
    assert MealPlanUpdateArguments(
        integration_id=account.id,
        meal_plan_token=meal_token,
        recipe_token=None,
    ).model_fields_set >= {"recipe_token"}
    assert RecipeGetArguments(
        integration_id=account.id, recipe_token=recipe_token
    ).recipe_token == recipe_token


async def test_account_listing_uses_only_the_bound_active_snapshot() -> None:
    first = _account(name="Familie")
    second = _account(name="Archiv")
    disabled = _account(name="Aus")
    disabled.enabled = False
    context = SimpleNamespace(
        bound_options={
            "integration_ids": json.dumps([str(first.id), str(disabled.id)])
        },
        integration_settings=IntegrationSettings(
            instances=[first, second, disabled]
        ),
    )

    result = await mealie_list_accounts(MealieListAccountsArguments(), context)

    assert json.loads(result.content) == {
        "accounts": [{"integrationId": str(first.id), "name": "Familie"}],
        "contentIsUntrusted": True,
    }


async def test_selected_client_loads_the_exact_owner_credential(monkeypatch) -> None:
    account = _account()
    revision = _revision(account)
    context = _context(account, revision)
    context.db = object()
    record = SimpleNamespace(kind=MEALIE_CREDENTIAL_KIND)
    load = AsyncMock(return_value=record)
    monkeypatch.setattr(mealie_module, "load_credential", load)
    monkeypatch.setattr(mealie_module, "read_credential", lambda _record: {"token": "secret"})
    client_context = AsyncMock()
    client_context.__aenter__.return_value = object()
    constructor = Mock(return_value=client_context)
    monkeypatch.setattr(mealie_module, "MealieClient", constructor)

    async with mealie_module._selected_client(account.id, context) as (_client, selected):
        assert selected == revision

    load.assert_awaited_once_with(
        context.db, account.credential_id, owner_id=account.owner_id
    )
    constructor.assert_called_once_with(
        "http://mealie.example.test/kitchen", "secret"
    )
    monkeypatch.setattr(
        mealie_module,
        "read_credential",
        lambda _record: {"token": "x", "extra": "y"},
    )
    with pytest.raises(MealieError, match="mealie.authenticationFailed"):
        mealie_module._credential_payload(record)


async def test_service_stores_the_token_outside_integration_settings(monkeypatch) -> None:
    owner_id = uuid4()
    settings = IntegrationSettings()
    credential_id = uuid4()
    load = AsyncMock(return_value=settings)
    save = AsyncMock()
    store = AsyncMock(return_value=SimpleNamespace(id=credential_id))
    monkeypatch.setattr(service_module, "_load_integrations", load)
    monkeypatch.setattr(service_module, "_save_settings", save)
    monkeypatch.setattr(service_module, "store_credential", store)
    db = AsyncMock()

    result = await MealieIntegrationService.create(
        db,
        name="Familienküche",
        base_url="http://mealie.example.test/kitchen/",
        token="secret-token",
        enabled=True,
        owner_id=owner_id,
    )

    instance = result.instances[0]
    assert instance.config == {"base_url": "http://mealie.example.test/kitchen"}
    assert instance.credential_id == credential_id
    assert "secret-token" not in json.dumps(result.model_dump(mode="json"))
    store.assert_awaited_once_with(
        db,
        owner_id=owner_id,
        kind=MEALIE_CREDENTIAL_KIND,
        label="Familienküche",
        payload={"token": "secret-token"},
    )
    save.assert_awaited_once_with(db, settings, owner_id)
    db.commit.assert_awaited_once()


def test_tokens_are_encrypted_secret_and_binding_scoped() -> None:
    account = _account()
    revision = _revision(account)
    recipe_token = mealie_module._recipe_token(_summary(), account.id, revision)
    meal_token = mealie_module._meal_plan_token(_entry(), account.id, revision)

    assert RECIPE_ID not in recipe_token
    assert "Kartoffelsuppe" not in recipe_token
    assert GROUP_ID not in meal_token
    decrypted = mealie_module.CredentialCipher().decrypt(recipe_token.encode("ascii"))
    assert decrypted["binding_revision"] == hashlib.sha256(revision.encode()).hexdigest()
    assert decrypted["recipe_id"] == RECIPE_ID
    assert mealie_module._decode_token(
        mealie_module._RecipeToken, recipe_token, account.id, revision
    ).name == "Kartoffelsuppe"
    with pytest.raises(ValueError, match="binding"):
        mealie_module._decode_token(
            mealie_module._RecipeToken, recipe_token, uuid4(), revision
        )


async def test_recipe_search_and_detail_return_bounded_opaque_results(monkeypatch) -> None:
    account = _account()
    revision = _revision(account)
    client = AsyncMock()
    client.search_recipes.return_value = (_summary(description="x" * 6_000),)
    client.get_recipe.return_value = _recipe()
    _mock_client(monkeypatch, client, revision)
    context = _context(account, revision)

    search = await recipes_search(
        RecipesSearchArguments(integration_id=account.id, query="Suppe"), context
    )
    summary = json.loads(search.content)["recipes"][0]
    detail = await recipe_get(
        RecipeGetArguments(
            integration_id=account.id, recipe_token=summary["recipeToken"]
        ),
        context,
    )
    recipe = json.loads(detail.content)["recipe"]

    client.search_recipes.assert_awaited_once_with("Suppe", 20)
    client.get_recipe.assert_awaited_once_with(RECIPE_ID)
    assert len(summary["description"]) == 5_000
    assert summary["categories"] == ["Suppe"]
    assert len(recipe["ingredients"]) == 1
    assert len(recipe["instructions"]) == 1
    assert "recipe_id" not in detail.content
    assert RECIPE_ID not in detail.content
    assert json.loads(detail.content)["contentIsUntrusted"] is True


async def test_recipe_detail_rejects_incomplete_bounded_output(monkeypatch) -> None:
    account = _account()
    revision = _revision(account)
    client = AsyncMock()
    client.get_recipe.return_value = _recipe(
        ingredients=tuple(_recipe().ingredients[0] for _ in range(201))
    )
    _mock_client(monkeypatch, client, revision)

    result = await recipe_get(
        RecipeGetArguments(
            integration_id=account.id,
            recipe_token=mealie_module._recipe_token(
                _summary(), account.id, revision
            ),
        ),
        _context(account, revision),
    )

    assert result.metadata == {"errorCode": "integration.responseTooLarge"}


async def test_meal_plan_read_add_update_and_delete_chain_opaque_tokens(
    monkeypatch,
) -> None:
    account = _account()
    revision = _revision(account)
    original = _entry()
    cleared = replace(original, title="Später", recipe_id=None, recipe=None)
    client = AsyncMock()
    client.get_meal_plans.return_value = (original,)
    client.create_meal_plan.return_value = original
    client.get_meal_plan.side_effect = [original, cleared]
    client.update_meal_plan.return_value = cleared
    _mock_client(monkeypatch, client, revision)
    context = _context(account, revision)

    listing = await meal_plan_get(
        MealPlanGetArguments(
            integration_id=account.id,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
        ),
        context,
    )
    listed = json.loads(listing.content)["entries"][0]
    recipe_token = listed["recipe"]["recipeToken"]
    created = await meal_plan_add(
        MealPlanAddArguments(
            integration_id=account.id,
            date=date(2026, 8, 20),
            recipe_token=recipe_token,
        ),
        context,
    )
    updated = await meal_plan_update(
        MealPlanUpdateArguments(
            integration_id=account.id,
            meal_plan_token=json.loads(created.content)["entry"]["mealPlanToken"],
            title="Später",
            recipe_token=None,
        ),
        context,
    )
    updated_entry = json.loads(updated.content)["entry"]
    deleted = await meal_plan_delete(
        MealPlanDeleteArguments(
            integration_id=account.id,
            meal_plan_token=updated_entry["mealPlanToken"],
        ),
        context,
    )

    client.create_meal_plan.assert_awaited_once_with(
        date(2026, 8, 20), "dinner", "", "", RECIPE_ID
    )
    sent = client.update_meal_plan.await_args.args[0]
    assert sent.title == "Später"
    assert sent.text == "Mit Brot"
    assert sent.recipe_id is None
    assert updated_entry["recipe"] is None
    assert json.loads(deleted.content) == {
        "deleted": True,
        "contentIsUntrusted": True,
    }
    client.delete_meal_plan.assert_awaited_once_with(7)
    for remote_id in (RECIPE_ID, GROUP_ID, USER_ID, HOUSEHOLD_ID):
        assert remote_id not in listing.content


async def test_post_commit_meal_plan_serialization_failure_is_outcome_unknown(
    monkeypatch,
) -> None:
    account = _account()
    revision = _revision(account)
    client = AsyncMock()
    client.create_meal_plan.return_value = _entry(text="x" * 5_001)
    _mock_client(monkeypatch, client, revision)

    result = await meal_plan_add(
        MealPlanAddArguments(
            integration_id=account.id,
            date=date(2026, 8, 20),
            title="Abendessen",
        ),
        _context(account, revision),
    )

    assert result.metadata == {"errorCode": "integration.outcomeUnknown"}


@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_meal_plan_mutations_reject_a_remotely_changed_entry(
    monkeypatch, operation: str
) -> None:
    account = _account()
    revision = _revision(account)
    token = mealie_module._meal_plan_token(_entry(), account.id, revision)
    client = AsyncMock()
    client.get_meal_plan.return_value = _entry(text="Fremde Änderung")
    _mock_client(monkeypatch, client, revision)
    context = _context(account, revision)

    if operation == "update":
        result = await meal_plan_update(
            MealPlanUpdateArguments(
                integration_id=account.id,
                meal_plan_token=token,
                title="Neu",
            ),
            context,
        )
    else:
        result = await meal_plan_delete(
            MealPlanDeleteArguments(
                integration_id=account.id, meal_plan_token=token
            ),
            context,
        )

    assert result.metadata == {"errorCode": "integration.remoteChanged"}
    client.update_meal_plan.assert_not_awaited()
    client.delete_meal_plan.assert_not_awaited()


def test_error_mapping_covers_mealie_client_failures() -> None:
    assert mealie_module.mealie_error_code(
        MealieError("mealie.invalidRequest")
    ) == "tool.invalidArguments"
    assert mealie_module.mealie_error_code(
        MealieError("mealie.authenticationFailed")
    ) == "integration.authenticationFailed"
    assert mealie_module.mealie_error_code(
        MealieError("mealie.permissionDenied")
    ) == "integration.permissionDenied"
    assert mealie_module.mealie_error_code(
        MealieError("mealie.notFound")
    ) == "integration.remoteNotFound"
    assert mealie_module.mealie_error_code(
        MealieError("mealie.outcomeUnknown")
    ) == "integration.outcomeUnknown"
    assert mealie_module.mealie_error_code(
        MealieError("mealie.responseTooLarge")
    ) == "integration.responseTooLarge"
    assert mealie_module.mealie_error_code(
        MealieError("mealie.malformedResponse")
    ) == "integration.invalidResponse"
    assert mealie_module.mealie_error_code(
        MealieError("mealie.rateLimited")
    ) == "integration.rateLimited"


def test_permissions_name_the_account_show_details_and_limit_writes() -> None:
    account = _account(name="Familienküche")
    revision = _revision(account)
    context = _context(account, revision)
    tools = {tool.name: tool for tool in MEALIE_ADAPTER.shared_tools}
    read = tools["meal_plan_get"].authorization_factory(
        MealPlanGetArguments(
            integration_id=account.id,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 7),
        ),
        context,
    )[0]
    write_arguments = MealPlanAddArguments(
        integration_id=account.id,
        date=date(2026, 8, 20),
        title="Suppe",
    )
    write = tools["meal_plan_add"].authorization_factory(
        write_arguments, context
    )[0]
    account_list = tools["mealie_list_accounts"].authorization_factory(
        MealieListAccountsArguments(), context
    )[0]

    assert read.resource_display == 'Mealie-Konto "Familienküche"'
    assert read.available_decisions == ("allow_once", "allow_session", "deny")
    assert read.display_items
    assert write.available_decisions == ("allow_once", "deny")
    assert write.display_items
    scope = json.loads(write.resource_scope)
    operation = scope["operationKey"]
    assert operation == mealie_module._operation_key(
        "meal-plan-add", write_arguments
    )
    assert operation.startswith("meal-plan-add:") and len(operation) == 78
    assert account_list.capability == "mealie.accounts.read"
    assert account_list.resource_type == "network_service"
    assert account_list.resource_scope == "mealie_accounts"
