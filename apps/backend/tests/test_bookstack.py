import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

import mato_loop_backend.integrations.bookstack as bookstack_module
import mato_loop_backend.integrations.webfetch as webfetch_module
from mato_loop_backend.agent.permissions import PermissionDecision
from mato_loop_backend.integrations.bookstack import (
    BOOKSTACK_ADAPTER,
    KnowledgeBookArguments,
    KnowledgeCreateAttachmentArguments,
    KnowledgeCreateBookArguments,
    KnowledgeCreatePageArguments,
    KnowledgeCreateShelfArguments,
    KnowledgeDeleteAttachmentArguments,
    KnowledgeDeletePageArguments,
    KnowledgeIntegrationArguments,
    KnowledgeListAccountsArguments,
    KnowledgeListAttachmentsArguments,
    KnowledgeSearchArguments,
    KnowledgeShelfArguments,
    KnowledgeUpdateBookArguments,
    KnowledgeUpdateChapterArguments,
    KnowledgeUpdatePageArguments,
    KnowledgeUpdateShelfArguments,
    knowledge_create_attachment,
    knowledge_create_book,
    knowledge_create_page,
    knowledge_create_shelf,
    knowledge_delete_attachment,
    knowledge_delete_page,
    knowledge_delete_shelf,
    knowledge_list_accounts,
    knowledge_list_attachments,
    knowledge_list_books,
    knowledge_list_shelves,
    knowledge_read_book,
    knowledge_read_shelf,
    knowledge_search,
    knowledge_update_book,
    knowledge_update_chapter,
    knowledge_update_page,
    knowledge_update_shelf,
    load_bookstack_resources,
)
from mato_loop_backend.integrations.bookstack_client import (
    BookStackAttachment,
    BookStackBook,
    BookStackBookContents,
    BookStackChapter,
    BookStackError,
    BookStackPage,
    BookStackSearchResult,
    BookStackShelf,
    BookStackShelfContents,
)
from mato_loop_backend.integrations.registry import (
    effective_integration_contributions,
    integration_adapter,
)
from mato_loop_backend.integrations.webfetch import DownloadedFile, WebFetchError
from mato_loop_backend.settings import (
    IntegrationInstance,
    IntegrationSettings,
    ToolExecutionPolicy,
    ToolSettings,
    WebFetchPolicy,
)
from mato_loop_backend.workspace import RootFileError


def _base(*, name: str = "Wiki") -> IntegrationInstance:
    return IntegrationInstance(
        name=name,
        integration_type="knowledge",
        adapter_type="bookstack",
        config={"base_url": "https://wiki.example.com"},
    )


def _revision(instance: IntegrationInstance) -> str:
    return json.dumps(
        instance.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _context(instance: IntegrationInstance, revision: str) -> SimpleNamespace:
    return SimpleNamespace(
        bound_options={
            "integration_id": str(instance.id),
            "binding_revision": revision,
        },
        integration_settings=IntegrationSettings(instances=[instance]),
    )


def _page(**overrides) -> BookStackPage:
    values = {
        "id": 21,
        "book_id": 7,
        "chapter_id": None,
        "name": "Seite",
        "slug": "seite",
        "updated_at": "2026-01-01T10:00:00.000000Z",
    }
    values.update(overrides)
    return BookStackPage(**values)


def _bind_location_checks(
    client: AsyncMock,
    *,
    book_id: int = 7,
    page_id: int = 21,
    chapter_id: int = 5,
    attachment_page_id: int = 21,
) -> None:
    """Give a mocked client answers for the live book-membership checks.

    Write operations re-read the current book of the page, chapter or
    attachment they touch, so every mock needs these lookups to resolve to the
    approved book.
    """
    client.read_page_location.return_value = _page(id=page_id, book_id=book_id)
    client.read_chapter.return_value = BookStackChapter(
        id=chapter_id, book_id=book_id, name="Kapitel", slug="kapitel"
    )
    client.read_attachment_location.return_value = BookStackAttachment(
        id=12, name="Datenblatt", page_id=attachment_page_id, external=False,
        extension="pdf",
    )


async def test_resource_loader_maps_books_to_policy_resources(monkeypatch) -> None:
    client = AsyncMock()
    client.list_books.return_value = (
        (
            BookStackBook(id=7, name="  Handbuch  ", slug="handbuch"),
            BookStackBook(id=8, name="X" * 200, slug="x"),
        ),
        False,
    )
    context = AsyncMock()
    context.__aenter__.return_value = client
    constructor = Mock(return_value=context)
    monkeypatch.setattr(bookstack_module, "BookStackClient", constructor)

    resources = await load_bookstack_resources(
        _base(), {"token_id": "id", "token_secret": "secret"}
    )

    constructor.assert_called_once_with("https://wiki.example.com/", "id", "secret")
    assert resources[0].key == "book:7"
    assert resources[0].label == "Handbuch"
    assert resources[0].hard_unavailable_capabilities == frozenset()
    assert len(resources[1].label) == 160


def test_adapter_declares_stable_shared_tools_and_policy_modes() -> None:
    first = _base(name="Wiki")
    second = _base(name="Archiv")
    contributions = effective_integration_contributions(
        IntegrationSettings(instances=[first, second])
    )
    tools = {tool.name: tool for tool in contributions.tools}

    assert integration_adapter("knowledge", "bookstack") is BOOKSTACK_ADAPTER
    assert set(tools) == {
        "knowledge_list_accounts",
        "knowledge_list_books",
        "knowledge_search",
        "knowledge_read_book",
        "knowledge_read_page",
        "knowledge_create_page",
        "knowledge_update_page",
        "knowledge_delete_page",
        "knowledge_create_book",
        "knowledge_update_book",
        "knowledge_delete_book",
        "knowledge_create_chapter",
        "knowledge_update_chapter",
        "knowledge_delete_chapter",
        "knowledge_list_shelves",
        "knowledge_read_shelf",
        "knowledge_create_shelf",
        "knowledge_update_shelf",
        "knowledge_delete_shelf",
        "knowledge_list_attachments",
        "knowledge_create_attachment",
        "knowledge_delete_attachment",
    }
    assert all(tool.write is False for tool in tools.values())
    assert all(tool.releases_private_data for tool in tools.values())
    assert tools["knowledge_list_accounts"].instance_argument is None
    assert tools["knowledge_list_accounts"].integration_ids == (
        str(first.id),
        str(second.id),
    )
    assert tools["knowledge_search"].instance_argument == "integration_id"
    assert tools["knowledge_search"].resource_argument is None
    assert tools["knowledge_create_book"].resource_argument is None
    for name in (
        "knowledge_list_shelves",
        "knowledge_read_shelf",
        "knowledge_create_shelf",
        "knowledge_update_shelf",
        "knowledge_delete_shelf",
    ):
        assert tools[name].resource_argument is None
        assert tools[name].instance_argument == "integration_id"
    for name in (
        "knowledge_read_book",
        "knowledge_read_page",
        "knowledge_create_page",
        "knowledge_update_page",
        "knowledge_delete_page",
        "knowledge_update_book",
        "knowledge_delete_book",
        "knowledge_create_chapter",
        "knowledge_update_chapter",
        "knowledge_delete_chapter",
    ):
        assert tools[name].resource_argument == "book_key"
    actions = {action.capability: action for action in BOOKSTACK_ADAPTER.resource_actions}
    assert [item.value for item in actions["knowledge.read"].allowed_policies] == [
        "disabled",
        "ask",
        "auto",
    ]
    assert [item.value for item in actions["knowledge.delete"].allowed_policies] == [
        "disabled",
        "ask",
    ]


async def test_account_list_uses_only_bound_active_bookstack_snapshot() -> None:
    first = _base(name="Wiki")
    second = _base(name="Archiv")
    disabled = _base(name="Aus")
    disabled.enabled = False
    context = SimpleNamespace(
        bound_options={"integration_ids": json.dumps([str(first.id), str(disabled.id)])},
        integration_settings=IntegrationSettings(instances=[first, second, disabled]),
    )

    result = await knowledge_list_accounts(KnowledgeListAccountsArguments(), context)

    assert json.loads(result.content) == {
        "accounts": [{"integrationId": str(first.id), "name": "Wiki"}],
        "contentIsUntrusted": True,
    }


async def test_page_token_is_binding_scoped(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    token = bookstack_module._page_token(_page(), instance.id, revision, "book:7")
    client = AsyncMock()
    client.read_page.return_value = _page()
    client.update_page.return_value = _page(name="Neu")
    _bind_location_checks(client)

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)
    arguments = KnowledgeUpdatePageArguments(
        integration_id=instance.id,
        book_key="book:7",
        page_token=token,
        markdown="Neuer Text",
    )

    result = await knowledge_update_page(arguments, _context(instance, revision))

    assert client.update_page.await_args.args[0] == 21
    assert client.update_page.await_args.kwargs["markdown"] == "Neuer Text"
    assert client.update_page.await_args.kwargs["name"] is None
    assert json.loads(result.content)["contentIsUntrusted"] is True

    foreign = SimpleNamespace(
        bound_options={
            "integration_id": str(uuid4()),
            "binding_revision": revision,
        },
        integration_settings=IntegrationSettings(instances=[instance]),
    )
    assert (await knowledge_update_page(arguments, foreign)).is_error is True


async def test_page_outside_the_approved_book_is_refused(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    token = bookstack_module._page_token(_page(), instance.id, revision, "book:7")
    client = AsyncMock()
    client.read_page.return_value = _page(book_id=9)

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)
    arguments = KnowledgeDeletePageArguments(
        integration_id=instance.id, book_key="book:7", page_token=token
    )

    result = await knowledge_delete_page(arguments, _context(instance, revision))

    assert result.is_error is True
    client.delete_page.assert_not_awaited()


async def test_chapter_token_of_another_book_is_refused(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    chapter = BookStackChapter(id=11, book_id=9, name="Kapitel", slug="kapitel")
    token = bookstack_module._chapter_token(chapter, instance.id, revision, "book:9")
    client = AsyncMock()

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)
    arguments = KnowledgeCreatePageArguments(
        integration_id=instance.id,
        book_key="book:7",
        name="Neu",
        markdown="Text",
        chapter_token=token,
    )

    result = await knowledge_create_page(arguments, _context(instance, revision))

    assert result.is_error is True
    client.create_page.assert_not_awaited()


async def test_page_creation_targets_the_approved_book(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    client = AsyncMock()
    client.create_page.return_value = _page(id=30, name="Neu")

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)
    arguments = KnowledgeCreatePageArguments(
        integration_id=instance.id,
        book_key="book:7",
        name="Neu",
        markdown="Text",
    )

    result = await knowledge_create_page(arguments, _context(instance, revision))

    assert client.create_page.await_args.kwargs["book_id"] == 7
    assert client.create_page.await_args.kwargs["chapter_id"] is None
    payload = json.loads(result.content)
    assert payload["page"]["name"] == "Neu"
    assert "pageToken" in payload["page"]


async def test_shelf_tools_map_keys_to_bookstack_identifiers(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    shelf = BookStackShelf(id=4, name="Technik", slug="technik")
    client = AsyncMock()
    client.list_shelves.return_value = ((shelf,), False)
    client.read_shelf.return_value = BookStackShelfContents(
        shelf=shelf, books=(BookStackBook(id=7, name="Handbuch", slug="handbuch"),)
    )
    client.create_shelf.return_value = shelf
    client.update_shelf.return_value = shelf

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)
    context = _context(instance, revision)

    listing = await knowledge_list_shelves(
        KnowledgeIntegrationArguments(integration_id=instance.id), context
    )
    assert json.loads(listing.content)["shelves"] == [
        {"shelfKey": "shelf:4", "name": "Technik", "description": None}
    ]

    contents = await knowledge_read_shelf(
        KnowledgeShelfArguments(integration_id=instance.id, shelf_key="shelf:4"), context
    )
    client.read_shelf.assert_awaited_once_with(4)
    assert json.loads(contents.content)["books"] == [
        {"bookKey": "book:7", "name": "Handbuch", "description": None}
    ]

    await knowledge_create_shelf(
        KnowledgeCreateShelfArguments(
            integration_id=instance.id,
            name="Technik",
            book_keys=["book:7", "book:8"],
        ),
        context,
    )
    assert client.create_shelf.await_args.kwargs["book_ids"] == (7, 8)

    await knowledge_delete_shelf(
        KnowledgeShelfArguments(integration_id=instance.id, shelf_key="shelf:4"), context
    )
    client.delete_shelf.assert_awaited_once_with(4)


async def test_shelf_update_sends_only_provided_fields(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    client = AsyncMock()
    client.update_shelf.return_value = BookStackShelf(
        id=4, name="Technik", slug="technik"
    )

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    await knowledge_update_shelf(
        KnowledgeUpdateShelfArguments(
            integration_id=instance.id, shelf_key="shelf:4", book_keys=[]
        ),
        _context(instance, revision),
    )

    assert client.update_shelf.await_args.args[0] == 4
    assert client.update_shelf.await_args.kwargs["name"] is None
    assert client.update_shelf.await_args.kwargs["description"] is None
    assert client.update_shelf.await_args.kwargs["book_ids"] == ()


def test_shelf_arguments_reject_invalid_keys_and_empty_patches() -> None:
    with pytest.raises(ValidationError):
        KnowledgeShelfArguments(integration_id=uuid4(), shelf_key="book:4")
    with pytest.raises(ValidationError):
        KnowledgeCreateShelfArguments(
            integration_id=uuid4(),
            name="Technik",
            book_keys=["shelf:7"],
        )
    with pytest.raises(ValidationError):
        KnowledgeCreateShelfArguments(
            integration_id=uuid4(),
            name="Technik",
            book_keys=["book:7", "book:7"],
        )
    with pytest.raises(ValidationError):
        KnowledgeUpdateShelfArguments(integration_id=uuid4(), shelf_key="shelf:4")

async def test_update_page_treats_explicit_nulls_as_absent_and_keeps_tokens_short(
    monkeypatch,
) -> None:
    instance = _base()
    revision = _revision(instance)
    token = bookstack_module._page_token(_page(), instance.id, revision, "book:7")
    assert len(token) < 300

    client = AsyncMock()
    client.read_page.return_value = _page()
    client.update_page.return_value = _page(name="IP-Adressen")
    _bind_location_checks(client)

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    arguments = KnowledgeUpdatePageArguments.model_validate(
        {
            "integration_id": str(instance.id),
            "book_key": "book:7",
            "page_token": token,
            "name": None,
            "markdown": "# IP-Adressen",
        }
    )
    result = await knowledge_update_page(arguments, _context(instance, revision))

    assert result.is_error is False
    assert client.update_page.await_args.kwargs["name"] is None
    assert client.update_page.await_args.kwargs["markdown"] == "# IP-Adressen"

    with pytest.raises(ValidationError):
        KnowledgeUpdatePageArguments.model_validate(
            {
                "integration_id": str(instance.id),
                "book_key": "book:7",
                "page_token": token,
                "name": None,
                "markdown": None,
            }
        )


async def test_update_shelf_treats_explicit_nulls_as_absent(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    client = AsyncMock()
    client.update_shelf.return_value = BookStackShelf(
        id=4, name="Home-IT", slug="home-it"
    )

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    arguments = KnowledgeUpdateShelfArguments.model_validate(
        {
            "integration_id": str(instance.id),
            "shelf_key": "shelf:4",
            "name": "Home-IT",
            "description": None,
            "book_keys": None,
        }
    )
    result = await knowledge_update_shelf(arguments, _context(instance, revision))

    assert result.is_error is False
    assert client.update_shelf.await_args.kwargs["description"] is None
    assert client.update_shelf.await_args.kwargs["book_ids"] is None

    with pytest.raises(ValidationError):
        KnowledgeUpdateShelfArguments.model_validate(
            {
                "integration_id": str(instance.id),
                "shelf_key": "shelf:4",
                "name": None,
                "description": None,
                "book_keys": None,
            }
        )

async def test_update_page_returns_stored_markdown_for_verification(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    token = bookstack_module._page_token(_page(), instance.id, revision, "book:7")
    stored = _page(name="IP-Adressen", markdown="# IP-Adressen\n\nDHCP: 100-199\n")

    client = AsyncMock()
    client.read_page.return_value = _page()
    client.update_page.return_value = stored
    _bind_location_checks(client)

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    result = await knowledge_update_page(
        KnowledgeUpdatePageArguments(
            integration_id=instance.id,
            book_key="book:7",
            page_token=token,
            markdown="# IP-Adressen\n\nDHCP: 101-199\n",
        ),
        _context(instance, revision),
    )

    assert json.loads(result.content)["page"]["markdown"] == (
        "# IP-Adressen\n\nDHCP: 100-199\n"
    )


async def test_update_page_reloads_when_bookstack_omits_markdown(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    token = bookstack_module._page_token(_page(), instance.id, revision, "book:7")

    client = AsyncMock()
    client.read_page.return_value = _page(markdown="# Aus BookStack\n")
    client.update_page.return_value = _page()
    _bind_location_checks(client)

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    result = await knowledge_update_page(
        KnowledgeUpdatePageArguments(
            integration_id=instance.id,
            book_key="book:7",
            page_token=token,
            markdown="# Neu\n",
        ),
        _context(instance, revision),
    )

    # The membership check no longer loads content, so exactly one full read
    # remains: the reload that fetches the stored markdown.
    assert client.read_page.await_count == 1
    assert json.loads(result.content)["page"]["markdown"] == "# Aus BookStack\n"


async def test_create_book_files_the_book_into_requested_shelves(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    shelf = BookStackShelf(id=10, name="Home-IT", slug="home-it")
    client = AsyncMock()
    client.create_book.return_value = BookStackBook(
        id=13, name="Geraete", slug="geraete"
    )
    client.read_shelf.return_value = BookStackShelfContents(
        shelf=shelf, books=(BookStackBook(id=8, name="Netzwerk", slug="netzwerk"),)
    )

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    result = await knowledge_create_book(
        KnowledgeCreateBookArguments(
            integration_id=instance.id,
            name="Geraete",
            shelf_keys=["shelf:10"],
        ),
        _context(instance, revision),
    )

    client.read_shelf.assert_awaited_once_with(10)
    assert client.update_shelf.await_args.args[0] == 10
    assert client.update_shelf.await_args.kwargs["book_ids"] == (8, 13)
    assert json.loads(result.content)["book"]["shelfKeys"] == ["shelf:10"]


async def test_create_book_without_shelves_reports_empty_membership(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    client = AsyncMock()
    client.create_book.return_value = BookStackBook(id=13, name="Geraete", slug="g")

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    result = await knowledge_create_book(
        KnowledgeCreateBookArguments(
            integration_id=instance.id, name="Geraete"
        ),
        _context(instance, revision),
    )

    client.update_shelf.assert_not_awaited()
    assert json.loads(result.content)["book"]["shelfKeys"] == []


async def test_update_book_changes_metadata_only(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    client = AsyncMock()
    client.update_book.return_value = BookStackBook(
        id=7, name="Geraete neu", slug="geraete", description="Beschreibung"
    )

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    result = await knowledge_update_book(
        KnowledgeUpdateBookArguments(
            integration_id=instance.id,
            book_key="book:7",
            name="Geraete neu",
        ),
        _context(instance, revision),
    )

    client.update_book.assert_awaited_once_with(7, name="Geraete neu", description=None)
    client.update_shelf.assert_not_awaited()
    payload = json.loads(result.content)["book"]
    assert payload["bookKey"] == "book:7"
    assert payload["name"] == "Geraete neu"
    # Shelf membership is managed through shelves, so it is not reported here.
    assert "shelfKeys" not in payload


async def test_update_book_requires_a_changed_field() -> None:
    with pytest.raises(ValidationError):
        KnowledgeUpdateBookArguments(integration_id=uuid4(), book_key="book:7")


async def test_update_chapter_uses_the_token_of_the_approved_book(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    chapter = BookStackChapter(id=11, book_id=7, name="Kapitel", slug="kapitel")
    token = bookstack_module._chapter_token(chapter, instance.id, revision, "book:7")
    client = AsyncMock()
    client.update_chapter.return_value = BookStackChapter(
        id=11, book_id=7, name="Kapitel neu", slug="kapitel"
    )
    _bind_location_checks(client, chapter_id=11)

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    result = await knowledge_update_chapter(
        KnowledgeUpdateChapterArguments(
            integration_id=instance.id,
            book_key="book:7",
            chapter_token=token,
            name="Kapitel neu",
        ),
        _context(instance, revision),
    )

    client.update_chapter.assert_awaited_once_with(
        11, name="Kapitel neu", description=None
    )
    assert json.loads(result.content)["chapter"]["name"] == "Kapitel neu"


async def test_update_chapter_refuses_a_token_of_another_book(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    chapter = BookStackChapter(id=11, book_id=9, name="Kapitel", slug="kapitel")
    token = bookstack_module._chapter_token(chapter, instance.id, revision, "book:9")
    client = AsyncMock()

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    result = await knowledge_update_chapter(
        KnowledgeUpdateChapterArguments(
            integration_id=instance.id,
            book_key="book:7",
            chapter_token=token,
            name="Kapitel neu",
        ),
        _context(instance, revision),
    )

    assert result.is_error is True
    client.update_chapter.assert_not_awaited()


async def test_book_listings_expose_shelf_membership(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    shelf = BookStackShelf(id=10, name="Home-IT", slug="home-it")
    shelved = BookStackBook(id=8, name="Netzwerk", slug="netzwerk")
    orphan = BookStackBook(id=13, name="Geraete", slug="geraete")
    client = AsyncMock()
    client.list_books.return_value = ((shelved, orphan), False)
    client.list_shelves.return_value = ((shelf,), False)
    client.read_shelf.return_value = BookStackShelfContents(
        shelf=shelf, books=(shelved,)
    )
    client.read_book.return_value = BookStackBookContents(
        book=orphan, chapters=(), pages=()
    )

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)
    context = _context(instance, revision)

    listing = await knowledge_list_books(
        KnowledgeIntegrationArguments(integration_id=instance.id), context
    )
    books = json.loads(listing.content)["books"]
    assert [book["shelfKeys"] for book in books] == [["shelf:10"], []]

    contents = await knowledge_read_book(
        KnowledgeBookArguments(integration_id=instance.id, book_key="book:13"), context
    )
    assert json.loads(contents.content)["book"]["shelfKeys"] == []


async def test_book_listing_omits_membership_when_shelves_are_unreadable(
    monkeypatch,
) -> None:
    instance = _base()
    revision = _revision(instance)
    client = AsyncMock()
    client.list_books.return_value = ((BookStackBook(id=8, name="Netzwerk", slug="n"),), False)
    client.list_shelves.side_effect = BookStackError("bookstack.accessDenied")

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    listing = await knowledge_list_books(
        KnowledgeIntegrationArguments(integration_id=instance.id),
        _context(instance, revision),
    )

    assert listing.is_error is False
    assert "shelfKeys" not in json.loads(listing.content)["books"][0]


async def test_shelf_update_adds_and_removes_without_dropping_other_books(
    monkeypatch,
) -> None:
    instance = _base()
    revision = _revision(instance)
    shelf = BookStackShelf(id=10, name="Home-IT", slug="home-it")
    client = AsyncMock()
    client.read_shelf.return_value = BookStackShelfContents(
        shelf=shelf,
        books=(
            BookStackBook(id=8, name="Netzwerk", slug="n"),
            BookStackBook(id=9, name="Alt", slug="a"),
        ),
    )
    client.update_shelf.return_value = shelf

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    await knowledge_update_shelf(
        KnowledgeUpdateShelfArguments(
            integration_id=instance.id,
            shelf_key="shelf:10",
            add_book_keys=["book:13"],
            remove_book_keys=["book:9"],
        ),
        _context(instance, revision),
    )

    assert client.update_shelf.await_args.kwargs["book_ids"] == (8, 13)


def test_shelf_update_rejects_replace_combined_with_incremental_changes() -> None:
    with pytest.raises(ValidationError):
        KnowledgeUpdateShelfArguments(
            integration_id=uuid4(),
            shelf_key="shelf:10",
            book_keys=["book:8"],
            add_book_keys=["book:13"],
        )
    with pytest.raises(ValidationError):
        KnowledgeUpdateShelfArguments(
            integration_id=uuid4(),
            shelf_key="shelf:10",
            add_book_keys=["book:13"],
            remove_book_keys=["book:13"],
        )


def test_create_book_rejects_invalid_shelf_keys() -> None:
    with pytest.raises(ValidationError):
        KnowledgeCreateBookArguments(
            integration_id=uuid4(),
            name="Geraete",
            shelf_keys=["book:10"],
        )
    with pytest.raises(ValidationError):
        KnowledgeCreateBookArguments(
            integration_id=uuid4(),
            name="Geraete",
            shelf_keys=["shelf:10", "shelf:10"],
        )

def _attachment(**overrides) -> BookStackAttachment:
    values = {
        "id": 12,
        "name": "Datenblatt",
        "page_id": 21,
        "external": False,
        "extension": "pdf",
    }
    values.update(overrides)
    return BookStackAttachment(**values)


def _download_context(instance: IntegrationInstance, revision: str) -> SimpleNamespace:
    context = _context(instance, revision)
    context.web_fetch_policy = WebFetchPolicy.AUTO_PUBLIC
    context.tool_settings = ToolSettings()
    context.allowed_private_origins = ()
    context.ask = AsyncMock(return_value=PermissionDecision.ALLOW)
    context.root_files = Mock()
    # Uploading a local file asks for filesystem.read, which builds a permission
    # scope from the root identity.
    context.root_files.root_identity.return_value = "root-identity"
    return context


async def test_attachment_from_url_is_downloaded_and_uploaded(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    token = bookstack_module._page_token(_page(), instance.id, revision, "book:7")
    client = AsyncMock()
    _bind_location_checks(client)
    client.create_file_attachment.return_value = _attachment()

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    async def download_file(url, **_kwargs):
        assert url == "https://example.test/sheet.pdf"
        return DownloadedFile(
            final_url=url,
            content_type="application/pdf",
            filename="sheet.pdf",
            data=b"%PDF-1.4",
        )

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)
    monkeypatch.setattr(webfetch_module, "download_file", download_file)
    arguments = KnowledgeCreateAttachmentArguments(
        integration_id=instance.id,
        book_key="book:7",
        page_token=token,
        name="Datenblatt",
        url="https://example.test/sheet.pdf",
    )

    result = await knowledge_create_attachment(
        arguments, _download_context(instance, revision)
    )

    assert client.create_file_attachment.await_args.kwargs["filename"] == "sheet.pdf"
    assert client.create_file_attachment.await_args.kwargs["data"] == b"%PDF-1.4"
    assert client.create_file_attachment.await_args.kwargs["page_id"] == 21
    payload = json.loads(result.content)["attachment"]
    assert payload["kind"] == "file"
    assert payload["extension"] == "pdf"


async def test_failed_download_reports_the_underlying_reason(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    token = bookstack_module._page_token(_page(), instance.id, revision, "book:7")
    client = AsyncMock()

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    async def download_file(_url, **_kwargs):
        raise WebFetchError("webfetch.contentTypeUnsupported")

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)
    monkeypatch.setattr(webfetch_module, "download_file", download_file)
    arguments = KnowledgeCreateAttachmentArguments(
        integration_id=instance.id,
        book_key="book:7",
        page_token=token,
        name="Datenblatt",
        url="https://example.test/sheet.pdf",
    )

    result = await knowledge_create_attachment(
        arguments, _download_context(instance, revision)
    )

    assert result.is_error is True
    assert (
        json.loads(result.content)["error"]["code"]
        == "webfetch.contentTypeUnsupported"
    )
    client.create_file_attachment.assert_not_awaited()


async def test_download_is_refused_when_web_access_is_disabled(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    token = bookstack_module._page_token(_page(), instance.id, revision, "book:7")
    client = AsyncMock()

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)
    context = _download_context(instance, revision)
    context.web_fetch_policy = WebFetchPolicy.DISABLED
    arguments = KnowledgeCreateAttachmentArguments(
        integration_id=instance.id,
        book_key="book:7",
        page_token=token,
        name="Datenblatt",
        url="https://example.test/sheet.pdf",
    )

    result = await knowledge_create_attachment(arguments, context)

    assert result.is_error is True
    assert json.loads(result.content)["error"]["code"] == "tool.unavailable"
    client.create_file_attachment.assert_not_awaited()


async def test_attachment_from_local_file_uses_root_storage(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    token = bookstack_module._page_token(_page(), instance.id, revision, "book:7")
    client = AsyncMock()
    _bind_location_checks(client)
    client.create_file_attachment.return_value = _attachment(name="Notiz", extension="md")

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)
    context = _download_context(instance, revision)
    context.root_files.normalize_relative_path.return_value = "notes/notiz.md"
    context.root_files.read_bytes.return_value = b"# Notiz"
    arguments = KnowledgeCreateAttachmentArguments(
        integration_id=instance.id,
        book_key="book:7",
        page_token=token,
        name="Notiz",
        root="session",
        path="notes/notiz.md",
    )

    result = await knowledge_create_attachment(arguments, context)

    assert context.root_files.read_bytes.call_args.args[0] == "session"
    assert client.create_file_attachment.await_args.kwargs["filename"] == "notiz.md"
    assert (
        client.create_file_attachment.await_args.kwargs["content_type"]
        == "text/markdown"
    )
    assert json.loads(result.content)["attachment"]["name"] == "Notiz"


async def test_missing_local_file_reports_file_not_found(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    token = bookstack_module._page_token(_page(), instance.id, revision, "book:7")
    client = AsyncMock()
    _bind_location_checks(client)

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)
    context = _download_context(instance, revision)
    context.root_files.normalize_relative_path.return_value = "missing.pdf"
    context.root_files.read_bytes.side_effect = RootFileError(
        "file.fileNotFound", "missing"
    )
    arguments = KnowledgeCreateAttachmentArguments(
        integration_id=instance.id,
        book_key="book:7",
        page_token=token,
        name="Fehlt",
        root="workspace",
        path="missing.pdf",
    )

    result = await knowledge_create_attachment(arguments, context)

    assert result.is_error is True
    assert json.loads(result.content)["error"]["code"] == "file.fileNotFound"
    client.create_file_attachment.assert_not_awaited()


async def test_link_attachment_skips_any_download(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    token = bookstack_module._page_token(_page(), instance.id, revision, "book:7")
    client = AsyncMock()
    _bind_location_checks(client)
    client.create_link_attachment.return_value = _attachment(
        id=13, name="Handbuch", external=True, extension=""
    )

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)
    context = _download_context(instance, revision)
    context.web_fetch_policy = WebFetchPolicy.DISABLED
    arguments = KnowledgeCreateAttachmentArguments(
        integration_id=instance.id,
        book_key="book:7",
        page_token=token,
        name="Handbuch",
        link="https://example.test/manual",
    )

    result = await knowledge_create_attachment(arguments, context)

    assert client.create_link_attachment.await_args.kwargs["link"] == (
        "https://example.test/manual"
    )
    client.create_file_attachment.assert_not_awaited()
    assert json.loads(result.content)["attachment"]["kind"] == "link"


async def test_attachment_sources_are_mutually_exclusive() -> None:
    instance = _base()
    base = {
        "integration_id": instance.id,
        "book_key": "book:7",
        "page_token": "token",
        "name": "Datei",
    }
    for extra in (
        {},
        {"url": "https://a.test/x.pdf", "link": "https://a.test/x"},
        {"root": "session"},
        {"path": "x.pdf"},
        {"url": "https://a.test/x.pdf", "root": "session", "path": "x.pdf"},
    ):
        with pytest.raises(ValidationError):
            KnowledgeCreateAttachmentArguments(**base, **extra)


async def test_attachment_tokens_are_binding_scoped(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    token = bookstack_module._attachment_token(
        _attachment(), instance.id, revision, "book:7"
    )
    client = AsyncMock()
    _bind_location_checks(client)

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)
    arguments = KnowledgeDeleteAttachmentArguments(
        integration_id=instance.id, book_key="book:7", attachment_token=token
    )

    result = await knowledge_delete_attachment(arguments, _context(instance, revision))
    assert json.loads(result.content)["deleted"] is True
    assert client.delete_attachment.await_args.args[0] == 12

    foreign = SimpleNamespace(
        bound_options={
            "integration_id": str(instance.id),
            "binding_revision": revision,
        },
        integration_settings=IntegrationSettings(instances=[instance]),
    )
    other_book = KnowledgeDeleteAttachmentArguments(
        integration_id=instance.id, book_key="book:9", attachment_token=token
    )
    assert (await knowledge_delete_attachment(other_book, foreign)).is_error is True


async def test_attachment_listing_returns_reusable_tokens(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    page_token = bookstack_module._page_token(_page(), instance.id, revision, "book:7")
    client = AsyncMock()
    _bind_location_checks(client)
    client.list_attachments.return_value = (
        _attachment(),
        _attachment(id=13, name="Link", external=True, extension=""),
    )

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)
    arguments = KnowledgeListAttachmentsArguments(
        integration_id=instance.id, book_key="book:7", page_token=page_token
    )

    result = await knowledge_list_attachments(arguments, _context(instance, revision))

    assert client.list_attachments.await_args.args[0] == 21
    attachments = json.loads(result.content)["attachments"]
    assert [item["kind"] for item in attachments] == ["file", "link"]
    delete_arguments = KnowledgeDeleteAttachmentArguments(
        integration_id=instance.id,
        book_key="book:7",
        attachment_token=attachments[0]["attachmentToken"],
    )
    deleted = await knowledge_delete_attachment(
        delete_arguments, _context(instance, revision)
    )
    assert json.loads(deleted.content)["deleted"] is True


def _blocked_context(instance, revision, *, book_key: str = "book:9"):
    context = _context(instance, revision)
    context.tool_settings = ToolSettings(
        resource_policies={
            str(instance.id): {book_key: {"knowledge.read": ToolExecutionPolicy.DISABLED}}
        }
    )
    return context


async def test_search_hides_results_from_books_with_disabled_read_policy(
    monkeypatch,
) -> None:
    instance = _base()
    revision = _revision(instance)
    client = AsyncMock()
    client.search.return_value = (
        BookStackSearchResult(
            type="page", id=1, name="Sichtbar", book_id=7, chapter_id=None,
            preview="Erlaubt",
        ),
        BookStackSearchResult(
            type="page", id=2, name="Geheim", book_id=9, chapter_id=None,
            preview="Vertraulich",
        ),
        BookStackSearchResult(
            type="book", id=9, name="Gesperrtes Buch", book_id=None, chapter_id=None,
            preview="Vertraulich",
        ),
    )

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    result = await knowledge_search(
        KnowledgeSearchArguments(integration_id=instance.id, query="Netz"),
        _blocked_context(instance, revision),
    )

    payload = json.loads(result.content)["results"]
    assert [item["name"] for item in payload] == ["Sichtbar"]
    assert "Vertraulich" not in result.content


async def test_book_listing_hides_books_with_disabled_read_policy(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    client = AsyncMock()
    client.list_books.return_value = (
        (
            BookStackBook(id=7, name="Handbuch", slug="handbuch"),
            BookStackBook(id=9, name="Gesperrt", slug="gesperrt"),
        ),
        False,
    )
    client.list_shelves.return_value = ((), False)

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    result = await knowledge_list_books(
        KnowledgeIntegrationArguments(integration_id=instance.id),
        _blocked_context(instance, revision),
    )

    assert [book["name"] for book in json.loads(result.content)["books"]] == ["Handbuch"]


async def test_shelf_update_refuses_books_with_disabled_read_policy(
    monkeypatch,
) -> None:
    instance = _base()
    revision = _revision(instance)
    client = AsyncMock()

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    result = await knowledge_update_shelf(
        KnowledgeUpdateShelfArguments(
            integration_id=instance.id,
            shelf_key="shelf:4",
            remove_book_keys=["book:9"],
        ),
        _blocked_context(instance, revision),
    )

    assert result.is_error is True
    assert json.loads(result.content)["error"]["code"] == "permission.denied"
    client.update_shelf.assert_not_awaited()


async def test_shelf_replacement_keeps_books_the_agent_cannot_see(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    client = AsyncMock()
    client.read_shelf.return_value = BookStackShelfContents(
        shelf=BookStackShelf(id=4, name="Regal", slug="regal"),
        books=(
            BookStackBook(id=9, name="Gesperrt", slug="gesperrt"),
            BookStackBook(id=7, name="Handbuch", slug="handbuch"),
        ),
    )
    client.update_shelf.return_value = BookStackShelf(id=4, name="Regal", slug="regal")

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    await knowledge_update_shelf(
        KnowledgeUpdateShelfArguments(
            integration_id=instance.id, shelf_key="shelf:4", book_keys=["book:7"]
        ),
        _blocked_context(instance, revision),
    )

    assert set(client.update_shelf.await_args.kwargs["book_ids"]) == {7, 9}


async def test_page_update_rejects_markdown_based_on_an_outdated_page(
    monkeypatch,
) -> None:
    instance = _base()
    revision = _revision(instance)
    token = bookstack_module._page_token(_page(), instance.id, revision, "book:7")

    client = AsyncMock()
    client.read_page_location.return_value = _page(
        updated_at="2026-02-02T20:00:00.000000Z"
    )

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    result = await knowledge_update_page(
        KnowledgeUpdatePageArguments(
            integration_id=instance.id,
            book_key="book:7",
            page_token=token,
            markdown="# Ueberschreibt fremde Aenderung\n",
        ),
        _context(instance, revision),
    )

    assert result.is_error is True
    payload = json.loads(result.content)["error"]
    assert payload["code"] == "integration.staleReference"
    assert payload["field"] == "page_token"
    client.update_page.assert_not_awaited()


async def test_page_rename_is_allowed_on_an_outdated_page(monkeypatch) -> None:
    instance = _base()
    revision = _revision(instance)
    token = bookstack_module._page_token(_page(), instance.id, revision, "book:7")

    client = AsyncMock()
    client.read_page_location.return_value = _page(
        updated_at="2026-02-02T20:00:00.000000Z"
    )
    client.update_page.return_value = _page(name="Neuer Titel")
    client.read_page.return_value = _page(name="Neuer Titel", markdown="# Inhalt\n")

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    result = await knowledge_update_page(
        KnowledgeUpdatePageArguments(
            integration_id=instance.id,
            book_key="book:7",
            page_token=token,
            name="Neuer Titel",
        ),
        _context(instance, revision),
    )

    assert result.is_error is False
    client.update_page.assert_awaited_once()


async def test_page_update_rejects_markdown_for_tokens_without_a_revision(
    monkeypatch,
) -> None:
    instance = _base()
    revision = _revision(instance)
    token = bookstack_module._page_token(
        _page(updated_at=None), instance.id, revision, "book:7"
    )

    client = AsyncMock()
    client.read_page_location.return_value = _page()

    @asynccontextmanager
    async def selected_client(_integration_id, _context):
        yield client, revision

    monkeypatch.setattr(bookstack_module, "_selected_client", selected_client)

    result = await knowledge_update_page(
        KnowledgeUpdatePageArguments(
            integration_id=instance.id,
            book_key="book:7",
            page_token=token,
            markdown="# Blind ueberschrieben\n",
        ),
        _context(instance, revision),
    )

    assert result.is_error is True
    assert json.loads(result.content)["error"]["code"] == "integration.staleReference"
    client.update_page.assert_not_awaited()


def test_write_tools_do_not_ask_the_model_for_an_idempotency_key() -> None:
    """BookStack offers no idempotency API, so the field would only mislead.

    A required key that nothing reads costs tokens on every call and suggests
    protection against duplicate creation that does not exist.
    """
    instance = _base()
    settings = IntegrationSettings(instances=[instance])
    tools = {
        tool.name: tool
        for tool in effective_integration_contributions(settings).tools
    }
    write_tools = [
        "knowledge_create_page",
        "knowledge_create_book",
        "knowledge_create_chapter",
        "knowledge_create_shelf",
        "knowledge_create_attachment",
    ]
    for name in write_tools:
        schema = tools[name].arguments_model.model_json_schema()
        assert "idempotency_key" not in schema["properties"], name
