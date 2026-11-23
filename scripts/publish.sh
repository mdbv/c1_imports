#!/usr/bin/env bash
# Build the image on this machine, tag the commit with today's date, and push
# both the image and the tag to GitHub.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "Commit or stash tracked changes before publishing." >&2
    exit 1
fi

remote=$(git remote get-url origin)
remote=${remote%.git}
remote=${remote#*github.com:}
remote=${remote#*github.com/}
image="ghcr.io/${remote,,}"

git fetch --tags origin

# YYYY.M.N — calendar month in UTC, then the build number for that month.
# 2026.11.1 is the first publish in November 2026.
# Older tags include the day (v2026.11.23.1) and are not part of this count.
month=$(date -u +%Y.%-m)
count=$(git tag -l "v${month}.*" | awk -F. 'NF==3' | wc -l)
version="${month}.$((count + 1))"
tag="v${version}"

echo "Building ${image}:${version}"
docker build \
    --label "org.opencontainers.image.version=${version}" \
    --label "org.opencontainers.image.revision=$(git rev-parse HEAD)" \
    --label "org.opencontainers.image.source=https://github.com/${remote}" \
    -t "${image}:${version}" \
    -t "${image}:latest" \
    .

echo "Pushing ${image}:${version}"
docker push "${image}:${version}"
docker push "${image}:latest"

git tag -a "$tag" -m "$tag"
# The version tag push must not start another image build.
C1_PUBLISHING=1 git push origin "$tag"

echo "Published ${image}:${version} (${tag})"
