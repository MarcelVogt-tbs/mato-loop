#!/usr/bin/env bash
set -Eeuo pipefail

VERSION_PATTERN='[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{8}'
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

tags_for() {
    [[ "$2" =~ ^$VERSION_PATTERN$ ]] || return 1
    case "$1" in
        develop) printf '%s\n' "$2" develop ;;
        release) printf '%s\n' "$2" latest ;;
        *) return 1 ;;
    esac
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

if [[ "${1:-}" == --self-test ]]; then
    [[ "$(tags_for develop 2026-10-08-12345678)" == $'2026-10-08-12345678\ndevelop' ]]
    [[ "$(tags_for release 2026-10-08-12345678)" == $'2026-10-08-12345678\nlatest' ]]
    ! tags_for release v1.2.3 >/dev/null
    docker() { printf 'manifest unknown' >&2; return 1; }
    [[ "$(remote_state example.invalid/image:missing)" == missing ]]
    docker() { printf 'registry timeout' >&2; return 1; }
    ! (remote_state example.invalid/image:missing) >/dev/null 2>&1
    test_config="$(mktemp -d)"
    trap 'rm -rf "$test_config"' EXIT
    printf '{"auths":{"docker.io":{"auth":"synthetic"}}}\n' > "$test_config/config.json"
    docker() { [[ "$1" != build ]] || cmp -s "$EXPECTED_CONFIG" "$DOCKER_CONFIG/config.json"; }
    export -f docker
    EXPECTED_CONFIG="$test_config/config.json" DOCKER_CONFIG="$test_config" \
        bash "$0" build mato-loop . apps/backend/Dockerfile
    printf 'Docker image tag self-test passed.\n'
    exit
fi

[[ $# -ge 4 && $# -le 5 ]] || die "Usage: $0 build|develop|release IMAGE CONTEXT DOCKERFILE [DATE-SHA]"
MODE="$1" IMAGE="$2" CONTEXT="$3" DOCKERFILE="$4" RELEASE_TAG="${5:-}"
[[ "$MODE" == build || "$MODE" == develop || "$MODE" == release ]] || die "Invalid mode."
[[ "$IMAGE" =~ ^[a-z0-9]+([._-][a-z0-9]+)*$ ]] || die "Invalid image name."
[[ -d "$CONTEXT" && -f "$DOCKERFILE" ]] || die "Missing build context or Dockerfile."
command -v docker >/dev/null
SOURCE_DOCKER_CONFIG="${DOCKER_CONFIG:-$HOME/.docker}"
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
if [[ -f "$SOURCE_DOCKER_CONFIG/config.json" ]]; then
    cp "$SOURCE_DOCKER_CONFIG/config.json" "$DOCKER_CONFIG/config.json"
fi

REVISION="$(git rev-parse HEAD)"
[[ -z "${GITHUB_SHA:-}" || "$GITHUB_SHA" == "$REVISION" ]] || die "Checkout does not match CI revision."
VERSION="$(python3 "$CONTEXT/libs/python/version/src/matoloop_version/__init__.py" --source "$CONTEXT")"
CREATED="$(git show -s --format=%cI HEAD)"
LOCAL_REF="$IMAGE:ci-${CI_RUN_ID:-local}"
LOCAL_REFS+=("$LOCAL_REF")
LABELS=(--label "org.opencontainers.image.created=$CREATED"
    --label "org.opencontainers.image.licenses=AGPL-3.0-only"
    --label "org.opencontainers.image.revision=$REVISION"
    --label "org.opencontainers.image.version=$VERSION")
BUILD_ARGS=(--build-arg "BUILD_REVISION=$REVISION" --build-arg "MATOLOOP_VERSION=$VERSION")
if [[ -n "${GITHUB_SERVER_URL:-}" && -n "${GITHUB_REPOSITORY:-}" ]]; then
    LABELS+=(--label "org.opencontainers.image.source=$GITHUB_SERVER_URL/$GITHUB_REPOSITORY")
fi

build_image() {
    docker build --platform linux/amd64 --file "$DOCKERFILE" --tag "$LOCAL_REF" \
        "${BUILD_ARGS[@]}" "${LABELS[@]}" "$CONTEXT"
}
check_image() {
    [[ "$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$1")" == "$REVISION" ]] \
        || die "Image revision collision for $1."
    [[ "$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$1")" == "$VERSION" ]] \
        || die "Image version mismatch for $1."
}
if [[ "$MODE" == build ]]; then
    [[ -z "$RELEASE_TAG" ]] || die "Build mode does not accept a release tag."
    build_image
    exit
fi
[[ "$VERSION" =~ ^$VERSION_PATTERN$ ]] || die "Publication requires a clean source version."
if [[ "$MODE" == release ]]; then
    [[ "$RELEASE_TAG" == "$VERSION" ]] || die "Release tag must match the source version."
else
    [[ -z "$RELEASE_TAG" ]] || die "Develop mode does not accept a release tag."
fi
[[ -n "${CONTAINER_REGISTRY:-}" && -n "${CONTAINER_USERNAME:-}" && -n "${CONTAINER_PASSWORD:-}" ]] \
    || die "Container registry credentials are required."
[[ "$CONTAINER_REGISTRY" != *://* && "$CONTAINER_REGISTRY" != */* && "$CONTAINER_REGISTRY" != *[[:space:]]* ]] \
    || die "Invalid registry host."
NAMESPACE="$(printf '%s' "${CONTAINER_IMAGE_NAMESPACE:-${GITHUB_REPOSITORY:-}}" | tr '[:upper:]' '[:lower:]')"
[[ "$NAMESPACE" =~ ^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*$ ]] || die "Invalid image namespace."
IMAGE_REF="$CONTAINER_REGISTRY/$NAMESPACE/$IMAGE"
mapfile -t TAGS < <(tags_for "$MODE" "$VERSION")
for tag in "${TAGS[@]}"; do LOCAL_REFS+=("$IMAGE_REF:$tag"); done
printf '%s' "$CONTAINER_PASSWORD" | docker login "$CONTAINER_REGISTRY" --username "$CONTAINER_USERNAME" --password-stdin

if [[ "$(remote_state "$IMAGE_REF:$VERSION")" == exists ]]; then
    docker pull "$IMAGE_REF:$VERSION" >/dev/null
    check_image "$IMAGE_REF:$VERSION"
    docker tag "$IMAGE_REF:$VERSION" "$LOCAL_REF"
else
    if ! docker image inspect "$LOCAL_REF" >/dev/null 2>&1; then build_image; fi
    check_image "$LOCAL_REF"
    docker tag "$LOCAL_REF" "$IMAGE_REF:$VERSION"
    docker push "$IMAGE_REF:$VERSION"
fi
ALIAS="${TAGS[1]}"
if [[ "$ALIAS" == latest && "$(remote_state "$IMAGE_REF:latest")" == exists ]]; then
    docker pull "$IMAGE_REF:latest" >/dev/null
    LATEST_REVISION="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$IMAGE_REF:latest")"
    [[ "$LATEST_REVISION" =~ ^[0-9a-f]{40,64}$ ]] || die "Latest image has no source revision."
    if [[ "$LATEST_REVISION" != "$REVISION" ]]; then
        if git merge-base --is-ancestor "$REVISION" "$LATEST_REVISION"; then
            printf 'Keeping latest at newer revision %s.\n' "$LATEST_REVISION"
            exit
        fi
        git merge-base --is-ancestor "$LATEST_REVISION" "$REVISION" || die "Latest image has unrelated or unavailable history."
    fi
fi
docker tag "$LOCAL_REF" "$IMAGE_REF:$ALIAS"
docker push "$IMAGE_REF:$ALIAS"
