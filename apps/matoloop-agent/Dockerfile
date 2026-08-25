FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca

RUN apt-get update \
    && apt-get install --no-install-recommends -y git openscad ripgrep xauth xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data /workspace
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY config.json /usr/share/matoloop-agent/config.json
COPY --chmod=755 docker/entrypoint /usr/local/bin/matoloop-agent-entrypoint

ENV PATH=/app/.venv/bin:$PATH \
    MATOLOOP_AGENT_CONFIG=/data/config.json

ENTRYPOINT ["matoloop-agent-entrypoint", "matoloop-agent"]
CMD ["run"]
