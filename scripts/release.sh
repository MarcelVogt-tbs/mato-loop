#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=false

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
on_exit() {
    local status=$?
    if ((status != 0)); then
        printf '\nRelease stopped with exit status %d. No automatic cleanup was performed.\n' "$status" >&2
        git -C "$ROOT" status --short --branch >&2
    fi
}
trap on_exit EXIT
trap 'exit 130' INT TERM

[[ "${1:-}" != --dry-run ]] || { DRY_RUN=true; shift; }
[[ $# == 0 ]] || die "Usage: ./scripts/release.sh [--dry-run]"
cd "$ROOT"
command -v python3 >/dev/null || die "python3 is required."
[[ "$(git branch --show-current)" == develop ]] || die "Release must start on develop."
[[ -z "$(git status --porcelain=v1)" ]] || die "The develop worktree must be clean."
git remote get-url origin >/dev/null
if [[ "$DRY_RUN" == false ]]; then
    git fetch --prune --tags origin
fi
for branch in develop main; do
    remote="$(git ls-remote --heads origin "refs/heads/$branch")"
    [[ -n "$remote" && "$(git rev-parse "$branch")" == "${remote%%[[:space:]]*}" ]] \
        || die "Local $branch must exactly match origin/$branch."
done
git merge-base --is-ancestor main develop || die "main must be an ancestor of develop."
[[ "$(git rev-parse main)" != "$(git rev-parse develop)" ]] || die "No commits to release."
SOURCE="$(git rev-parse HEAD)"
SOURCE_VERSION="$(python3 scripts/check-version.py)"
python3 scripts/ci/create-release.py "$SOURCE_VERSION" "$SOURCE" --print-notes
if [[ "$DRY_RUN" == true ]]; then
    printf '%s\n' "Dry run passed. A release would commit dated notes, run checks and tests," \
        "fast-forward main, tag the final commit with its date/SHA version, and push atomically."
    exit
fi

python3 scripts/ci/create-release.py "$SOURCE_VERSION" "$SOURCE" \
    --write-changelog docs/user/changelog.md
./scripts/dev.sh check
./scripts/dev.sh test
git diff --check
[[ "$(git diff --name-only)" == docs/user/changelog.md ]] || die "Unexpected release changes."
[[ -z "$(git ls-files --others --exclude-standard)" ]] || die "Untracked release files remain."
git add -- docs/user/changelog.md
git commit -m "Prepare release notes"
[[ -z "$(git status --porcelain=v1)" ]] || die "The release commit left worktree changes."
TAG="$(python3 scripts/check-version.py)"
git show-ref --verify --quiet "refs/tags/$TAG" && die "Tag $TAG already exists."
REMOTE_TAG="$(git ls-remote --tags origin "refs/tags/$TAG")"
[[ -z "$REMOTE_TAG" ]] || die "Remote tag $TAG already exists."
git switch main
git merge --ff-only develop
git tag -a "$TAG" -m "MatoLoop $TAG"
git switch develop
git push --atomic origin refs/heads/develop:refs/heads/develop \
    refs/heads/main:refs/heads/main "refs/tags/$TAG:refs/tags/$TAG"
printf 'Release %s completed.\n' "$TAG"
