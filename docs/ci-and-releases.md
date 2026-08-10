# Forgejo CI and Releases

Forgejo Actions verifies MatoLoop and publishes release artifacts. It does not deploy MatoLoop, update a server, touch deployment storage, or perform Docker garbage collection on runners.

## Published Artifacts

The registry contains two OCI images:

```text
${CONTAINER_REGISTRY}/${CONTAINER_IMAGE_NAMESPACE}/mato-loop
${CONTAINER_REGISTRY}/${CONTAINER_IMAGE_NAMESPACE}/mato-loop-execution-agent
```

Both images currently target `linux/amd64` only. There is no multi-architecture manifest and no native Execution Agent archive yet.

The tag policy deliberately limits mutable artifact storage:

| Source | Checks | Image build | Publish |
| --- | --- | --- | --- |
| Pull request | Full checks and tests | Both images | Never |
| Feature branch push | Checks and tests only | No | Never |
| `develop` push | Full checks and tests | Both images | Mutable `develop` |
| `main` push | Full checks and tests | No | Never; release tags only |
| Release `vX.Y.Z` | Full checks and tests | Both images | `vX.Y.Z`, `X.Y.Z`, `latest` |

Feature branches therefore consume runner cache only, `develop` retains one named moving tag, and releases retain fixed version tags. Forgejo registry retention and unreferenced-blob cleanup remain instance-administrator responsibilities.

Pull requests run the complete test and production-image build path, but registry login, publication, and release creation are disabled. Repository secrets must not be passed to pull request jobs. This applies to same-repository and fork pull requests.

## Runner Requirements

Jobs require a self-hosted Forgejo Runner registered with the `ubuntu-latest` label and running on `linux/amd64`. The runner needs:

- a Docker daemon available to job containers, commonly through `/var/run/docker.sock` or an isolated Docker service
- Bash, Python 3.11 or newer, Git, and the Docker Compose plugin
- permission to build, tag, inspect, and push OCI images
- outbound access to the Forgejo instance, GitHub for the SHA-pinned checkout action, base-image registries, and the Forgejo container registry
- enough CPU, memory, and disk for the backend/frontend image and Execution Agent image to build concurrently
- a current Git client and normal TLS trust for the configured endpoints
- Forgejo Actions task-queue concurrency enabled so publication workflows with the same group are serialized

Docker socket access is effectively root access to the runner host. Pull request builds execute repository code and Dockerfiles even though they receive no secrets. Run untrusted pull requests only on ephemeral, isolated runners with no production mounts, credentials, internal-network privileges, or reusable trusted-job workspace. Do not colocate this runner with a MatoLoop production deployment.

Forgejo Runner does not own host Docker retention. Configure image, layer, BuildKit cache, container, and volume garbage collection outside the workflows and monitor disk use. Never solve runner pressure by mounting or pruning deployment Docker storage.

## Forgejo Configuration

Configure these repository or organization Action variables:

| Variable | Value |
| --- | --- |
| `CONTAINER_REGISTRY` | Forgejo registry host and optional port, without `https://` |
| `CONTAINER_IMAGE_NAMESPACE` | Optional image path; defaults to the lowercase Forgejo `owner/repository` |

Configure these Action secrets only for trusted `develop` and release jobs:

| Secret | Purpose |
| --- | --- |
| `CONTAINER_USERNAME` | Dedicated registry publisher account |
| `CONTAINER_PASSWORD` | Token with permission to write packages in the image namespace |
| `FORGEJO_TOKEN` | Token with repository release write permission |

The release job uses the configured `FORGEJO_TOKEN`; it is not a registry credential. Pull request jobs receive none of these configured secrets. The runner registration token belongs in the runner's external configuration, not in repository variables or secrets.

Use a dedicated least-privilege publisher account. The registry token needs no MatoLoop application, database, deployment-host, provider, or Execution Agent credentials.

Protect `develop`, `main`, and `v*` tags so only trusted maintainers can modify them. Require the CI workflow for branch changes and protect reviews of `.forgejo/`, `scripts/ci/`, `scripts/release.sh`, Dockerfiles, `VERSION`, and package lockfiles. The Forgejo server URL must use HTTPS.

## Release Process

Releases originate from `develop` and fast-forward release-only `main`. Start one explicitly from a clean, current `develop` checkout:

```bash
./scripts/release.sh X.Y.Z
```

Use the numeric version without a leading `v`. The script synchronizes package versions and locks, runs all checks and tests, commits on `develop`, fast-forwards `main`, creates `vX.Y.Z`, returns to `develop`, and atomically pushes both branches and the tag. CI accepts only annotated release tags whose commit is on `main`. Do not create release tags manually or publish from arbitrary commits.

After checks and both `linux/amd64` builds succeed, the release job publishes these tags on both images:

```text
vX.Y.Z
X.Y.Z
latest
```

It then creates the matching Forgejo Release. Release notes are the Forgejo commit notes since the previous release, so release commits and merged change commits must have meaningful subjects. Workflow retries preserve existing fixed tags, publication workflows are serialized, and `latest` never moves to an older version. Forgejo concurrency is best-effort, so the required task queue and branch/tag protections remain part of the integrity boundary. Registry pushes across two repositories are not transactional: if publication stops partway through, no Forgejo Release is created; repair any incomplete fixed-tag pair within one image before retrying. Artifact publication is not deployment automation; operators choose when to pull a version.

## Compose and Rollback

`docker-compose.yaml` keeps local builds and adds optional image names. With no overrides, build locally:

```bash
docker compose --profile execution-agent build
docker compose --profile execution-agent up -d
```

To consume published images, set fixed tags and pull before recreating containers:

```env
MATO_LOOP_IMAGE=${CONTAINER_REGISTRY}/${CONTAINER_IMAGE_NAMESPACE}/mato-loop:X.Y.Z
MATO_LOOP_EXECUTION_AGENT_IMAGE=${CONTAINER_REGISTRY}/${CONTAINER_IMAGE_NAMESPACE}/mato-loop-execution-agent:X.Y.Z
```

```bash
docker compose --profile execution-agent pull
docker compose --profile execution-agent up -d
```

Use the same version for the backend and Execution Agent. `latest` is convenient for discovery but is not a reproducible deployment reference.

For a mutable `develop` test environment, use a separate checkout or deployment directory so the relative `./data` and `./db` bind mounts cannot overlap stable storage. Set both image variables to `:develop` and run `docker compose pull` before every recreation. Never attach a `develop` deployment to production data.

Rollback by setting both image variables to the previous fixed `X.Y.Z` tag, pulling, and recreating the containers. Because startup applies Alembic migrations, an image rollback may also require restoring the database and `/data` from the matching pre-upgrade backup. Never use `develop` or `latest` as a rollback target.
