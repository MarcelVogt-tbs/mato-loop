from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from mato_loop_backend import changelog
from mato_loop_backend.api.errors import register_error_handlers
from mato_loop_backend.api.routes.changelog import router
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.security import require_user

BASE = "a" * 40
TARGET = "b" * 40


@pytest.fixture
def github(monkeypatch):
    requests = []
    responses = []
    client = httpx.AsyncClient

    def handle(request):
        requests.append(request)
        assert request.url.host == "api.github.com"
        assert request.url.path.startswith("/repos/devmato/mato-loop/")
        response = responses.pop(0)
        return response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)

    monkeypatch.setattr(changelog, "_targets", changelog._Cache())
    monkeypatch.setattr(changelog, "_comparisons", changelog._Cache())
    monkeypatch.setattr(changelog.httpx, "AsyncClient", lambda **kwargs: client(
        **({"transport": httpx.MockTransport(handle)} | kwargs),
    ))
    monkeypatch.setattr(changelog, "build_revision", lambda: BASE)
    monkeypatch.setattr(changelog, "__version__", "2026-11-17-aaaaaaaa")
    return requests, responses


def commit(number, message, parents=1):
    return {"sha": f"{number:040x}", "commit": {"message": message},
            "parents": [{"sha": BASE}] * parents}


def comparison(status="ahead", commits=None, total=None):
    commits = commits or []
    return {"status": status, "total_commits": len(commits) if total is None else total,
            "commits": commits}


async def test_exact_comparison_collects_only_final_nonmerge_trailers_and_paginates(github):
    requests, responses = github
    responses.extend([
        comparison(commits=[
            commit(1, "Subject\n\nRelease-Note: Added: Viewer\nRelease-Note: Security: Safer URLs"),
            commit(2, "Merge\n\nRelease-Note: Fixed: Duplicate", parents=2),
            commit(3, "Subject\n\nRelease-Note: Fixed: Body example\n\nNot a trailer"),
            commit(4, "Subject\n\nRelease-Note: Other: Ignore"),
        ], total=5),
        comparison(commits=[commit(5, "Subject\n\nRelease-Note: Changed: New view")], total=5),
    ])
    query = changelog.ChangelogQuery(base_revision=BASE, target_revision=TARGET)
    result = await changelog.get_changelog(query)
    assert result.model_dump() == {
        "base_revision": BASE, "base_version": None,
        "target_revision": TARGET, "target_version": None,
        "state": "behind", "status": "ok", "incomplete": False,
        "compare_url": f"https://github.com/devmato/mato-loop/compare/{BASE}...{TARGET}",
        "notes": [{"category": "Added", "text": "Viewer"},
                  {"category": "Security", "text": "Safer URLs"},
                  {"category": "Changed", "text": "New view"}],
    }
    assert [r.url.params["page"] for r in requests] == ["1", "2"]
    assert all(r.url.params["per_page"] == "100" for r in requests)
    await changelog.get_changelog(query)
    assert len(requests) == 2


@pytest.mark.parametrize("remote,state", [("behind", "ahead"), ("diverged", "diverged")])
async def test_state_is_base_relative_to_target(github, remote, state):
    _, responses = github
    responses.append(comparison(status=remote))
    result = await changelog.get_changelog(changelog.ChangelogQuery(
        base_revision=BASE, target_revision=TARGET,
    ))
    assert result.state == state


async def test_pagination_cap_is_explicit(github, monkeypatch):
    _, responses = github
    monkeypatch.setattr(changelog, "MAX_PAGES", 1)
    responses.append(comparison(commits=[commit(1, "Subject")], total=2))
    result = await changelog.get_changelog(changelog.ChangelogQuery(
        base_revision=BASE, target_revision=TARGET,
    ))
    assert result.incomplete is True


async def test_stable_resolves_release_tag_not_moving_commitish(github):
    requests, responses = github
    responses.extend([
        {"tag_name": "2026-11-17-bbbbbbbb", "target_commitish": "main", "draft": False, "prerelease": False},
        {"sha": TARGET}, comparison(),
    ])
    result = await changelog.get_changelog(changelog.ChangelogQuery(channel="stable"))
    assert result.target_revision == TARGET
    assert result.target_version == "2026-11-17-bbbbbbbb"
    assert requests[1].url.path.endswith("/commits/refs/tags/2026-11-17-bbbbbbbb")


async def test_stable_without_release_is_not_a_connection_error(github):
    _, responses = github
    responses.append(httpx.Response(404))
    result = await changelog.get_changelog(changelog.ChangelogQuery(channel="stable"))
    assert result.status == "no_release"


@pytest.mark.parametrize("revision,version", [
    (BASE, "2026-11-17-aaaaaaaa-dirty"), (None, "unknown"), (BASE, "2026-11-17-cccccccc"),
])
async def test_unknown_build_never_reports_current(github, monkeypatch, revision, version):
    requests, responses = github
    monkeypatch.setattr(changelog, "build_revision", lambda: revision)
    monkeypatch.setattr(changelog, "__version__", version)
    responses.append({"sha": BASE})
    result = await changelog.get_changelog(changelog.ChangelogQuery(channel="develop"))
    assert (result.state, result.status) == ("unknown", "unknown_build")
    assert len(requests) == 1


async def test_clean_identical_develop_build(github):
    requests, responses = github
    responses.append({"sha": BASE})
    result = await changelog.get_changelog(changelog.ChangelogQuery())
    assert (result.state, result.status) == ("identical", "ok")
    assert requests[0].url.path.endswith("/commits/heads/develop")


@pytest.fixture
def stored_history(monkeypatch):
    payload = {}

    async def save(_db, document):
        payload.update(document.model_dump(mode="json"))

    monkeypatch.setattr(changelog.SettingsStore, "save", AsyncMock(side_effect=save))
    monkeypatch.setattr(changelog.SettingsStore, "load", AsyncMock(
        side_effect=lambda _db, model, **_kwargs: model.model_validate(payload),
    ))


async def test_last_installation_compares_exact_builds_across_restarts(github, stored_history, monkeypatch):
    requests, responses = github
    db = AsyncMock()
    await changelog.record_installed_build(db)
    assert (await changelog.get_installed_changelog(db)).status == "no_previous_build"

    monkeypatch.setattr(changelog, "build_revision", lambda: TARGET)
    monkeypatch.setattr(changelog, "__version__", "2026-11-18-bbbbbbbb")
    await changelog.record_installed_build(db)
    await changelog.record_installed_build(db)  # A restart of the same build is not another update.
    newer = "c" * 40
    responses.extend([
        comparison(commits=[commit(1, "Subject\n\nRelease-Note: Fixed: Now installed")]),
        {"sha": newer},
        comparison(commits=[commit(2, "Subject\n\nRelease-Note: Added: Not installed")]),
    ])
    installed = await changelog.get_installed_changelog(db)
    available = await changelog.get_changelog(changelog.ChangelogQuery())
    assert (installed.base_version, installed.target_version) == ("2026-11-17-aaaaaaaa", "2026-11-18-bbbbbbbb")
    assert ([n.text for n in installed.notes], [n.text for n in available.notes]) == (["Now installed"], ["Not installed"])
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests] == [
        f"{BASE}...{TARGET}", "develop", f"{TARGET}...{newer}",
    ]


@pytest.mark.parametrize("response", [
    httpx.Response(403, text="private external error"),
    httpx.Response(200, json={"sha": "not-a-revision", "message": "private external error"}),
    httpx.Response(200, text="not json"),
])
async def test_external_errors_are_safe(github, response):
    _, responses = github
    responses.append(response)
    result = await changelog.get_changelog(changelog.ChangelogQuery(channel="develop"))
    assert (result.state, result.status, result.incomplete) == ("unknown", "unavailable", True)
    assert "private external error" not in result.model_dump_json()


async def test_route_authentication_and_revision_validation(github, stored_history):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    register_error_handlers(app)
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/changelog")).status_code == 401
        assert (await client.get("/api/changelog/installed")).status_code == 401
        app.dependency_overrides[require_user] = lambda: SimpleNamespace(is_admin=False)
        for params in [
            {"base_revision": BASE},
            {"base_revision": "https://example.org/", "target_revision": TARGET},
            {"base_revision": BASE, "target_revision": TARGET, "channel": "develop"},
            {"channel": "arbitrary"},
        ]:
            assert (await client.get("/api/changelog", params=params)).status_code == 422
        response = await client.get("/api/changelog", params={"base_revision": BASE, "target_revision": BASE})
        assert response.json()["state"] == "identical"
        assert response.headers["Cache-Control"] == "no-store"
        response = await client.get("/api/changelog/installed")
        assert response.json()["status"] == "no_previous_build"
        assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", ["/api/changelog", "/api/changelog/installed"])
async def test_changelog_releases_read_transaction_before_github(github, stored_history, monkeypatch, path):
    db = AsyncMock()
    await changelog.record_installed_build(db)
    monkeypatch.setattr(changelog, "build_revision", lambda: TARGET)
    monkeypatch.setattr(changelog, "__version__", "2026-11-18-bbbbbbbb")
    await changelog.record_installed_build(db)

    async def github_request(_client, endpoint, **_params):
        db.rollback.assert_awaited_once()
        return {"sha": TARGET} if endpoint == "commits/heads/develop" else comparison()

    monkeypatch.setattr(changelog, "_json", github_request)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[require_user] = lambda: SimpleNamespace(is_admin=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(path)).json()["status"] == "ok"
