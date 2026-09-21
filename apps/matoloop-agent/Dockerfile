FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca

RUN apt-get update \
    && apt-get install --no-install-recommends -y git openscad ripgrep xauth xvfb x11-xserver-utils x11-utils openbox x11vnc xclip \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data /workspace
WORKDIR /app
ENV UV_LINK_MODE=copy
COPY apps/matoloop-agent/pyproject.toml apps/matoloop-agent/uv.lock apps/matoloop-agent/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --group browser --no-install-project
ENV PLAYWRIGHT_BROWSERS_PATH=/usr/local/lib/matoloop-agent/tools/browser \
    MATOLOOP_AGENT_CONTAINER=1
RUN .venv/bin/playwright install --with-deps --no-shell chromium \
    && chmod -R a+rX /usr/local/lib/matoloop-agent/tools/browser \
    && useradd --system --uid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin matoloop-browser \
    && rm -rf /var/lib/apt/lists/*
COPY apps/matoloop-agent/src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --group browser
ARG BUILD_REVISION=unknown
RUN printf '%s\n' "$BUILD_REVISION" > /app/BUILD_REVISION
COPY apps/matoloop-agent/config.json /usr/share/matoloop-agent/config.json
COPY --chmod=755 apps/matoloop-agent/docker/entrypoint /usr/local/bin/matoloop-agent-entrypoint
COPY LICENSE /usr/share/licenses/matoloop-agent/LICENSE

ENV PATH=/app/.venv/bin:$PATH \
    MATOLOOP_AGENT_CONFIG=/data/config.json

ENTRYPOINT ["matoloop-agent-entrypoint", "matoloop-agent"]
CMD ["run"]
