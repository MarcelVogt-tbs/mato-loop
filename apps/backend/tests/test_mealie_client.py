import json
from dataclasses import replace
from datetime import date

import httpx
import pytest

from mato_loop_backend.integrations.mealie_client import (
    MealieClient,
    MealieError,
)

RECIPE_ID = "12345678-1234-5678-9234-567812345678"
GROUP_ID = "22345678-1234-5678-9234-567812345678"
USER_ID = "32345678-1234-5678-9234-567812345678"
HOUSEHOLD_ID = "42345678-1234-5678-9234-567812345678"
STEP_ID = "52345678-1234-5678-9234-567812345678"


def _summary() -> dict:
    return {
        "id": RECIPE_ID,
        "slug": "kartoffelsuppe",
        "name": "Kartoffelsuppe",
        "description": "Cremig",
        "recipeServings": 4,
        "recipeYieldQuantity": 2,
        "recipeYield": "Liter",
        "totalTime": "45 Minuten",
        "prepTime": "15 Minuten",
        "cookTime": "30 Minuten",
        "performTime": "45 Minuten",
        "recipeCategory": [{"name": "Suppe"}],
        "tags": [{"name": "Vegetarisch"}],
    }


def _entry() -> dict:
    return {
        "id": 7,
        "date": "2026-08-20",
        "entryType": "dinner",
        "title": "Abendessen",
        "text": "Mit Brot",
        "recipeId": RECIPE_ID,
        "recipe": _summary(),
        "groupId": GROUP_ID,
        "userId": USER_ID,
        "householdId": HOUSEHOLD_ID,
    }


def _client(handler, base_url: str = "https://mealie.example.test") -> MealieClient:
    return MealieClient(
        base_url,
        "api-token",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_search_uses_bearer_auth_deployment_path_and_current_pagination() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"items": [_summary()]})

    async with _client(handler, "http://mealie.example.test/kitchen/") as client:
        recipes = await client.search_recipes("  Suppe  ", limit=12)

    request = requests[0]
    assert request.headers["Authorization"] == "Bearer api-token"
    assert request.url.path == "/kitchen/api/recipes"
    assert dict(request.url.params) == {"page": "1", "perPage": "12", "search": "Suppe"}
    assert recipes[0].id == RECIPE_ID
    assert recipes[0].categories == ("Suppe",)
    assert recipes[0].tags == ("Vegetarisch",)


@pytest.mark.parametrize(
    "value",
    [
        "ftp://mealie.example.test",
        "https://user:pass@mealie.example.test",
        "https://mealie.example.test?x=1",
        "https://mealie.example.test#fragment",
        "https://mealie.example.test\\path",
        "https://mealie.example.test/\npath",
        "",
    ],
)
def test_invalid_base_urls_are_rejected(value: str) -> None:
    with pytest.raises(MealieError) as error:
        MealieClient(value, "token")
    assert error.value.code == "mealie.invalidUrl"


@pytest.mark.parametrize("token", ["", "two words", "line\nbreak"])
def test_invalid_bearer_tokens_are_rejected(token: str) -> None:
    with pytest.raises(MealieError) as error:
        MealieClient("https://mealie.example.test", token)
    assert error.value.code == "mealie.authenticationFailed"


async def test_connection_and_recipe_detail_use_fixed_api_paths_and_parse_fields() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/users/self"):
            return httpx.Response(200, json={"id": USER_ID})
        return httpx.Response(
            200,
            json={
                **_summary(),
                "recipeIngredient": [
                    {
                        "quantity": 2.5,
                        "unit": {"name": "kg"},
                        "food": {"name": "Kartoffeln"},
                        "note": "mehlig",
                        "display": "2,5 kg Kartoffeln",
                        "title": "Suppe",
                        "originalText": "2,5 kg Kartoffeln",
                    }
                ],
                "recipeInstructions": [
                    {"id": STEP_ID, "title": "Kochen", "summary": "", "text": "30 Minuten kochen"}
                ],
            },
        )

    async with _client(handler, "https://mealie.example.test/home") as client:
        await client.test_connection()
        recipe = await client.get_recipe("kartoffelsuppe")

    assert paths == ["/home/api/users/self", "/home/api/recipes/kartoffelsuppe"]
    assert recipe.recipe_servings == 4
    assert recipe.recipe_yield == "Liter"
    assert recipe.ingredients[0].food == "Kartoffeln"
    assert recipe.instructions[0].text == "30 Minuten kochen"


async def test_meal_plan_reads_use_date_query_and_parse_expanded_recipe() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/mealplans"):
            return httpx.Response(200, json={"items": [_entry()]})
        return httpx.Response(200, json=_entry())

    async with _client(handler) as client:
        entries = await client.get_meal_plans(
            date(2026, 8, 1), date(2026, 8, 31), limit=25
        )
        entry = await client.get_meal_plan(7)

    assert dict(requests[0].url.params) == {
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
        "page": "1",
        "perPage": "25",
    }
    assert requests[1].url.path == "/api/households/mealplans/7"
    assert entries[0].date == date(2026, 8, 20)
    assert entry.recipe is not None
    assert entry.recipe.slug == "kartoffelsuppe"


async def test_meal_plan_mutations_send_current_camel_case_bodies() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(201 if request.method == "POST" else 200, json=_entry())

    async with _client(handler) as client:
        created = await client.create_meal_plan(
            date(2026, 8, 20), "dinner", "Abendessen", "Mit Brot", RECIPE_ID
        )
        await client.update_meal_plan(replace(created, title="Spätes Abendessen"))
        await client.delete_meal_plan(7)

    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/api/households/mealplans"),
        ("PUT", "/api/households/mealplans/7"),
        ("DELETE", "/api/households/mealplans/7"),
    ]
    assert json.loads(requests[0].content) == {
        "date": "2026-08-20",
        "entryType": "dinner",
        "title": "Abendessen",
        "text": "Mit Brot",
        "recipeId": RECIPE_ID,
    }
    assert json.loads(requests[1].content) == {
        "id": 7,
        "date": "2026-08-20",
        "entryType": "dinner",
        "title": "Spätes Abendessen",
        "text": "Mit Brot",
        "recipeId": RECIPE_ID,
        "groupId": GROUP_ID,
        "userId": USER_ID,
        "householdId": HOUSEHOLD_ID,
    }


async def test_limits_dates_and_entry_types_are_validated() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    async with _client(handler) as client:
        with pytest.raises(MealieError) as recipe_limit:
            await client.search_recipes(limit=51)
        with pytest.raises(MealieError) as meal_limit:
            await client.get_meal_plans(date(2026, 8, 1), date(2026, 8, 2), 101)
        with pytest.raises(MealieError) as dates:
            await client.get_meal_plans(date(2026, 8, 2), date(2026, 8, 1))
        with pytest.raises(MealieError) as entry_type:
            await client.create_meal_plan(date(2026, 8, 1), "brunch", "x", "", None)  # type: ignore[arg-type]

    assert {error.value.code for error in (recipe_limit, meal_limit, dates, entry_type)} == {
        "mealie.invalidRequest"
    }


async def test_oversized_streamed_json_response_is_rejected() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{" + b"x" * 2048 + b"}")

    client = MealieClient(
        "https://mealie.example.test",
        "token",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_response_bytes=1024,
    )
    async with client:
        with pytest.raises(MealieError) as error:
            await client.search_recipes()
    assert error.value.code == "mealie.responseTooLarge"


async def test_redirects_and_read_statuses_map_without_response_text() -> None:
    statuses = {
        302: "mealie.redirectRejected",
        400: "mealie.invalidRequest",
        401: "mealie.authenticationFailed",
        403: "mealie.permissionDenied",
        404: "mealie.notFound",
        422: "mealie.invalidRequest",
        429: "mealie.rateLimited",
        500: "mealie.unexpectedStatus",
    }
    for status, code in statuses.items():
        def handler(_request: httpx.Request, status: int = status) -> httpx.Response:
            return httpx.Response(status, text="secret upstream response")

        async with _client(handler) as client:
            with pytest.raises(MealieError) as error:
                await client.search_recipes()
        assert error.value.code == code
        assert "secret" not in str(error.value)


async def test_malformed_json_pagination_and_models_are_rejected() -> None:
    responses = [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, json={"items": [{**_summary(), "id": "not-a-uuid"}]}),
    ]
    for response in responses:
        def handler(_request: httpx.Request, response: httpx.Response = response) -> httpx.Response:
            return response

        async with _client(handler) as client:
            with pytest.raises(MealieError) as error:
                await client.search_recipes()
        assert error.value.code == "mealie.malformedResponse"


async def test_remote_collections_are_bounded_before_model_parsing() -> None:
    summary_items = [_summary() for _ in range(21)]

    async with _client(
        lambda _request: httpx.Response(200, json={"items": summary_items})
    ) as client:
        with pytest.raises(MealieError) as listing_error:
            await client.search_recipes(limit=20)

    oversized_recipe = {
        **_summary(),
        "recipeIngredient": [{"display": "Salz"} for _ in range(201)],
        "recipeInstructions": [],
    }
    async with _client(
        lambda _request: httpx.Response(200, json=oversized_recipe)
    ) as client:
        with pytest.raises(MealieError) as recipe_error:
            await client.get_recipe("kartoffelsuppe")

    assert listing_error.value.code == "mealie.responseTooLarge"
    assert recipe_error.value.code == "mealie.responseTooLarge"


async def test_transport_and_mutation_server_failures_report_unknown_outcome() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    async with _client(unavailable) as client:
        with pytest.raises(MealieError) as read_error:
            await client.search_recipes()
        with pytest.raises(MealieError) as mutation_error:
            await client.create_meal_plan(
                date(2026, 8, 20), "dinner", "Abendessen", "", RECIPE_ID
            )

    def server_error(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="possibly completed")

    async with _client(server_error) as client:
        with pytest.raises(MealieError) as status_error:
            await client.delete_meal_plan(7)

    assert read_error.value.code == "mealie.unavailable"
    assert mutation_error.value.code == "mealie.outcomeUnknown"
    assert status_error.value.code == "mealie.outcomeUnknown"


async def test_ambiguous_mutation_responses_report_unknown_outcome() -> None:
    responses = [
        httpx.Response(302, headers={"Location": "https://other.example"}),
        httpx.Response(201, content=b"not-json"),
        httpx.Response(201, json={"id": 7}),
    ]
    for response in responses:
        def handler(
            _request: httpx.Request, response: httpx.Response = response
        ) -> httpx.Response:
            return response

        async with _client(handler) as client:
            with pytest.raises(MealieError) as error:
                await client.create_meal_plan(
                    date(2026, 8, 20), "dinner", "Abendessen", "", RECIPE_ID
                )
        assert error.value.code == "mealie.outcomeUnknown"


async def test_connection_requires_a_mealie_user_id() -> None:
    async with _client(lambda _request: httpx.Response(200, json={})) as client:
        with pytest.raises(MealieError) as error:
            await client.test_connection()

    assert error.value.code == "mealie.malformedResponse"
