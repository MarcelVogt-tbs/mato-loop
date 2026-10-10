from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from datetime import date as Date
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_RECIPE_RESULTS = 50
MAX_MEAL_PLAN_RESULTS = 100
MAX_RECIPE_PARTS = 200
MAX_RECIPE_NAMES = 100
MAX_INGREDIENT_LOOKUP_PAGES = 100

MealieEntryType = Literal[
    "breakfast", "lunch", "dinner", "side", "snack", "drink", "dessert"
]
_ENTRY_TYPES = {"breakfast", "lunch", "dinner", "side", "snack", "drink", "dessert"}
_MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class MealieError(RuntimeError):
    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class MealieRecipeSummary:
    id: str | None
    slug: str
    name: str
    description: str | None
    recipe_servings: float
    recipe_yield_quantity: float
    recipe_yield: str | None
    total_time: str | None
    prep_time: str | None
    cook_time: str | None
    perform_time: str | None
    categories: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MealieRecipeIngredient:
    quantity: float | None
    unit: str | None
    food: str | None
    note: str | None
    display: str
    title: str | None
    original_text: str | None


@dataclass(frozen=True, slots=True)
class MealieRecipeInstruction:
    id: str | None
    title: str | None
    summary: str | None
    text: str


@dataclass(frozen=True, slots=True)
class MealieRecipe(MealieRecipeSummary):
    ingredients: tuple[MealieRecipeIngredient, ...]
    instructions: tuple[MealieRecipeInstruction, ...]
    snapshot_hash: str


@dataclass(frozen=True, slots=True)
class MealieRecipePage:
    recipes: tuple[MealieRecipeSummary, ...]
    page: int
    total: int
    total_pages: int


@dataclass(frozen=True, slots=True)
class MealieMealPlanEntry:
    id: int
    date: Date
    entry_type: MealieEntryType
    title: str
    text: str
    recipe_id: str | None
    recipe: MealieRecipeSummary | None
    group_id: str
    user_id: str
    household_id: str


def _error(code: str, *, status_code: int | None = None) -> MealieError:
    return MealieError(f"mealie.{code}", status_code=status_code)


def _parse_url(value: str) -> httpx.URL:
    if (
        not isinstance(value, str)
        or not value
        or "?" in value
        or "#" in value
        or "\\" in value
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise _error("invalidUrl")
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, ValueError) as exc:
        raise _error("invalidUrl") from exc
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo:
        raise _error("invalidUrl")
    return url.copy_with(raw_path=url.raw_path.rstrip(b"/"))


def _uuid(value: Any, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise _error("malformedResponse")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise _error("malformedResponse") from exc


def _request_uuid(value: str | None, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise _error("invalidRequest")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise _error("invalidRequest") from exc


def _filter_literal(name: str) -> str:
    # Mealie's filter parser has no quoted-string escape syntax.
    if (
        not isinstance(name, str) or not name.strip() or len(name) > 500
        or any(character in '\"[]\\$' or ord(character) < 32 for character in name)
    ):
        raise _error("invalidRequest")
    return json.dumps(name.strip(), ensure_ascii=False)


def _string(value: Any, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise _error("malformedResponse")
    return value


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _error("malformedResponse")
    return float(value)


def _names(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _error("malformedResponse")
    if len(value) > MAX_RECIPE_NAMES:
        raise _error("responseTooLarge")
    names: list[str] = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise _error("malformedResponse")
        names.append(item["name"])
    return tuple(names)


def _recipe_summary(payload: Any) -> MealieRecipeSummary:
    if not isinstance(payload, dict):
        raise _error("malformedResponse")
    return MealieRecipeSummary(
        id=_uuid(payload.get("id"), optional=True),
        slug=_string(payload.get("slug", "")) or "",
        name=_string(payload.get("name"), optional=True) or "",
        description=_string(payload.get("description"), optional=True),
        recipe_servings=_number(payload.get("recipeServings", 0)),
        recipe_yield_quantity=_number(payload.get("recipeYieldQuantity", 0)),
        recipe_yield=_string(payload.get("recipeYield"), optional=True),
        total_time=_string(payload.get("totalTime"), optional=True),
        prep_time=_string(payload.get("prepTime"), optional=True),
        cook_time=_string(payload.get("cookTime"), optional=True),
        perform_time=_string(payload.get("performTime"), optional=True),
        categories=_names(payload.get("recipeCategory")),
        tags=_names(payload.get("tags")),
    )


def _named_object(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        raise _error("malformedResponse")
    return value["name"]


def _ingredient(payload: Any) -> MealieRecipeIngredient:
    if not isinstance(payload, dict):
        raise _error("malformedResponse")
    quantity = payload.get("quantity")
    if quantity is not None:
        quantity = _number(quantity)
    return MealieRecipeIngredient(
        quantity=quantity,
        unit=_named_object(payload.get("unit")),
        food=_named_object(payload.get("food")),
        note=_string(payload.get("note"), optional=True),
        display=_string(payload.get("display", "")) or "",
        title=_string(payload.get("title"), optional=True),
        original_text=_string(payload.get("originalText"), optional=True),
    )


def _instruction(payload: Any) -> MealieRecipeInstruction:
    if not isinstance(payload, dict):
        raise _error("malformedResponse")
    instruction_id = payload.get("id")
    return MealieRecipeInstruction(
        id=_uuid(instruction_id, optional=True),
        title=_string(payload.get("title"), optional=True),
        summary=_string(payload.get("summary"), optional=True),
        text=_string(payload.get("text")) or "",
    )


def _recipe(payload: Any) -> MealieRecipe:
    summary = _recipe_summary(payload)
    ingredients = payload.get("recipeIngredient", [])
    instructions = payload.get("recipeInstructions")
    if instructions is None:
        instructions = []
    if not isinstance(ingredients, list) or not isinstance(instructions, list):
        raise _error("malformedResponse")
    if len(ingredients) > MAX_RECIPE_PARTS or len(instructions) > MAX_RECIPE_PARTS:
        raise _error("responseTooLarge")
    return MealieRecipe(
        **{field.name: getattr(summary, field.name) for field in fields(summary)},
        ingredients=tuple(_ingredient(item) for item in ingredients),
        instructions=tuple(_instruction(item) for item in instructions),
        snapshot_hash=hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )


def _date(value: Any) -> Date:
    if not isinstance(value, str):
        raise _error("malformedResponse")
    try:
        return Date.fromisoformat(value)
    except ValueError as exc:
        raise _error("malformedResponse") from exc


def _request_date(value: Date) -> str:
    if not isinstance(value, Date):
        raise _error("invalidRequest")
    return value.isoformat()


def _entry_type(value: Any, *, response: bool) -> MealieEntryType:
    if not isinstance(value, str) or value not in _ENTRY_TYPES:
        raise _error("malformedResponse" if response else "invalidRequest")
    return value


def _identifier(value: Any, *, response: bool) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _error("malformedResponse" if response else "invalidRequest")
    return value


def _meal_plan(payload: Any) -> MealieMealPlanEntry:
    if not isinstance(payload, dict):
        raise _error("malformedResponse")
    recipe_payload = payload.get("recipe")
    if recipe_payload is not None and not isinstance(recipe_payload, dict):
        raise _error("malformedResponse")
    return MealieMealPlanEntry(
        id=_identifier(payload.get("id"), response=True),
        date=_date(payload.get("date")),
        entry_type=_entry_type(payload.get("entryType", "breakfast"), response=True),
        title=_string(payload.get("title", "")) or "",
        text=_string(payload.get("text", "")) or "",
        recipe_id=_uuid(payload.get("recipeId"), optional=True),
        recipe=_recipe_summary(recipe_payload) if recipe_payload is not None else None,
        group_id=_uuid(payload.get("groupId")) or "",
        user_id=_uuid(payload.get("userId")) or "",
        household_id=_uuid(payload.get("householdId")) or "",
    )


class MealieClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self._base_url = _parse_url(base_url)
        if (
            not isinstance(token, str)
            or not token
            or any(character.isspace() or ord(character) < 32 for character in token)
        ):
            raise _error("authenticationFailed")
        if max_response_bytes < 1 or max_response_bytes > MAX_RESPONSE_BYTES:
            raise _error("invalidRequest")
        self._authorization = f"Bearer {token}"
        self._client = client or httpx.AsyncClient(timeout=30, trust_env=False)
        self._owns_client = client is None
        self._max_response_bytes = max_response_bytes
        self._mutation_count = 0

    async def __aenter__(self) -> MealieClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _url(self, path: str, params: dict[str, Any] | None = None) -> httpx.URL:
        base_path = self._base_url.raw_path.rstrip(b"/")
        url = self._base_url.copy_with(raw_path=base_path + b"/api" + path.encode())
        return url.copy_with(params=params) if params else url

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        expected: set[int] = frozenset({200}),
        discard_response: bool = False,
    ) -> Any:
        headers = {"Authorization": self._authorization, "Accept": "application/json"}
        content = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            content = json.dumps(payload, ensure_ascii=False).encode()
        request = self._client.build_request(
            method, self._url(path, params), headers=headers, content=content
        )
        mutation = method in _MUTATION_METHODS

        def response_error(code: str, *, status_code: int | None = None) -> MealieError:
            return _error(
                "outcomeUnknown" if mutation else code,
                status_code=status_code,
            )

        try:
            response = await self._client.send(
                request, stream=True, follow_redirects=False
            )
        except httpx.HTTPError as exc:
            raise _error(
                "outcomeUnknown" if mutation else "unavailable"
            ) from exc
        try:
            status = response.status_code
            if 300 <= status < 400:
                raise response_error("redirectRejected", status_code=status)
            if status not in expected:
                code = _STATUS_CODES.get(status)
                if mutation and code not in {
                    "invalidRequest",
                    "authenticationFailed",
                    "permissionDenied",
                    "notFound",
                    "rateLimited",
                }:
                    code = "outcomeUnknown"
                code = code or "unexpectedStatus"
                raise _error(code, status_code=status)
            if mutation:
                self._mutation_count += 1
            if discard_response:
                return None
            length = response.headers.get("content-length")
            if length is not None:
                try:
                    if int(length) > self._max_response_bytes:
                        raise response_error("responseTooLarge")
                except ValueError as exc:
                    raise response_error("malformedResponse") from exc
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self._max_response_bytes:
                    raise response_error("responseTooLarge")
                chunks.append(chunk)
            body = b"".join(chunks)
        except httpx.HTTPError as exc:
            raise response_error("unavailable") from exc
        finally:
            await response.aclose()
        if not body:
            return None
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise response_error("malformedResponse") from exc

    @staticmethod
    def _items(payload: Any, limit: int) -> list[Any]:
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise _error("malformedResponse")
        items = payload["items"]
        if len(items) > limit:
            raise _error("responseTooLarge")
        return items

    async def test_connection(self) -> None:
        payload = await self._request("GET", "/users/self")
        if not isinstance(payload, dict):
            raise _error("malformedResponse")
        _uuid(payload.get("id"))

    async def search_recipes(
        self, query: str | None = None, limit: int = 20, *, page: int = 1,
        tags: tuple[str, ...] = (), categories: tuple[str, ...] = (),
        ingredients: tuple[str, ...] = (),
    ) -> MealieRecipePage:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RECIPE_RESULTS:
            raise _error("invalidRequest")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise _error("invalidRequest")
        params: dict[str, Any] = {"page": page, "perPage": limit, "orderBy": "id"}
        if query is not None:
            if not isinstance(query, str):
                raise _error("invalidRequest")
            if cleaned := query.strip():
                params["search"] = cleaned
        filters = []
        for attribute, names in (
            ("tags.name", tags), ("recipeCategory.name", categories),
            ("recipeIngredient.food.name", ingredients),
        ):
            for name in names:
                filters.append(f"{attribute} = {_filter_literal(name)}")
        if filters:
            params["queryFilter"] = " AND ".join(filters)
        payload = await self._request("GET", "/recipes", params=params)
        recipes = tuple(_recipe_summary(item) for item in self._items(payload, limit))
        for field in ("page", "total", "total_pages"):
            value = payload.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise _error("malformedResponse")
        if payload["page"] != page:
            raise _error("malformedResponse")
        return MealieRecipePage(recipes, page, payload["total"], payload["total_pages"])

    async def get_recipe(self, slug_or_id: str) -> MealieRecipe:
        if not isinstance(slug_or_id, str) or not slug_or_id:
            raise _error("invalidRequest")
        segment = quote(slug_or_id, safe="")
        return _recipe(await self._request("GET", f"/recipes/{segment}"))

    async def _created_recipe(
        self, slug: Any, changes: dict[str, Any] | None = None,
    ) -> MealieRecipe:
        try:
            if not isinstance(slug, str) or not slug.strip() or len(slug) > 1_000:
                raise _error("malformedResponse")
            if changes:
                return await self.update_recipe(slug, changes)
            recipe = await self.get_recipe(slug)
            if recipe.id is None:
                raise _error("malformedResponse")
            return recipe
        except MealieError as exc:
            # Creation already succeeded; a failed follow-up may leave a partial recipe.
            raise _error("outcomeUnknown") from exc

    async def create_recipe(self, name: str, changes: dict[str, Any]) -> MealieRecipe:
        slug = await self._request("POST", "/recipes", payload={"name": name}, expected={201})
        return await self._created_recipe(slug, changes)

    async def import_recipe(self, url: str) -> MealieRecipe:
        slug = await self._request(
            "POST", "/recipes/create/url", payload={"url": url}, expected={201},
        )
        return await self._created_recipe(slug)

    async def _ingredient_reference(
        self, kind: Literal["food", "unit"], name: str,
    ) -> dict[str, str]:
        name = name.strip()
        if not name:
            raise _error("invalidRequest")
        path = {"food": "/foods", "unit": "/units"}[kind]
        fields = ("name", "pluralName", "aliases.name")
        if kind == "unit":
            fields += ("abbreviation", "pluralAbbreviation")
        params: dict[str, Any] = {"perPage": 100, "orderBy": "id"}
        try:
            literal = _filter_literal(name)
        except MealieError:
            # Names unsupported by the filter grammar still have to resolve exactly.
            pass
        else:
            params["queryFilter"] = " OR ".join(f"{field} = {literal}" for field in fields)
        for page in range(1, MAX_INGREDIENT_LOOKUP_PAGES + 1):
            payload = await self._request("GET", path, params={**params, "page": page})
            for item in self._items(payload, 100):
                if not isinstance(item, dict):
                    raise _error("malformedResponse")
                names = [_string(item.get(field), optional=True) for field in fields if field != "aliases.name"]
                names.extend(_names(item.get("aliases")))
                if any(value is not None and value.casefold() == name.casefold() for value in names):
                    return {"id": _uuid(item.get("id")), "name": _string(item.get("name"))}
            total_pages = payload.get("total_pages")
            if isinstance(total_pages, bool) or not isinstance(total_pages, int) or total_pages < 0:
                raise _error("malformedResponse")
            if page >= total_pages:
                break
        else:
            raise _error("responseTooLarge")
        payload = await self._request("POST", path, payload={"name": name}, expected={201})
        if not isinstance(payload, dict):
            raise _error("malformedResponse")
        return {"id": _uuid(payload.get("id")), "name": _string(payload.get("name"))}

    async def update_recipe(self, slug_or_id: str, changes: dict[str, Any]) -> MealieRecipe:
        before = self._mutation_count
        try:
            references: dict[tuple[str, str], dict[str, str]] = {}
            for ingredient in changes.get("recipeIngredient", []):
                ingredient["referenceId"] = str(uuid4())
                for kind in ("food", "unit"):
                    if reference := ingredient.get(kind):
                        key = (kind, reference["name"].strip().casefold())
                        if key not in references:
                            references[key] = await self._ingredient_reference(kind, reference["name"])
                        ingredient[kind] = references[key]
            payload = await self._request(
                "PATCH", f"/recipes/{quote(slug_or_id, safe='')}", payload=changes,
            )
            recipe = _recipe(payload)
            if recipe.id is None:
                raise _error("malformedResponse")
            return recipe
        except MealieError as exc:
            if self._mutation_count != before:
                # Food/unit creation may have succeeded before a later step failed.
                raise _error("outcomeUnknown") from exc
            raise

    async def delete_recipe(self, recipe_id: str) -> None:
        recipe_id = _request_uuid(recipe_id)
        await self._request(
            "DELETE", f"/recipes/{recipe_id}", expected={200, 204}, discard_response=True,
        )

    async def get_meal_plans(
        self, start_date: Date, end_date: Date, limit: int = 100
    ) -> tuple[MealieMealPlanEntry, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_MEAL_PLAN_RESULTS:
            raise _error("invalidRequest")
        start = _request_date(start_date)
        end = _request_date(end_date)
        if start_date > end_date:
            raise _error("invalidRequest")
        payload = await self._request(
            "GET",
            "/households/mealplans",
            params={"start_date": start, "end_date": end, "page": 1, "perPage": limit},
        )
        return tuple(_meal_plan(item) for item in self._items(payload, limit))

    async def get_meal_plan(self, meal_plan_id: int) -> MealieMealPlanEntry:
        identifier = _identifier(meal_plan_id, response=False)
        return _meal_plan(
            await self._request("GET", f"/households/mealplans/{identifier}")
        )

    async def create_meal_plan(
        self,
        date: Date,
        entry_type: MealieEntryType,
        title: str,
        text: str,
        recipe_id: str | None,
    ) -> MealieMealPlanEntry:
        if not isinstance(title, str) or not isinstance(text, str):
            raise _error("invalidRequest")
        payload = {
            "date": _request_date(date),
            "entryType": _entry_type(entry_type, response=False),
            "title": title,
            "text": text,
            "recipeId": _request_uuid(recipe_id, optional=True),
        }
        try:
            return _meal_plan(await self._request(
                "POST",
                "/households/mealplans",
                payload=payload,
                expected={200, 201},
            ))
        except MealieError as exc:
            if exc.code == "mealie.malformedResponse":
                raise _error("outcomeUnknown") from exc
            raise

    async def update_meal_plan(
        self, entry: MealieMealPlanEntry
    ) -> MealieMealPlanEntry:
        if not isinstance(entry, MealieMealPlanEntry):
            raise _error("invalidRequest")
        identifier = _identifier(entry.id, response=False)
        payload = {
            "id": identifier,
            "date": _request_date(entry.date),
            "entryType": _entry_type(entry.entry_type, response=False),
            "title": entry.title,
            "text": entry.text,
            "recipeId": _request_uuid(entry.recipe_id, optional=True),
            "groupId": _request_uuid(entry.group_id),
            "userId": _request_uuid(entry.user_id),
            "householdId": _request_uuid(entry.household_id),
        }
        try:
            return _meal_plan(await self._request(
                "PUT", f"/households/mealplans/{identifier}", payload=payload
            ))
        except MealieError as exc:
            if exc.code == "mealie.malformedResponse":
                raise _error("outcomeUnknown") from exc
            raise

    async def delete_meal_plan(self, meal_plan_id: int) -> None:
        identifier = _identifier(meal_plan_id, response=False)
        await self._request(
            "DELETE",
            f"/households/mealplans/{identifier}",
            expected={200, 204},
            discard_response=True,
        )


_STATUS_CODES = {
    400: "invalidRequest",
    401: "authenticationFailed",
    403: "permissionDenied",
    404: "notFound",
    422: "invalidRequest",
    429: "rateLimited",
}
