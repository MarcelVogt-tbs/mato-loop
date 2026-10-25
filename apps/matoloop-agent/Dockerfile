FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca AS version-build
ARG MATOLOOP_VERSION
RUN --mount=type=bind,source=.,target=/source \
    if [ -n "$MATOLOOP_VERSION" ]; then printf '%s\n' "$MATOLOOP_VERSION" > /build-version.txt; \
    else apt-get update && apt-get install -y --no-install-recommends git \
      && python /source/libs/python/version/src/matoloop_version/__init__.py --source /source --output /build-version.txt; fi

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca
RUN apt-get update \
    && apt-get install --no-install-recommends -y git openscad ripgrep xauth xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data /workspace
ENV UV_LINK_MODE=copy \
    MATOLOOP_AGENT_CONTAINER=1 \
    MATOLOOP_AGENT_INSTALL_ROOT=/opt/matoloop-agent \
    MATOLOOP_AGENT_CONFIG=/data/config.json \
    PLAYWRIGHT_BROWSERS_PATH=/usr/local/lib/matoloop-agent/tools/browser \
    UV_PROJECT_ENVIRONMENT=/opt/matoloop-agent/releases/image/venv
WORKDIR /opt/matoloop-agent/releases/image/source/apps/matoloop-agent
COPY libs/python/version ../../libs/python/version
COPY --from=version-build /build-version.txt ../../libs/python/version/src/matoloop_version/build-version.txt
COPY apps/matoloop-agent/pyproject.toml apps/matoloop-agent/uv.lock apps/matoloop-agent/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --group browser --no-install-project --no-editable --refresh-package matoloop-version
COPY apps/matoloop-agent/src ./src
COPY scripts/matoloop-agent-installer.py ../../scripts/matoloop-agent-installer.py
# Share headless browser dependencies with development and managed updates.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --group browser --no-editable \
    && sh src/matoloop_agent/container-browser-setup.sh "$UV_PROJECT_ENVIRONMENT/bin/python"
ARG AGENT_SOURCE_URL=https://github.com/devmato/mato-loop.git
RUN python ../../scripts/matoloop-agent-installer.py container-init "$AGENT_SOURCE_URL"
COPY apps/matoloop-agent/config.json /usr/share/matoloop-agent/config.json
COPY --chmod=755 apps/matoloop-agent/docker/entrypoint /usr/local/bin/matoloop-agent-entrypoint
COPY --chmod=755 apps/matoloop-agent/docker/command /usr/local/bin/matoloop-agent
COPY LICENSE /usr/share/licenses/matoloop-agent/LICENSE
WORKDIR /opt/matoloop-agent
ENTRYPOINT ["matoloop-agent-entrypoint"]
CMD ["python", "/opt/matoloop-agent/installer.py", "container-run"]
