# MatoLoop Backend

FastAPI-Backend mit asynchronem SQLAlchemy, PostgreSQL und Alembic.

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn mato_loop_backend.app:app --reload --port 6020
```
