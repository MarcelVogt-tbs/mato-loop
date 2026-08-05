FROM node:22-bookworm-slim AS frontend

RUN corepack enable
WORKDIR /build/frontend
COPY apps/frontend/package.json apps/frontend/yarn.lock apps/frontend/.yarnrc.yml ./
RUN yarn install --immutable
COPY apps/frontend/ ./
RUN yarn build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS documentation

WORKDIR /build
COPY apps/backend/pyproject.toml apps/backend/uv.lock apps/backend/README.md ./
RUN uv sync --frozen --only-group docs --no-install-project
COPY mkdocs.yml ./
COPY docs/user ./docs/user
RUN .venv/bin/mkdocs build --strict --site-dir /build/site \
    && rm -f /build/site/404.html

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ffmpeg poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY apps/backend/pyproject.toml apps/backend/uv.lock apps/backend/README.md ./
COPY apps/backend/src ./src
COPY apps/backend/alembic ./alembic
COPY apps/backend/alembic.ini ./alembic.ini
RUN uv sync --frozen --no-dev
COPY --from=frontend /build/frontend/dist/spa /app/static
COPY --from=documentation /build/site /app/static/docs

ENV MATO_LOOP_DATA_DIR=/data \
    MATO_LOOP_STATIC_DIR=/app/static \
    UV_PROJECT_ENVIRONMENT=/app/.venv

EXPOSE 6020

CMD ["sh", "-c", "./.venv/bin/alembic upgrade head && exec ./.venv/bin/uvicorn mato_loop_backend.app:app --host 0.0.0.0 --port 6020"]
