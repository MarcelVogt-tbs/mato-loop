#!/usr/bin/env bash
set -Eeuo pipefail

SEMVER='(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

tags_for() {
    local mode="$1" tag="${2:-}"
    case "$mode" in
        develop)
            [[ "$tag" =~ ^[0-9a-f]{12,64}$ ]] || return 1
            printf '%s\n' develop "sha-${tag:0:12}"
            ;;
        release)
            [[ "$tag" =~ ^v$SEMVER$ ]] || return 1
            printf '%s\n' "$tag" "${tag#v}" latest
            ;;
        *) return 1 ;;
    esac
}

version_at_least() {
    python3 - "$1" "$2" <<'PY'
import sys

current, requested = (tuple(map(int, value.split("."))) for value in sys.argv[1:])
raise SystemExit(0 if current >= requested else 1)
PY
}

remote_state() {
    local output
    if output="$(docker manifest inspect "$1" 2>&1)"; then
        printf exists
    elif [[ "$output" == *"no such manifest"* || "$output" == *"manifest unknown"* || "$output" == *"not found"* ]]; then
        printf missing
    else
        die "Cannot inspect remote image $1: $output"
    fi
}

self_test() {
    local tags
    mapfile -t tags < <(tags_for release v1.2.3)
    [[ "${tags[*]}" == "v1.2.3 1.2.3 latest" ]]
    [[ "$(tags_for develop 0123456789abcdef)" == $'develop\nsha-0123456789ab' ]]
    ! tags_for develop short >/dev/null 2>&1
    ! tags_for release v1.2 >/dev/null 2>&1
    version_at_least 1.2.3 1.2.3
    version_at_least 2.0.0 1.9.9
    ! version_at_least 1.9.9 2.0.0
    docker() { printf 'manifest unknown' >&2; return 1; }
    [[ "$(remote_state example.invalid/image:v1.2.3)" == missing ]]
    docker() { printf 'registry timeout' >&2; return 1; }
    ! (remote_state example.invalid/image:v1.2.3) >/dev/null 2>&1
    printf 'Docker image tag self-test passed.\n'
}

if [[ "${1:-}" == --self-test ]]; then
    self_test
    exit
fi

[[ $# -ge 4 && $# -le 5 ]] || die "Usage: $0 build|develop|release IMAGE CONTEXT DOCKERFILE [vX.Y.Z]"
MODE="$1"
IMAGE="$2"
CONTEXT="$3"
DOCKERFILE="$4"
RELEASE_TAG="${5:-}"
[[ "$IMAGE" =~ ^[a-z0-9]+([._-][a-z0-9]+)*$ ]] || die "Invalid image name: $IMAGE"
[[ -d "$CONTEXT" ]] || die "Build context does not exist: $CONTEXT"
[[ -f "$DOCKERFILE" ]] || die "Dockerfile does not exist: $DOCKERFILE"
command -v docker >/dev/null || die "docker is required."

DOCKER_CONFIG="$(mktemp -d "${TMPDIR:-/tmp}/mato-loop-docker.XXXXXX")"
export DOCKER_CONFIG
LOCAL_REFS=()

cleanup() {
    if [[ "${KEEP_LOCAL_IMAGE:-}" != 1 ]] && ((${#LOCAL_REFS[@]})); then
        docker image rm --force "${LOCAL_REFS[@]}" >/dev/null 2>&1 || true
    fi
    rm -rf "$DOCKER_CONFIG"
}
trap cleanup EXIT

REVISION="${GITHUB_SHA:-$(git rev-parse HEAD)}"
SOURCE_URL="${GITHUB_SERVER_URL:-}/${GITHUB_REPOSITORY:-}"
CREATED="$(git show -s --format=%cI "$REVISION")"
PROJECT_VERSION="$(<VERSION)"
LABELS=(
    --label "org.opencontainers.image.created=$CREATED"
    --label "org.opencontainers.image.revision=$REVISION"
)
BUILD_ARGS=(--build-arg "BUILD_REVISION=$REVISION")
if [[ -n "${GITHUB_SERVER_URL:-}" && -n "${GITHUB_REPOSITORY:-}" ]]; then
    LABELS+=(--label "org.opencontainers.image.source=$SOURCE_URL")
fi

if [[ "$MODE" == build ]]; then
    [[ -z "$RELEASE_TAG" ]] || die "The build mode does not accept a release tag."
    LOCAL_REF="$IMAGE:ci-${CI_RUN_ID:-local}"
    LOCAL_REFS+=("$LOCAL_REF")
    LABELS+=(--label "org.opencontainers.image.version=${IMAGE_VERSION:-$PROJECT_VERSION}")
    docker build \
        --platform linux/amd64 \
        --file "$DOCKERFILE" \
        --tag "$LOCAL_REF" \
        "${BUILD_ARGS[@]}" \
        "${LABELS[@]}" \
        "$CONTEXT"
    exit
fi

[[ "$MODE" == develop || "$MODE" == release ]] || die "Unknown mode: $MODE"
[[ -n "${CONTAINER_REGISTRY:-}" ]] || die "CONTAINER_REGISTRY is required."
CONTAINER_IMAGE_NAMESPACE="${CONTAINER_IMAGE_NAMESPACE:-${GITHUB_REPOSITORY:-}}"
[[ -n "$CONTAINER_IMAGE_NAMESPACE" ]] || die "CONTAINER_IMAGE_NAMESPACE or GITHUB_REPOSITORY is required."
[[ -n "${CONTAINER_USERNAME:-}" ]] || die "CONTAINER_USERNAME is required."
[[ -n "${CONTAINER_PASSWORD:-}" ]] || die "CONTAINER_PASSWORD is required."
[[ "$CONTAINER_REGISTRY" != *://* && "$CONTAINER_REGISTRY" != */* && "$CONTAINER_REGISTRY" != *[[:space:]]* ]] || die "CONTAINER_REGISTRY must be a registry host without a scheme or path."
CONTAINER_IMAGE_NAMESPACE="$(printf '%s' "$CONTAINER_IMAGE_NAMESPACE" | tr '[:upper:]' '[:lower:]')"
[[ "$CONTAINER_IMAGE_NAMESPACE" =~ ^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*$ ]] || die "CONTAINER_IMAGE_NAMESPACE must be a lowercase OCI image path."

TAG_SOURCE="$RELEASE_TAG"
[[ "$MODE" != develop ]] || TAG_SOURCE="$REVISION"
TAG_OUTPUT="$(tags_for "$MODE" "$TAG_SOURCE")" || die "Release tags must use stable SemVer vX.Y.Z and develop revisions must be hexadecimal."
mapfile -t TAGS <<<"$TAG_OUTPUT"
IMAGE_REF="$CONTAINER_REGISTRY/$CONTAINER_IMAGE_NAMESPACE/$IMAGE"
LOCAL_REF="$IMAGE:ci-${CI_RUN_ID:-local}"
LOCAL_REFS+=("$LOCAL_REF")
for tag in "${TAGS[@]}"; do
    LOCAL_REFS+=("$IMAGE_REF:$tag")
done

printf '%s' "$CONTAINER_PASSWORD" | docker login "$CONTAINER_REGISTRY" --username "$CONTAINER_USERNAME" --password-stdin
if [[ "$MODE" == develop && "$(remote_state "$IMAGE_REF:${TAGS[1]}")" == exists ]]; then
    docker pull "$IMAGE_REF:${TAGS[1]}" >/dev/null
    EXISTING_REVISION="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$IMAGE_REF:${TAGS[1]}")"
    [[ "$EXISTING_REVISION" == "$REVISION" ]] || die "Short commit tag collision for $IMAGE_REF:${TAGS[1]}."
fi
if [[ "$MODE" == release ]]; then
    version_exists=false
    numeric_exists=false
    VERSION_STATE="$(remote_state "$IMAGE_REF:$RELEASE_TAG")"
    NUMERIC_STATE="$(remote_state "$IMAGE_REF:${RELEASE_TAG#v}")"
    [[ "$VERSION_STATE" == exists ]] && version_exists=true
    [[ "$NUMERIC_STATE" == exists ]] && numeric_exists=true
    if [[ "$version_exists" == true && "$numeric_exists" == true ]]; then
        docker pull "$IMAGE_REF:$RELEASE_TAG" >/dev/null
        docker pull "$IMAGE_REF:${RELEASE_TAG#v}" >/dev/null
        VERSION_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE_REF:$RELEASE_TAG")"
        NUMERIC_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE_REF:${RELEASE_TAG#v}")"
        [[ "$VERSION_ID" == "$NUMERIC_ID" ]] || die "Fixed release tags point to different images."
        FIXED_VERSION="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$IMAGE_REF:$RELEASE_TAG")"
        FIXED_REVISION="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$IMAGE_REF:$RELEASE_TAG")"
        [[ "$FIXED_VERSION" == "${RELEASE_TAG#v}" && "$FIXED_REVISION" == "$REVISION" ]] || die "Existing fixed release tags do not match this release."
        LATEST_STATE="$(remote_state "$IMAGE_REF:latest")"
        if [[ "$LATEST_STATE" == exists ]]; then
            docker pull "$IMAGE_REF:latest" >/dev/null
            LATEST_VERSION="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$IMAGE_REF:latest")"
            [[ "$LATEST_VERSION" =~ ^$SEMVER$ ]] || die "Existing latest image has no stable SemVer label."
            if [[ "$LATEST_VERSION" == "${RELEASE_TAG#v}" ]]; then
                LATEST_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE_REF:latest")"
                [[ "$LATEST_ID" == "$VERSION_ID" ]] || die "Latest and fixed tags for this version point to different images."
                printf 'Release image %s already exists and latest matches.\n' "$IMAGE_REF"
                exit
            fi
            if version_at_least "$LATEST_VERSION" "${RELEASE_TAG#v}"; then
                printf 'Release image %s already exists; keeping latest at %s.\n' "$IMAGE_REF" "$LATEST_VERSION"
                exit
            fi
        fi
        docker tag "$IMAGE_REF:$RELEASE_TAG" "$IMAGE_REF:latest"
        docker push "$IMAGE_REF:latest"
        printf 'Restored latest for existing release image %s.\n' "$IMAGE_REF"
        exit
    fi
    [[ "$version_exists" == false && "$numeric_exists" == false ]] || die "Release image has only one fixed version tag; repair the registry before retrying."
fi
PUSH_LATEST=true
if [[ "$MODE" == release ]]; then
    LATEST_STATE="$(remote_state "$IMAGE_REF:latest")"
    if [[ "$LATEST_STATE" == exists ]]; then
        docker pull "$IMAGE_REF:latest" >/dev/null
        LATEST_VERSION="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$IMAGE_REF:latest")"
        [[ "$LATEST_VERSION" =~ ^$SEMVER$ ]] || die "Existing latest image has no stable SemVer label."
        if version_at_least "$LATEST_VERSION" "${RELEASE_TAG#v}"; then
            if [[ "$LATEST_VERSION" == "${RELEASE_TAG#v}" ]]; then
                LATEST_REVISION="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$IMAGE_REF:latest")"
                [[ "$LATEST_REVISION" == "$REVISION" ]] || die "Existing latest image for this version points to a different revision."
                docker tag "$IMAGE_REF:latest" "$LOCAL_REF"
            fi
            PUSH_LATEST=false
        fi
    fi
fi
LABELS+=(--label "org.opencontainers.image.version=$([[ "$MODE" == release ]] && printf '%s' "${RELEASE_TAG#v}" || printf develop)")
if docker image inspect "$LOCAL_REF" >/dev/null 2>&1; then
    LOCAL_VERSION="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$LOCAL_REF")"
    EXPECTED_VERSION="$([[ "$MODE" == release ]] && printf '%s' "${RELEASE_TAG#v}" || printf develop)"
    [[ "$LOCAL_VERSION" == "$EXPECTED_VERSION" ]] || die "Prepared image $LOCAL_REF has version label $LOCAL_VERSION, expected $EXPECTED_VERSION."
else
    docker build --platform linux/amd64 --file "$DOCKERFILE" --tag "$LOCAL_REF" "${BUILD_ARGS[@]}" "${LABELS[@]}" "$CONTEXT"
fi
for tag in "${TAGS[@]}"; do
    [[ "$tag" != latest || "$PUSH_LATEST" == true ]] || continue
    docker tag "$LOCAL_REF" "$IMAGE_REF:$tag"
    docker push "$IMAGE_REF:$tag"
done
