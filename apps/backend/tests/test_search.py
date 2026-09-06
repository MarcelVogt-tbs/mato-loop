from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.memory.episode_retrieval import EpisodeReference, EpisodeRetrieval
from mato_loop_backend.memory.retrieval import MemoryRetrievalStrategy
from mato_loop_backend.models import ChatSession, Project
from mato_loop_backend.search import GlobalSearchService


async def test_global_search_combines_owned_projects_titles_and_message_hits(
    monkeypatch,
) -> None:
    owner_id = uuid4()
    now = datetime.now(UTC)
    project = Project(id=uuid4(), user_id=owner_id, name="MatoLoop", updated_at=now)
    title_session = ChatSession(
        id=uuid4(),
        user_id=owner_id,
        project_id=project.id,
        title="Search planning",
        updated_at=now,
    )
    message_project = Project(
        id=uuid4(), user_id=owner_id, name="Other", updated_at=now
    )
    message_session_id = uuid4()
    message_id = uuid4()
    project_result = MagicMock()
    project_result.all.return_value = [(project, 2)]
    title_result = MagicMock()
    title_result.all.return_value = [(title_session, project.name)]
    project_name_result = MagicMock()
    project_name_result.all.return_value = [(message_project.id, message_project.name)]
    db = AsyncMock(spec=AsyncSession)
    db.execute.side_effect = [project_result, title_result, project_name_result]
    retrieve = AsyncMock(
        return_value=EpisodeRetrieval(
            references=[
                EpisodeReference(
                    session_id=message_session_id,
                    session_title="Earlier decision",
                    project_id=message_project.id,
                    anchor_message_id=message_id,
                    anchor_created_at=now,
                    anchor_role="assistant",
                    anchor_content="  We chose PostgreSQL\nfor the primary database.  ",
                    excerpt=(),
                )
            ],
            strategy=MemoryRetrievalStrategy.HYBRID,
        )
    )
    monkeypatch.setattr(
        "mato_loop_backend.search.EpisodeRetrievalService.retrieve", retrieve
    )

    result = await GlobalSearchService.search(
        db, owner_id=owner_id, query="database", limit=10
    )

    assert result.projects[0].session_count == 2
    assert [item.id for item in result.sessions] == [
        title_session.id,
        message_session_id,
    ]
    assert result.sessions[1].project_name == "Other"
    assert result.sessions[1].message_id == message_id
    assert result.sessions[1].message_excerpt == (
        "We chose PostgreSQL for the primary database."
    )
    assert result.strategy == MemoryRetrievalStrategy.HYBRID
    retrieve.assert_awaited_once_with(
        db,
        owner_id=owner_id,
        current_session_id=None,
        readable_by="private",
        query="database",
        limit=10,
        max_semantic_distance=0.25,
        anchor_only=True,
    )
    for call in db.execute.await_args_list:
        assert owner_id in call.args[0].compile().params.values()


async def test_global_search_deduplicates_title_matches_and_reports_lexical_fallback(
    monkeypatch,
) -> None:
    owner_id = uuid4()
    now = datetime.now(UTC)
    session = ChatSession(
        id=uuid4(), user_id=owner_id, title="PostgreSQL", updated_at=now
    )
    empty_projects = MagicMock()
    empty_projects.all.return_value = []
    title_result = MagicMock()
    title_result.all.return_value = [(session, None)]
    db = AsyncMock(spec=AsyncSession)
    db.execute.side_effect = [empty_projects, title_result]
    monkeypatch.setattr(
        "mato_loop_backend.search.EpisodeRetrievalService.retrieve",
        AsyncMock(
            return_value=EpisodeRetrieval(
                references=[
                    EpisodeReference(
                        session_id=session.id,
                        session_title=session.title,
                        anchor_message_id=uuid4(),
                        anchor_created_at=now,
                        anchor_role="user",
                        anchor_content="PostgreSQL",
                        excerpt=(),
                    )
                ],
                strategy=MemoryRetrievalStrategy.LEXICAL,
            )
        ),
    )

    result = await GlobalSearchService.search(
        db, owner_id=owner_id, query="PostgreSQL", limit=10
    )

    assert len(result.sessions) == 1
    assert result.sessions[0].match_type == "title"
    assert result.strategy == MemoryRetrievalStrategy.LEXICAL
