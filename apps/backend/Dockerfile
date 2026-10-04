FROM node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS frontend

RUN corepack enable
WORKDIR /build/frontend
COPY apps/frontend/package.json apps/frontend/yarn.lock apps/frontend/.yarnrc.yml ./
RUN yarn install --immutable
COPY apps/frontend/ ./
RUN yarn build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS documentation

ENV UV_LINK_MODE=copy
WORKDIR /build
COPY apps/backend/pyproject.toml apps/backend/uv.lock apps/backend/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --only-group docs --no-install-project
COPY mkdocs.yml ./
COPY docs/user ./docs/user
RUN .venv/bin/mkdocs build --strict --site-dir /build/site \
    && rm -f /build/site/404.html

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ffmpeg poppler-utils ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_LINK_MODE=copy
COPY apps/backend/pyproject.toml apps/backend/uv.lock apps/backend/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY apps/backend/src ./src
COPY apps/backend/alembic ./alembic
COPY apps/backend/alembic.ini ./alembic.ini
COPY scripts/install-matoloop-agent.sh ./scripts/install-matoloop-agent.sh
COPY scripts/install-matoloop-agent.ps1 ./scripts/install-matoloop-agent.ps1
COPY LICENSE /usr/share/licenses/mato-loop/LICENSE
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
ARG BUILD_REVISION=unknown
RUN printf '%s\n' "$BUILD_REVISION" > /app/BUILD_REVISION
COPY --from=frontend /build/frontend/dist/pwa /app/static
COPY --from=documentation /build/site /app/static/docs

ENV MATO_LOOP_DATA_DIR=/data \
    MATO_LOOP_STATIC_DIR=/app/static \
    UV_PROJECT_ENVIRONMENT=/app/.venv

EXPOSE 6020

CMD ["sh", "-c", "./.venv/bin/alembic upgrade head && exec ./.venv/bin/uvicorn mato_loop_backend.app:app --host 0.0.0.0 --port 6020"]
