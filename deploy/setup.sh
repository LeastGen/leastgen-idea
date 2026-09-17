#!/usr/bin/env bash
# ==============================================================================
# LeastGen one-command setup (developer / self-host quickstart)
# Fetched by the README one-liner:
#   curl -fsSL https://raw.githubusercontent.com/LeastGen/leastgen-idea/main/deploy/setup.sh | bash
#
# Clones (or updates) the repo, fetches the ResearchStudio engine, creates a
# venv, installs requirements.txt, and prints next steps. Safe to re-run.
# For hardened production deploys use deploy/alibaba-deploy.sh instead.
# ==============================================================================
set -euo pipefail

REPO_URL="${LEASTGEN_REPO_URL:-https://github.com/LeastGen/leastgen-idea.git}"
INSTALL_DIR="${LEASTGEN_DIR:-$HOME/leastgen-idea}"
BRANCH="${LEASTGEN_BRANCH:-main}"

info() { echo "==> $*"; }
warn() { echo "[WARN] $*" >&2; }

command -v git >/dev/null 2>&1 || { echo "ERROR: git is required." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 (3.11+) is required." >&2; exit 1; }

if [ -d "$INSTALL_DIR/.git" ]; then
  info "Updating existing checkout at $INSTALL_DIR ..."
  git -C "$INSTALL_DIR" fetch origin "$BRANCH" --depth 1
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" || true
else
  info "Cloning $REPO_URL ($BRANCH) into $INSTALL_DIR ..."
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

info "Fetching ResearchStudio engine (pinned ref, MIT) ..."
bash scripts/fetch_engine.sh

if [ ! -d ".venv" ]; then
  info "Creating virtual environment (.venv) ..."
  python3 -m venv .venv
fi
info "Installing dependencies from requirements.txt ..."
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -r requirements.txt

if [ -z "${OPENROUTER_API_KEY:-}" ] && [ ! -f "$HOME/.kinox/env" ]; then
  warn "OPENROUTER_API_KEY is not set. Export it or add it to ~/.kinox/env before starting."
fi

cat <<EOF

Setup complete.

  cd $INSTALL_DIR
  source .venv/bin/activate   # or: . .venv/bin/activate
  # export OPENROUTER_API_KEY=sk-or-v1-...
  uvicorn backend.main:app --host 0.0.0.0 --port 8756

Open http://localhost:8756 in your browser.
EOF
