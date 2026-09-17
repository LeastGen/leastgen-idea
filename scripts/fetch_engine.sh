#!/usr/bin/env bash
# fetch_engine.sh — vendor the Microsoft ResearchStudio-Idea engine.
#
# The engine (researchstudio/) is git-ignored and NOT committed to this repo,
# so a fresh clone needs this script to fetch it. Idempotent: skips when the
# engine entry point already exists.
#
# Usage:
#   bash scripts/fetch_engine.sh              # fetch pinned ref (default)
#   ENGINE_REF=<sha|branch> bash scripts/fetch_engine.sh   # override pin
#
# Pinned to a known-good upstream commit; override with ENGINE_REF to move.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENGINE_DIR="$PROJECT_ROOT/researchstudio"
ENGINE_ENTRY="$ENGINE_DIR/ResearchStudio-Idea/skills/idea_spark/scripts/run.py"

UPSTREAM_URL="${ENGINE_URL:-https://github.com/microsoft/ResearchStudio.git}"
PINNED_REF="c286aacc7941f4fcdd59fa2ae669a4ddec57935b"
ENGINE_REF="${ENGINE_REF:-$PINNED_REF}"

if [ -f "$ENGINE_ENTRY" ]; then
  echo "Engine already present at researchstudio/ — skipping fetch."
  exit 0
fi

if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git is required to fetch the ResearchStudio engine." >&2
  exit 1
fi

echo "Fetching ResearchStudio engine (ref $ENGINE_REF) from $UPSTREAM_URL ..."
rm -rf "$ENGINE_DIR"
git clone --depth 1 --filter=blob:none --sparse "$UPSTREAM_URL" "$ENGINE_DIR"
git -C "$ENGINE_DIR" sparse-checkout set ResearchStudio-Idea || true
# Try the pinned ref; fall back to whatever the shallow clone gave us.
git -C "$ENGINE_DIR" fetch --depth 1 origin "$ENGINE_REF" 2>/dev/null \
  && git -C "$ENGINE_DIR" checkout --detach "$ENGINE_REF" 2>/dev/null || true

if [ ! -f "$ENGINE_ENTRY" ]; then
  echo "ERROR: engine fetch failed — expected entry point missing:" >&2
  echo "  $ENGINE_ENTRY" >&2
  echo "Check network access to $UPSTREAM_URL and retry." >&2
  exit 1
fi

echo "Engine ready at researchstudio/ ($(git -C "$ENGINE_DIR" rev-parse --short HEAD 2>/dev/null || echo 'unknown ref'))."
