FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca

RUN apt-get update \
    && apt-get install --no-install-recommends -y git openscad ripgrep xauth xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data /workspace
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY --chmod=755 docker/entrypoint /usr/local/bin/mato-loop-execution-agent-entrypoint

ENV PATH=/app/.venv/bin:$PATH \
    MATO_EXECUTION_AGENT_CONFIG=/config/config.json

ENTRYPOINT ["mato-loop-execution-agent-entrypoint", "mato-loop-execution-agent"]
CMD ["run"]
