#!/usr/bin/env bash
# Recreate the repo fixture as a one-commit git repository (fixed identity and dates) in <dest>.
set -euo pipefail
src="$(cd "$(dirname "$0")/repo" && pwd)"; dest="$1"
mkdir -p "$dest"; cp -a "$src/." "$dest/"; cd "$dest"
export GIT_AUTHOR_NAME=fixture GIT_AUTHOR_EMAIL=fixture@example.invalid GIT_COMMITTER_NAME=fixture \
       GIT_COMMITTER_EMAIL=fixture@example.invalid GIT_AUTHOR_DATE='2026-10-01T00:00:00Z' GIT_COMMITTER_DATE='2026-10-01T00:00:00Z'
git -c init.defaultBranch=main init -q . && git add -A && git -c commit.gpgsign=false commit -qm 'fixture: contract repo'
git rev-parse HEAD
