FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58

WORKDIR /app/apps/matoloop-teams-relay
ENV UV_LINK_MODE=copy PYTHONPYCACHEPREFIX=/tmp/python-cache
COPY libs/python/teams-relay /app/libs/python/teams-relay
COPY apps/matoloop-teams-relay ./
RUN uv sync --frozen --no-dev && useradd --uid 10001 --create-home relay \
    && mkdir /spool && chown relay:relay /spool
USER 10001:10001
EXPOSE 3978
CMD [".venv/bin/uvicorn", "matoloop_teams_relay.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "3978", "--no-access-log"]
