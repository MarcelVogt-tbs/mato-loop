FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca

ARG AGENT_UID=1000
ARG AGENT_GID=1000
RUN apt-get update \
    && apt-get install --no-install-recommends -y git ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid ${AGENT_GID} agent \
    && useradd --create-home --uid ${AGENT_UID} --gid ${AGENT_GID} agent \
    && mkdir -p /data /workspace \
    && chown agent:agent /data /workspace
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH=/app/.venv/bin:$PATH \
    MATO_EXECUTION_AGENT_CONFIG=/config/config.json

USER ${AGENT_UID}:${AGENT_GID}
ENTRYPOINT ["mato-loop-execution-agent"]
CMD ["run"]
