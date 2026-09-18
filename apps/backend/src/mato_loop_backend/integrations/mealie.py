from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date as Date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from mato_loop_backend.agent.tool_contracts import ToolAction
from mato_loop_backend.integrations.contracts import (
    IntegrationAdapterDefinition,
    IntegrationContributions,
    ToolContribution,
)
from mato_loop_backend.integrations.mealie_client import (
    MealieClient,
    MealieEntryType,
    MealieError,
    MealieMealPlanEntry,
    MealieRecipe,
    MealieRecipeIngredient,
    MealieRecipeInstruction,
    MealieRecipeSummary,
)
from mato_loop_backend.models import Credential
from mato_loop_backend.security import CredentialCipher, load_credential, read_credential
from mato_loop_backend.settings import IntegrationInstance, ToolExecutionPolicy

MEALIE_CREDENTIAL_KIND = "mealie.api_token"

_ACCOUNTS_READ = ToolAction(
    "mealie.accounts.read", "Mealie-Konten auflisten", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK
)
_RECIPES_READ = ToolAction(
    "recipes.read", "Rezepte lesen", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK
)
_MEAL_PLAN_READ = ToolAction(
    "meal_plan.read", "Essensplan lesen", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK
)
_MEAL_PLAN_WRITE = ToolAction(
    "meal_plan.write", "Essensplan ändern", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK, mutates=True
)
_MEAL_PLAN_DELETE = ToolAction(
    "meal_plan.delete",
    "Essensplan-Einträge löschen",
    ToolExecutionPolicy.ASK,
    ToolExecutionPolicy.ASK,
    mutates=True,
)


class MealieConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: HttpUrl

    @model_validator(mode="after")
    def validate_url(self) -> MealieConfig:
        if (
            self.base_url.username is not None
            or self.base_url.password is not None
            or self.base_url.query is not None
            or self.base_url.fragment is not None
        ):
            raise ValueError("Mealie URL must not contain credentials, query, or fragment")
        return self


class _RecipeToken(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    integration_id: UUID
    binding_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    recipe_id: UUID
    slug: str = Field(max_length=1_000)
    name: str = Field(max_length=500)


class _MealPlanToken(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    integration_id: UUID
    binding_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    meal_plan_id: int = Field(ge=1)
    date: Date
    entry_type: MealieEntryType
    title: str = Field(max_length=500)
    text: str = Field(max_length=5_000)
    recipe_id: UUID | None
    group_id: UUID
    user_id: UUID
    household_id: UUID
    recipe_name: str | None = Field(max_length=500)


def _validate_encrypted_token(value: str, model: type[BaseModel], label: str) -> str:
    try:
        model.model_validate(CredentialCipher().decrypt(value.encode("ascii")))
    except Exception as exc:
        raise ValueError(f"invalid {label} token") from exc
    return value


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MealieListAccountsArguments(_Arguments):
    pass


class MealieIntegrationArguments(_Arguments):
    integration_id: UUID


class RecipesSearchArguments(MealieIntegrationArguments):
    query: str | None = Field(default=None, max_length=400)
    limit: int = Field(default=20, ge=1, le=50)


class RecipeGetArguments(MealieIntegrationArguments):
    recipe_token: str = Field(min_length=1, max_length=50_000)

    @field_validator("recipe_token")
    @classmethod
    def validate_recipe_token(cls, value: str) -> str:
        return _validate_encrypted_token(value, _RecipeToken, "recipe")


class MealPlanGetArguments(MealieIntegrationArguments):
    start_date: Date
    end_date: Date
    limit: int = Field(default=100, ge=1, le=100)

    @model_validator(mode="after")
    def validate_range(self) -> MealPlanGetArguments:
        if self.end_date < self.start_date or (self.end_date - self.start_date).days > 365:
            raise ValueError("date range must contain at most 366 inclusive days")
        return self


class MealPlanAddArguments(MealieIntegrationArguments):
    date: Date
    entry_type: MealieEntryType = "dinner"
    title: str = Field(default="", max_length=500)
    text: str = Field(default="", max_length=5_000)
    recipe_token: str | None = Field(default=None, min_length=1, max_length=50_000)

    @field_validator("recipe_token")
    @classmethod
    def validate_recipe_token(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_encrypted_token(value, _RecipeToken, "recipe")
        return value

    @model_validator(mode="after")
    def require_content(self) -> MealPlanAddArguments:
        if self.recipe_token is None and not self.title.strip():
            raise ValueError("recipe_token or title is required")
        return self


class MealPlanTokenArguments(MealieIntegrationArguments):
    meal_plan_token: str = Field(min_length=1, max_length=50_000)

    @field_validator("meal_plan_token")
    @classmethod
    def validate_meal_plan_token(cls, value: str) -> str:
        return _validate_encrypted_token(value, _MealPlanToken, "meal plan")


class MealPlanUpdateArguments(MealPlanTokenArguments):
    date: Date | None = None
    entry_type: MealieEntryType | None = None
    title: str | None = Field(default=None, max_length=500)
    text: str | None = Field(default=None, max_length=5_000)
    recipe_token: str | None = Field(default=None, min_length=1, max_length=50_000)

    @field_validator("recipe_token")
    @classmethod
    def validate_recipe_token(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_encrypted_token(value, _RecipeToken, "recipe")
        return value

    @model_validator(mode="after")
    def require_patch(self) -> MealPlanUpdateArguments:
        fields = self.model_fields_set & {
            "date",
            "entry_type",
            "title",
            "text",
            "recipe_token",
        }
        if not fields:
            raise ValueError("at least one meal plan field is required")
        for field in fields - {"recipe_token"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class MealPlanDeleteArguments(MealPlanTokenArguments):
    pass


_MEALIE_ERROR_CODES = {
    "mealie.authenticationFailed": "integration.authenticationFailed",
    "mealie.invalidResponse": "integration.invalidResponse",
    "mealie.invalidRequest": "tool.invalidArguments",
    "mealie.invalidUrl": "integration.invalidConfiguration",
    "mealie.malformedResponse": "integration.invalidResponse",
    "mealie.notFound": "integration.remoteNotFound",
    "mealie.outcomeUnknown": "integration.outcomeUnknown",
    "mealie.permissionDenied": "integration.permissionDenied",
    "mealie.rateLimited": "integration.rateLimited",
    "mealie.redirectRejected": "integration.unavailable",
    "mealie.responseTooLarge": "integration.responseTooLarge",
    "mealie.unavailable": "integration.unavailable",
    "mealie.unexpectedStatus": "integration.unavailable",
}


def mealie_error_code(error: MealieError) -> str:
    return _MEALIE_ERROR_CODES.get(error.code, "integration.unavailable")


def _bound_ids(context: Any) -> set[str]:
    value = context.bound_options.get("integration_ids")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return set()
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) for item in value
    ):
        return set()
    return set(value)


def _binding(context: Any, integration_id: UUID) -> tuple[IntegrationInstance, str]:
    selected_id = context.bound_options.get("integration_id")
    revision = context.bound_options.get("binding_revision")
    if selected_id != str(integration_id) or not isinstance(revision, str):
        raise ValueError("missing selected integration binding")
    instance = next(
        (
            item
            for item in context.integration_settings.instances
            if str(item.id) == selected_id
            and item.enabled
            and item.integration_type == "recipes"
            and item.adapter_type == "mealie"
        ),
        None,
    )
    if instance is None or json.dumps(
        instance.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) != revision:
        raise ValueError("stale selected integration binding")
    return instance, revision


def _credential_payload(record: Credential | None) -> dict[str, Any]:
    if record is None or record.kind != MEALIE_CREDENTIAL_KIND:
        raise MealieError("mealie.authenticationFailed")
    try:
        payload = read_credential(record)
    except Exception as exc:
        raise MealieError("mealie.authenticationFailed") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"token"}
        or not isinstance(payload["token"], str)
        or not payload["token"]
    ):
        raise MealieError("mealie.authenticationFailed")
    return payload


@asynccontextmanager
async def _selected_client(
    integration_id: UUID, context: Any
) -> AsyncIterator[tuple[MealieClient, str]]:
    instance, revision = _binding(context, integration_id)
    if instance.credential_id is None:
        raise MealieError("mealie.authenticationFailed")
    record = await load_credential(
        context.db, instance.credential_id, owner_id=instance.owner_id
    )
    token = _credential_payload(record)["token"]
    config = MealieConfig.model_validate(instance.config)
    async with MealieClient(str(config.base_url), token) as client:
        yield client, revision


def _fingerprint(revision: str) -> str:
    return hashlib.sha256(revision.encode()).hexdigest()


def _encrypt(payload: BaseModel) -> str:
    return (
        CredentialCipher()
        .encrypt(payload.model_dump(mode="json"))
        .decode("ascii")
    )


def _recipe_token(
    recipe: MealieRecipeSummary, integration_id: UUID, revision: str
) -> str:
    if recipe.id is None:
        raise MealieError("mealie.malformedResponse")
    return _encrypt(
        _RecipeToken(
            version=1,
            integration_id=integration_id,
            binding_revision=_fingerprint(revision),
            recipe_id=UUID(recipe.id),
            slug=_bounded(recipe.slug, 1_000) or "",
            name=_bounded(recipe.name, 500) or "",
        )
    )


def _meal_plan_token(
    entry: MealieMealPlanEntry, integration_id: UUID, revision: str
) -> str:
    if len(entry.title) > 500 or len(entry.text) > 5_000:
        raise MealieError("mealie.malformedResponse")
    return _encrypt(
        _MealPlanToken(
            version=1,
            integration_id=integration_id,
            binding_revision=_fingerprint(revision),
            meal_plan_id=entry.id,
            date=entry.date,
            entry_type=entry.entry_type,
            title=entry.title,
            text=entry.text,
            recipe_id=UUID(entry.recipe_id) if entry.recipe_id is not None else None,
            group_id=UUID(entry.group_id),
            user_id=UUID(entry.user_id),
            household_id=UUID(entry.household_id),
            recipe_name=(
                _bounded(entry.recipe.name, 500) if entry.recipe is not None else None
            ),
        )
    )


def _decode_token(
    model: type[BaseModel], value: str, integration_id: UUID, revision: str
) -> Any:
    try:
        payload = model.model_validate(
            CredentialCipher().decrypt(value.encode("ascii"))
        )
    except Exception as exc:
        raise ValueError("invalid token") from exc
    if (
        payload.integration_id != integration_id
        or payload.binding_revision != _fingerprint(revision)
    ):
        raise ValueError("token binding mismatch")
    return payload


def _bounded(value: str | None, limit: int) -> str | None:
    return value[:limit] if value is not None else None


def _bounded_names(values: tuple[str, ...]) -> list[str]:
    return [_bounded(value, 500) or "" for value in values[:100]]


def _recipe_summary(
    recipe: MealieRecipeSummary, integration_id: UUID, revision: str
) -> dict[str, Any]:
    return {
        "recipeToken": _recipe_token(recipe, integration_id, revision),
        "integrationId": str(integration_id),
        "name": _bounded(recipe.name, 500),
        "description": _bounded(recipe.description, 5_000),
        "servings": recipe.recipe_servings,
        "yieldQuantity": recipe.recipe_yield_quantity,
        "yield": _bounded(recipe.recipe_yield, 500),
        "totalTime": _bounded(recipe.total_time, 500),
        "prepTime": _bounded(recipe.prep_time, 500),
        "cookTime": _bounded(recipe.cook_time, 500),
        "performTime": _bounded(recipe.perform_time, 500),
        "categories": _bounded_names(recipe.categories),
        "tags": _bounded_names(recipe.tags),
    }


def _ingredient_payload(ingredient: MealieRecipeIngredient) -> dict[str, Any]:
    return {
        "quantity": ingredient.quantity,
        "unit": _bounded(ingredient.unit, 500),
        "food": _bounded(ingredient.food, 500),
        "note": _bounded(ingredient.note, 5_000),
        "display": _bounded(ingredient.display, 5_000),
        "title": _bounded(ingredient.title, 500),
        "originalText": _bounded(ingredient.original_text, 5_000),
    }


def _instruction_payload(instruction: MealieRecipeInstruction) -> dict[str, Any]:
    return {
        "title": _bounded(instruction.title, 500),
        "summary": _bounded(instruction.summary, 5_000),
        "text": _bounded(instruction.text, 10_000),
    }


def _recipe_payload(
    recipe: MealieRecipe, integration_id: UUID, revision: str
) -> dict[str, Any]:
    if (
        len(recipe.ingredients) > 200
        or len(recipe.instructions) > 200
        or any(
            len(value) > limit
            for value, limit in (
                (recipe.description or "", 5_000),
                *(
                    (value or "", limit)
                    for ingredient in recipe.ingredients
                    for value, limit in (
                        (ingredient.unit, 500),
                        (ingredient.food, 500),
                        (ingredient.note, 5_000),
                        (ingredient.display, 5_000),
                        (ingredient.title, 500),
                        (ingredient.original_text, 5_000),
                    )
                ),
                *(
                    (value or "", limit)
                    for instruction in recipe.instructions
                    for value, limit in (
                        (instruction.title, 500),
                        (instruction.summary, 5_000),
                        (instruction.text, 10_000),
                    )
                ),
            )
        )
    ):
        raise MealieError("mealie.responseTooLarge")
    return {
        **_recipe_summary(recipe, integration_id, revision),
        "ingredients": [
            _ingredient_payload(item) for item in recipe.ingredients[:200]
        ],
        "instructions": [
            _instruction_payload(item) for item in recipe.instructions[:200]
        ],
    }


def _meal_plan_payload(
    entry: MealieMealPlanEntry, integration_id: UUID, revision: str
) -> dict[str, Any]:
    if entry.recipe is not None and entry.recipe.id != entry.recipe_id:
        raise MealieError("mealie.malformedResponse")
    return {
        "mealPlanToken": _meal_plan_token(entry, integration_id, revision),
        "integrationId": str(integration_id),
        "date": entry.date.isoformat(),
        "entryType": entry.entry_type,
        "title": _bounded(entry.title, 500),
        "text": _bounded(entry.text, 5_000),
        "recipe": (
            _recipe_summary(entry.recipe, integration_id, revision)
            if entry.recipe is not None
            else None
        ),
    }


def _result(value: dict[str, Any]) -> Any:
    from mato_loop_backend.agent.tools import ToolResult

    return ToolResult.json({**value, "contentIsUntrusted": True})


def _error(error: MealieError) -> Any:
    from mato_loop_backend.agent.tools import ToolResult

    return ToolResult.error(mealie_error_code(error))


def _invalid(field: str) -> Any:
    from mato_loop_backend.agent.tools import ToolResult

    return ToolResult.error("integration.staleReference", field=field)


def _remote_changed() -> Any:
    from mato_loop_backend.agent.tools import ToolResult

    return ToolResult.error("integration.remoteChanged", field="meal_plan_token")


async def mealie_list_accounts(
    _arguments: MealieListAccountsArguments, context: Any
) -> Any:
    bound_ids = _bound_ids(context)
    if not bound_ids:
        from mato_loop_backend.agent.tools import ToolResult

        return ToolResult.error("tool.unavailable")
    accounts = [
        {"integrationId": str(instance.id), "name": instance.name[:160]}
        for instance in context.integration_settings.instances
        if str(instance.id) in bound_ids
        and instance.enabled
        and instance.integration_type == "recipes"
        and instance.adapter_type == "mealie"
    ]
    return _result({"accounts": accounts})


async def recipes_search(arguments: RecipesSearchArguments, context: Any) -> Any:
    try:
        async with _selected_client(arguments.integration_id, context) as (
            client,
            revision,
        ):
            recipes = await client.search_recipes(arguments.query, arguments.limit)
        return _result(
            {
                "recipes": [
                    _recipe_summary(recipe, arguments.integration_id, revision)
                    for recipe in recipes[: arguments.limit]
                ]
            }
        )
    except ValueError:
        return _invalid("integration_id")
    except MealieError as exc:
        return _error(exc)


async def recipe_get(arguments: RecipeGetArguments, context: Any) -> Any:
    try:
        _instance, revision = _binding(context, arguments.integration_id)
        token = _decode_token(
            _RecipeToken,
            arguments.recipe_token,
            arguments.integration_id,
            revision,
        )
        async with _selected_client(arguments.integration_id, context) as (
            client,
            _revision,
        ):
            recipe = await client.get_recipe(str(token.recipe_id))
        if recipe.id != str(token.recipe_id):
            raise MealieError("mealie.malformedResponse")
        return _result(
            {"recipe": _recipe_payload(recipe, arguments.integration_id, revision)}
        )
    except ValueError:
        return _invalid("recipe_token")
    except MealieError as exc:
        return _error(exc)


async def meal_plan_get(arguments: MealPlanGetArguments, context: Any) -> Any:
    try:
        async with _selected_client(arguments.integration_id, context) as (
            client,
            revision,
        ):
            entries = await client.get_meal_plans(
                arguments.start_date, arguments.end_date, arguments.limit
            )
        return _result(
            {
                "entries": [
                    _meal_plan_payload(entry, arguments.integration_id, revision)
                    for entry in entries[: arguments.limit]
                ]
            }
        )
    except ValueError:
        return _invalid("integration_id")
    except MealieError as exc:
        return _error(exc)


async def meal_plan_add(arguments: MealPlanAddArguments, context: Any) -> Any:
    try:
        _instance, revision = _binding(context, arguments.integration_id)
        recipe_id = None
        if arguments.recipe_token is not None:
            recipe_id = str(
                _decode_token(
                    _RecipeToken,
                    arguments.recipe_token,
                    arguments.integration_id,
                    revision,
                ).recipe_id
            )
        async with _selected_client(arguments.integration_id, context) as (
            client,
            _revision,
        ):
            entry = await client.create_meal_plan(
                arguments.date,
                arguments.entry_type,
                arguments.title,
                arguments.text,
                recipe_id,
            )
        try:
            return _result(
                {"entry": _meal_plan_payload(entry, arguments.integration_id, revision)}
            )
        except (MealieError, ValueError):
            from mato_loop_backend.agent.tools import ToolResult

            return ToolResult.error("integration.outcomeUnknown")
    except ValueError:
        return _invalid("recipe_token")
    except MealieError as exc:
        return _error(exc)


def _entry_matches(token: _MealPlanToken, entry: MealieMealPlanEntry) -> bool:
    return (
        token.meal_plan_id == entry.id
        and token.date == entry.date
        and token.entry_type == entry.entry_type
        and token.title == entry.title
        and token.text == entry.text
        and (str(token.recipe_id) if token.recipe_id is not None else None)
        == entry.recipe_id
        and str(token.group_id) == entry.group_id
        and str(token.user_id) == entry.user_id
        and str(token.household_id) == entry.household_id
    )


async def meal_plan_update(arguments: MealPlanUpdateArguments, context: Any) -> Any:
    try:
        _instance, revision = _binding(context, arguments.integration_id)
        token = _decode_token(
            _MealPlanToken,
            arguments.meal_plan_token,
            arguments.integration_id,
            revision,
        )
        recipe_id = None
        if "recipe_token" in arguments.model_fields_set and arguments.recipe_token is not None:
            recipe_id = str(
                _decode_token(
                    _RecipeToken,
                    arguments.recipe_token,
                    arguments.integration_id,
                    revision,
                ).recipe_id
            )
        async with _selected_client(arguments.integration_id, context) as (
            client,
            _revision,
        ):
            current = await client.get_meal_plan(token.meal_plan_id)
            if not _entry_matches(token, current):
                return _remote_changed()
            changes = {
                field: getattr(arguments, field)
                for field in ("date", "entry_type", "title", "text")
                if field in arguments.model_fields_set
            }
            if "recipe_token" in arguments.model_fields_set:
                changes |= {"recipe_id": recipe_id, "recipe": None}
            updated = replace(current, **changes)
            if updated.recipe_id is None and not updated.title.strip():
                from mato_loop_backend.agent.tools import ToolResult

                return ToolResult.error("tool.invalidArguments", field="title")
            entry = await client.update_meal_plan(updated)
        try:
            return _result(
                {"entry": _meal_plan_payload(entry, arguments.integration_id, revision)}
            )
        except (MealieError, ValueError):
            from mato_loop_backend.agent.tools import ToolResult

            return ToolResult.error("integration.outcomeUnknown")
    except ValueError:
        field = (
            "recipe_token"
            if "recipe_token" in arguments.model_fields_set
            else "meal_plan_token"
        )
        return _invalid(field)
    except MealieError as exc:
        return _error(exc)


async def meal_plan_delete(arguments: MealPlanDeleteArguments, context: Any) -> Any:
    try:
        _instance, revision = _binding(context, arguments.integration_id)
        token = _decode_token(
            _MealPlanToken,
            arguments.meal_plan_token,
            arguments.integration_id,
            revision,
        )
        async with _selected_client(arguments.integration_id, context) as (
            client,
            _revision,
        ):
            current = await client.get_meal_plan(token.meal_plan_id)
            if not _entry_matches(token, current):
                return _remote_changed()
            await client.delete_meal_plan(token.meal_plan_id)
        return _result({"deleted": True})
    except ValueError:
        return _invalid("meal_plan_token")
    except MealieError as exc:
        return _error(exc)


def _operation_key(name: str, arguments: BaseModel) -> str:
    material = json.dumps(
        arguments.model_dump(mode="json", exclude_unset=True),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"{name}:{hashlib.sha256(material.encode()).hexdigest()}"


def _display_text(value: str | None, limit: int = 180) -> str:
    text = " ".join((value or "").split())
    return f"{text[:limit]}..." if len(text) > limit else text


def _permission(
    capability: str,
    operation_key: str,
    arguments: MealieIntegrationArguments,
    context: Any,
    *,
    title: str,
    summary: str,
    display_items: tuple[Any, ...],
    resource_key: str,
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import integration_operation_permission

    instance, revision = _binding(context, arguments.integration_id)
    return (
        integration_operation_permission(
            capability=capability,
            integration_id=arguments.integration_id,
            binding_revision=revision,
            operation_key=operation_key,
            grant_resource_key=resource_key,
            resource_display=f'Mealie-Konto "{_display_text(instance.name)}"',
            title=title,
            summary=summary,
            display_items=display_items,
        ),
    )


def _accounts_permission(
    _arguments: MealieListAccountsArguments, _context: Any
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import PermissionDisplayItem, PermissionRequest

    return (
        PermissionRequest(
            capability="mealie.accounts.read",
            resource_type="network_service",
            resource_scope="mealie_accounts",
            action_kind="mealie_list_accounts",
            risk_level="medium",
            title="Mealie-Konten auflisten?",
            summary="Der Agent möchte die konfigurierten Mealie-Konten lesen.",
            resource_display="Konfigurierte Mealie-Konten",
            display_items=(PermissionDisplayItem("Aktion", "Mealie-Konten auflisten"),),
        ),
    )


def _search_permission(
    arguments: RecipesSearchArguments, context: Any
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import PermissionDisplayItem

    query = _display_text(arguments.query) or "Alle Rezepte"
    return _permission(
        "recipes.read",
        "recipes-search",
        arguments,
        context,
        title="Rezepte suchen?",
        summary=f'Der Agent möchte nach "{query}" suchen.',
        display_items=(
            PermissionDisplayItem("Suche", query),
            PermissionDisplayItem("Anzahl", f"Bis zu {arguments.limit}"),
        ),
        resource_key="recipes",
    )


def _recipe_get_permission(
    arguments: RecipeGetArguments, context: Any
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import PermissionDisplayItem

    name = "Ausgewähltes Rezept"
    try:
        _instance, revision = _binding(context, arguments.integration_id)
        name = _display_text(
            _decode_token(
                _RecipeToken,
                arguments.recipe_token,
                arguments.integration_id,
                revision,
            ).name
        )
    except ValueError:
        pass
    return _permission(
        "recipes.read",
        "recipe-get",
        arguments,
        context,
        title="Rezept lesen?",
        summary=f'Der Agent möchte das Rezept "{name}" lesen.',
        display_items=(PermissionDisplayItem("Rezept", name),),
        resource_key="recipes",
    )


def _meal_plan_get_permission(
    arguments: MealPlanGetArguments, context: Any
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import PermissionDisplayItem

    period = f"{arguments.start_date:%d.%m.%Y} bis {arguments.end_date:%d.%m.%Y}"
    return _permission(
        "meal_plan.read",
        "meal-plan-get",
        arguments,
        context,
        title="Essensplan lesen?",
        summary=f"Der Agent möchte den Essensplan vom {period} lesen.",
        display_items=(
            PermissionDisplayItem("Zeitraum", period),
            PermissionDisplayItem("Anzahl", f"Bis zu {arguments.limit}"),
        ),
        resource_key="meal-plan",
    )


def _meal_plan_add_permission(
    arguments: MealPlanAddArguments, context: Any
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import PermissionDisplayItem

    label = _display_text(arguments.title or arguments.text) or "Rezept"
    return _permission(
        "meal_plan.write",
        _operation_key("meal-plan-add", arguments),
        arguments,
        context,
        title="Essensplan-Eintrag erstellen?",
        summary=f'Der Agent möchte am {arguments.date:%d.%m.%Y} "{label}" eintragen.',
        display_items=(
            PermissionDisplayItem("Datum", f"{arguments.date:%d.%m.%Y}"),
            PermissionDisplayItem("Eintrag", label),
            PermissionDisplayItem("Typ", arguments.entry_type),
        ),
        resource_key="meal-plan",
    )


def _meal_plan_for_permission(
    arguments: MealPlanTokenArguments, context: Any
) -> _MealPlanToken:
    _instance, revision = _binding(context, arguments.integration_id)
    return _decode_token(
        _MealPlanToken,
        arguments.meal_plan_token,
        arguments.integration_id,
        revision,
    )


def _meal_plan_update_permission(
    arguments: MealPlanUpdateArguments, context: Any
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import PermissionDisplayItem

    changes = []
    for field, label in (
        ("date", "Datum"),
        ("entry_type", "Typ"),
        ("title", "Titel"),
        ("text", "Text"),
        ("recipe_token", "Rezept"),
    ):
        if field in arguments.model_fields_set:
            value = getattr(arguments, field)
            if field == "recipe_token":
                value = "entfernen" if value is None else "ändern"
            elif isinstance(value, Date):
                value = f"{value:%d.%m.%Y}"
            changes.append(f"{label}: {_display_text(str(value))}")
    try:
        token = _meal_plan_for_permission(arguments, context)
        entry = _display_text(token.title or token.text or token.recipe_name) or "Eintrag"
    except ValueError:
        entry = "Essensplan-Eintrag"
    return _permission(
        "meal_plan.write",
        _operation_key("meal-plan-update", arguments),
        arguments,
        context,
        title="Essensplan-Eintrag ändern?",
        summary=f'Der Agent möchte "{entry}" ändern.',
        display_items=(
            PermissionDisplayItem("Eintrag", entry),
            PermissionDisplayItem("Änderungen", tuple(changes), format="list"),
        ),
        resource_key="meal-plan",
    )


def _meal_plan_delete_permission(
    arguments: MealPlanDeleteArguments, context: Any
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import PermissionDisplayItem

    try:
        token = _meal_plan_for_permission(arguments, context)
        entry = _display_text(token.title or token.text or token.recipe_name) or "Eintrag"
        day = f"{token.date:%d.%m.%Y}"
    except ValueError:
        entry, day = "Essensplan-Eintrag", "Unbekannt"
    return _permission(
        "meal_plan.delete",
        _operation_key("meal-plan-delete", arguments),
        arguments,
        context,
        title="Essensplan-Eintrag löschen?",
        summary=f'Der Agent möchte "{entry}" löschen.',
        display_items=(
            PermissionDisplayItem("Eintrag", entry),
            PermissionDisplayItem("Datum", day),
        ),
        resource_key="meal-plan",
    )


MEALIE_SHARED_TOOLS = (
    ToolContribution(
        name="mealie_list_accounts",
        description="List active Mealie accounts available to this tool binding.",
        arguments_model=MealieListAccountsArguments,
        handler=mealie_list_accounts,
        label="Mealie-Konten auflisten",
        icon="restaurant",
        actions=(_ACCOUNTS_READ,),
        instance_argument=None,
        authorization_factory=_accounts_permission,
        releases_private_data=True,
    ),
    ToolContribution(
        name="recipes_search",
        description="Search recipes in one Mealie account and return recipe tokens for recipe_get.",
        arguments_model=RecipesSearchArguments,
        handler=recipes_search,
        label="Rezepte suchen",
        icon="search",
        actions=(_RECIPES_READ,),
        instance_argument="integration_id",
        authorization_factory=_search_permission,
        releases_private_data=True,
    ),
    ToolContribution(
        name="recipe_get",
        description="Read ingredients and instructions for a recipe returned by recipes_search.",
        arguments_model=RecipeGetArguments,
        handler=recipe_get,
        label="Rezept lesen",
        icon="menu_book",
        actions=(_RECIPES_READ,),
        instance_argument="integration_id",
        authorization_factory=_recipe_get_permission,
        releases_private_data=True,
    ),
    ToolContribution(
        name="meal_plan_get",
        description="Read meal-plan entries in an inclusive date range.",
        arguments_model=MealPlanGetArguments,
        handler=meal_plan_get,
        label="Essensplan lesen",
        icon="calendar_month",
        actions=(_MEAL_PLAN_READ,),
        instance_argument="integration_id",
        authorization_factory=_meal_plan_get_permission,
        releases_private_data=True,
    ),
    ToolContribution(
        name="meal_plan_add",
        description=(
            "Add a meal-plan entry, optionally using a recipe token returned by a recipe tool."
        ),
        arguments_model=MealPlanAddArguments,
        handler=meal_plan_add,
        label="Essensplan-Eintrag erstellen",
        icon="event_available",
        actions=(_MEAL_PLAN_WRITE,),
        instance_argument="integration_id",
        authorization_factory=_meal_plan_add_permission,
        releases_private_data=True,
    ),
    ToolContribution(
        name="meal_plan_update",
        description="Update fields of a meal-plan entry using its latest mealPlanToken.",
        arguments_model=MealPlanUpdateArguments,
        handler=meal_plan_update,
        label="Essensplan-Eintrag ändern",
        icon="edit_calendar",
        actions=(_MEAL_PLAN_WRITE,),
        instance_argument="integration_id",
        authorization_factory=_meal_plan_update_permission,
        releases_private_data=True,
    ),
    ToolContribution(
        name="meal_plan_delete",
        description="Delete a meal-plan entry using its latest mealPlanToken.",
        arguments_model=MealPlanDeleteArguments,
        handler=meal_plan_delete,
        label="Essensplan-Eintrag löschen",
        icon="event_busy",
        actions=(_MEAL_PLAN_DELETE,),
        instance_argument="integration_id",
        authorization_factory=_meal_plan_delete_permission,
        releases_private_data=True,
    ),
)


MEALIE_ADAPTER = IntegrationAdapterDefinition(
    integration_type="recipes",
    adapter_type="mealie",
    config_model=MealieConfig,
    contribution_factory=lambda _instance: IntegrationContributions(),
    resource_actions=(
        _ACCOUNTS_READ,
        _RECIPES_READ,
        _MEAL_PLAN_READ,
        _MEAL_PLAN_WRITE,
        _MEAL_PLAN_DELETE,
    ),
    shared_tools=MEALIE_SHARED_TOOLS,
)
