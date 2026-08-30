#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEMVER='(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
DRY_RUN=false
VERSION=""
TAG=""

usage() {
    printf '%s\n' "Usage: ./scripts/release.sh [--dry-run] X.Y.Z"
}

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

version_is_releasable() {
    python3 - "$1" "$2" "$3" <<'PY'
import sys

current, requested = (tuple(map(int, value.split("."))) for value in sys.argv[1:3])
has_release_tags = sys.argv[3] == "true"
raise SystemExit(0 if requested > current or (requested == current and not has_release_tags) else 1)
PY
}

self_test() {
    version_is_releasable 0.1.0 0.1.0 false
    ! version_is_releasable 0.1.0 0.1.0 true
    version_is_releasable 0.1.0 0.1.1 true
    ! version_is_releasable 0.1.0 0.0.9 false
    printf 'Release version self-test passed.\n'
}

on_exit() {
    local status=$?
    if ((status != 0)); then
        set +e
        printf '\nRelease stopped with exit status %d. No automatic cleanup was performed.\n' "$status" >&2
        git -C "$ROOT" status --short --branch >&2
        if [[ -n "$TAG" ]] && git -C "$ROOT" show-ref --verify --quiet "refs/tags/$TAG"; then
            printf 'Local tag %s exists.\n' "$TAG" >&2
        fi
    fi
}

remote_head() {
    local output
    output="$(git ls-remote --heads origin "refs/heads/$1")" || die "Cannot read origin/$1."
    [[ -n "$output" ]] || die "origin/$1 does not exist."
    printf '%s' "${output%%[[:space:]]*}"
}

check_allowed_changes() {
    local path
    while IFS= read -r path; do
        case "$path" in
            VERSION|apps/backend/pyproject.toml|apps/backend/src/mato_loop_backend/__init__.py|apps/backend/uv.lock|apps/frontend/package.json|apps/frontend/yarn.lock|apps/matoloop-agent/pyproject.toml|apps/matoloop-agent/src/matoloop_agent/__init__.py|apps/matoloop-agent/uv.lock|docs/user/changelog.md|"docs/user/changelog/$TAG.md") ;;
            *) die "Release command changed unexpected file: $path" ;;
        esac
    done < <(git diff --name-only)
    while IFS= read -r path; do
        [[ "$path" == "docs/user/changelog/$TAG.md" ]] || die "Release command created unexpected file: $path"
    done < <(git ls-files --others --exclude-standard)
}

update_versions() {
    python3 - "$VERSION" <<'PY'
import re
import sys
from pathlib import Path

version = sys.argv[1]


def replace(path_string, pattern):
    path = Path(path_string)
    text = path.read_text()
    updated, count = re.subn(pattern, lambda match: f"{match.group(1)}{version}{match.group(2)}", text)
    if count != 1:
        raise SystemExit(f"Expected one version declaration in {path}, found {count}.")
    path.write_text(updated)


Path("VERSION").write_text(f"{version}\n")
replace("apps/backend/pyproject.toml", r'(?m)^(version = ")[^"]+("$)')
replace("apps/backend/src/mato_loop_backend/__init__.py", r'(?m)^(__version__ = ")[^"]+("$)')
replace("apps/frontend/package.json", r'(?m)^(  "version": ")[^"]+(",$)')
replace("apps/matoloop-agent/pyproject.toml", r'(?m)^(version = ")[^"]+("$)')
replace(
    "apps/matoloop-agent/src/matoloop_agent/__init__.py",
    r'(?m)^(__version__ = ")[^"]+("$)',
)
PY
}

trap on_exit EXIT
trap 'exit 130' INT TERM

if [[ "${1:-}" == "--self-test" ]]; then
    self_test
    exit
fi
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
    shift
fi
[[ $# -eq 1 ]] || { usage >&2; exit 2; }
VERSION="$1"
[[ "$VERSION" =~ ^$SEMVER$ ]] || die "Version must be a stable SemVer X.Y.Z."
TAG="v$VERSION"

cd "$ROOT"
command -v git >/dev/null || die "git is required."
command -v python3 >/dev/null || die "python3 is required."
python3 scripts/check-version.py
CURRENT_VERSION="$(<VERSION)"

[[ "$(git branch --show-current)" == "develop" ]] || die "Release must start on develop."
[[ -z "$(git status --porcelain=v1)" ]] || die "The develop worktree must be clean."
git remote get-url origin >/dev/null 2>&1 || die "Remote origin is not configured."
git show-ref --verify --quiet refs/heads/develop || die "Local develop does not exist."
git show-ref --verify --quiet refs/heads/main || die "Local main does not exist."

if [[ "$DRY_RUN" == false ]]; then
    printf 'Fetching origin...\n'
    git fetch --prune --tags origin
    git show-ref --verify --quiet refs/remotes/origin/develop || die "origin/develop was not fetched."
    git show-ref --verify --quiet refs/remotes/origin/main || die "origin/main was not fetched."
    DEVELOP_REMOTE="$(git rev-parse refs/remotes/origin/develop)"
    MAIN_REMOTE="$(git rev-parse refs/remotes/origin/main)"
else
    DEVELOP_REMOTE="$(remote_head develop)"
    MAIN_REMOTE="$(remote_head main)"
fi

[[ "$(git rev-parse refs/heads/develop)" == "$DEVELOP_REMOTE" ]] || die "Local develop must exactly match origin/develop."
[[ "$(git rev-parse refs/heads/main)" == "$MAIN_REMOTE" ]] || die "Local main must exactly match origin/main."
git merge-base --is-ancestor main develop || die "Local main must be an ancestor of develop."
HAS_RELEASE_TAGS=false
[[ -z "$(git tag --list 'v[0-9]*')" ]] || HAS_RELEASE_TAGS=true
REMOTE_RELEASE_TAGS="$(git ls-remote --tags origin 'refs/tags/v[0-9]*')" || die "Cannot read release tags from origin."
[[ -z "$REMOTE_RELEASE_TAGS" ]] || HAS_RELEASE_TAGS=true
version_is_releasable "$CURRENT_VERSION" "$VERSION" "$HAS_RELEASE_TAGS" \
    || die "Version must be greater than the current version, except for the first untagged release."
git show-ref --verify --quiet "refs/tags/$TAG" && die "Local tag $TAG already exists."
REMOTE_TAG="$(git ls-remote --tags origin "refs/tags/$TAG" "refs/tags/$TAG^{}")" || die "Cannot check remote tag $TAG."
[[ -z "$REMOTE_TAG" ]] || die "Remote tag $TAG already exists."
RELEASE_SOURCE="$(git rev-parse HEAD)"

printf 'Release notes for %s:\n\n' "$TAG"
python3 scripts/ci/create-forgejo-release.py "$TAG" "$RELEASE_SOURCE" --print-notes
printf '\n'

if [[ "$DRY_RUN" == true ]]; then
    printf '%s\n' \
        "Dry run passed for $TAG." \
        "A release would update versions and locks, run all checks and tests, commit on develop," \
        "fast-forward main to develop, create the annotated tag, return to develop, and push atomically."
    exit 0
fi

printf 'Updating versions to %s...\n' "$VERSION"
update_versions
python3 scripts/ci/create-forgejo-release.py \
    "$TAG" \
    "$RELEASE_SOURCE" \
    --write-changelog docs/user/changelog.md
./scripts/dev.sh lock backend
./scripts/dev.sh lock frontend
./scripts/dev.sh lock matoloop-agent
python3 scripts/check-version.py "$TAG"

printf 'Running full checks and tests...\n'
./scripts/dev.sh check
./scripts/dev.sh test
python3 scripts/check-version.py "$TAG"
git diff --check
check_allowed_changes

git add -- \
    VERSION \
    apps/backend/pyproject.toml \
    apps/backend/src/mato_loop_backend/__init__.py \
    apps/backend/uv.lock \
    apps/frontend/package.json \
    apps/frontend/yarn.lock \
    apps/matoloop-agent/pyproject.toml \
    apps/matoloop-agent/src/matoloop_agent/__init__.py \
    apps/matoloop-agent/uv.lock \
    docs/user/changelog.md \
    "docs/user/changelog/$TAG.md"
git diff --cached --quiet && die "The release produced no version changes."
git diff --quiet || die "Unstaged release changes remain."
[[ -z "$(git ls-files --others --exclude-standard)" ]] || die "Untracked files remain."

git commit -m "Release $TAG"
[[ -z "$(git status --porcelain=v1)" ]] || die "The release commit left worktree changes."
git switch main
git merge --ff-only develop
git tag -a "$TAG" -m "MatoLoop $TAG"
git switch develop

printf 'Pushing develop, main, and %s atomically...\n' "$TAG"
git push --atomic origin \
    refs/heads/develop:refs/heads/develop \
    refs/heads/main:refs/heads/main \
    "refs/tags/$TAG:refs/tags/$TAG"
printf 'Release %s completed.\n' "$TAG"
