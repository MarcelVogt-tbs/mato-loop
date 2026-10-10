<p align="center">
  <img src="apps/frontend/public/icons/matoloop.svg" alt="MatoLoop" width="96" height="96">
</p>

<h1 align="center">MatoLoop</h1>

<p align="center">Self-hosted AI chat and agents, with your choice of models and explicit control over tools.</p>

<p align="center">
  <a href="https://github.com/devmato/mato-loop/actions/workflows/ci.yaml"><img alt="CI" src="https://github.com/devmato/mato-loop/actions/workflows/ci.yaml/badge.svg?branch=develop"></a>
  <a href="LICENSE"><img alt="License: AGPL-3.0-only" src="https://img.shields.io/badge/license-AGPL--3.0--only-blue.svg"></a>
</p>

MatoLoop is a provider-neutral AI chat and agent harness. It owns sessions, messages, files, tool execution, permissions, and memory; model providers transport requests and responses. The application and user handbook are in German.

## Disclaimer

MatoLoop is developed with significant AI assistance. Large parts of the codebase have not been reviewed by a human.

Use this software at your own risk. AI-generated responses may be incorrect, and agent actions can affect files, systems, and connected services. There is no warranty or guarantee of correctness, reliability, or security.

Verify important results and configure tool permissions carefully. Grant agents only the access you intend them to have.

## Features

- **Persistent conversations:** projects, sessions, attachments, revisioned files, queued messages, task progress, and delegated agent runs.
- **Choice of providers:** ChatGPT Codex with device-code OAuth, OpenAI-compatible Chat Completions or Responses, Lemonade, and media providers such as Azure Speech.
- **Controlled tool use:** private/public model policies, durable approvals, revocable session grants, and protected secret input.
- **Connected computers:** outbound-connected Linux and Windows Agents for files, shell commands, and supported local operations, with managed installation and updates.
- **Research and integrations:** built-in no-key web search, SearXNG, Webfetch, browser automation, mail, calendars, product adapters, and generic MCP servers.
- **Personalization:** agent profiles, personas, skills, session templates, optional durable memory, and Dreaming.
- **Automation and notifications:** scheduled work, Telegram channels, persistent notices, and optional Web Push.
- **Multiple users:** local accounts, administrator controls, owner-isolated personal data, and deliberately shared instance resources.

## Install with Docker Compose

### Requirements

- Docker Engine and Docker Compose v2.
- A `linux/amd64` host for the published images.
- An HTTPS origin and reverse proxy for production login; the production template enables Secure cookies but does not provide TLS itself.

Clone the repository and use the deployment files from the version you intend to install:

```bash
git clone https://github.com/devmato/mato-loop.git
cd mato-loop
# Check out the published release tag you intend to deploy.
git checkout <release-tag>
cp .env.example .env
```

Edit `.env` before starting:

1. Set a random, URL-safe `POSTGRES_PASSWORD`.
2. Replace both image placeholders with the same immutable tag from a successful [GitHub release](https://github.com/devmato/mato-loop/releases) or publication run: `YYYY-MM-DD-<SHA8>`.
3. Set `MATO_LOOP_PUBLIC_BASE_URL` to your public HTTP(S) origin without a path.
4. Review host data paths and the HTTP listener. Set `MATO_LOOP_HOST_ADDRESS=127.0.0.1` when your reverse proxy runs on the same host; use the appropriate private binding for other proxy setups.
5. Configure HTTPS and keep `MATO_LOOP_SECURE_COOKIES=true` for production.

Then start the stack:

```bash
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=100 mato-loop
```

The stack includes PostgreSQL with pgvector, Infinity for local retrieval embeddings, MatoLoop, and a MatoLoop Agent. Infinity's first model download can take several minutes; lexical retrieval remains available while embeddings are degraded. The default HTTP port is `10020`.

For a deployment without the bundled Agent, use `docker compose -f docker-compose.no-agent.yaml` for all deployment commands. Add `--remove-orphans` to `up -d` when switching an existing stack.

See [operations, updates, and backup/restore](docs/user/betrieb-und-datensicherung.md) for the complete procedure, including cleanup required when upgrading installations that used the removed Git integration. Computer Agents execute with their operating-system user's rights; the bundled container currently runs as root.

### First use

1. Open your configured HTTPS origin and create the first local account; it becomes the administrator.
2. Add a provider under **Administration > Provider**, synchronize its models, and enable the models you want to use.
3. Configure **Administration > Standardmodelle**, then start a chat.
4. Optionally add and pair a computer under **Einstellungen > Computer**, then choose it and a working directory in the session.

Chat and backend-managed files work without a computer. Personal providers and integrations depend on the administrator's instance settings. Start with the [user quickstart](docs/user/schnellstart.md).

## Documentation

The [documentation index](docs/index.md) links the current contracts, operations guides, and historical evaluation reports.

- [User handbook (German)](docs/user/index.md) — also served at `/docs/` by MatoLoop.
- [Operations and backup/restore](docs/user/betrieb-und-datensicherung.md).
- [Architecture](docs/architecture.md) and [agent harness](docs/harness.md).
- [Integrations](docs/integrations.md), [browser](docs/browser.md), and [computer maintenance](docs/computer-maintenance.md).
- [Backend](apps/backend/README.md), [frontend](apps/frontend/README.md), and [standalone Agent](apps/matoloop-agent/README.md).
- [CI and releases](docs/ci-and-releases.md).

## Development

Use the Docker development stack from the repository root:

```bash
./scripts/dev.sh up --build -d
```

| Endpoint | Default URL |
| --- | --- |
| Web UI | http://localhost:10020 |
| User handbook | http://localhost:10020/docs/ |
| Backend API | http://localhost:6020/api |
| OpenAPI | http://localhost:6020/api/docs |

The stack uses FastAPI, Pydantic v2, async SQLAlchemy, PostgreSQL/pgvector, Infinity, Vue 3, Quasar, Pinia, and Vue Router. Dependencies are managed with `uv` and Yarn 4.

`.env.dev` is ready to use. Put local settings in the gitignored `.env.dev.override`; start from `.env.dev.override.example`. **Parallel checkouts need distinct Compose project names, ports, and Agent bind paths**; see [development and documentation maintenance](docs/development.md).

```bash
./scripts/dev.sh status
./scripts/dev.sh logs backend
./scripts/dev.sh doctor
./scripts/dev.sh check backend
./scripts/dev.sh test frontend
./scripts/dev.sh test matoloop-agent
./scripts/dev.sh db current
./scripts/dev.sh stop
```

`check` and `test` without a target run all component checks or tests. `test rag` opts into real PostgreSQL/Infinity retrieval tests. Evaluations use `./scripts/eval.sh`; see the development guide for targets and data storage.

## Contributing

Use [GitHub issues](https://github.com/devmato/mato-loop/issues) for bug reports and feature requests. Read [AGENTS.md](AGENTS.md) for repository conventions and select the checks relevant to your change. Documentation contributions follow the [documentation maintenance guide](docs/development.md#documentation).

Noteworthy commits use an English `Release-Note: <category>: <text>` trailer; see [CI and releases](docs/ci-and-releases.md#release-process). Product versions are derived from commit dates and SHAs rather than manual version bumps.

## License

Copyright (C) 2026 MatoLoop contributors.

MatoLoop is licensed under the [GNU Affero General Public License version 3 only](LICENSE) (`AGPL-3.0-only`).
