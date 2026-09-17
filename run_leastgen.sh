#!/usr/bin/env bash
# run_ideaflow.sh — IdeaFlow pipeline entry point
#
# Activates the venv, sources API keys, and runs the IdeaSpark pipeline
# on OpenRouter (no Claude Code required).
#
# Usage:
#   ./run_ideaflow.sh check                    # verify connectors
#   ./run_ideaflow.sh phase0 "research question"  # Phase 0 literature search
#   ./run_ideaflow.sh run "research question"     # full pipeline
#   ./run_ideaflow.sh next                       # show next step (resume)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
SKILL_DIR="$SCRIPT_DIR/researchstudio/ResearchStudio-Idea/skills/idea_spark"
LLM_BRIDGE="$SCRIPT_DIR/llm_bridge.py"

# ── Engine guard (researchstudio/ is git-ignored; fetch via scripts/fetch_engine.sh) ─
if [ ! -f "$SKILL_DIR/scripts/run.py" ]; then
  echo "ERROR: ResearchStudio engine not found at researchstudio/." >&2
  echo "Fetch it with: bash scripts/fetch_engine.sh" >&2
  exit 1
fi

# ── Activate venv ──────────────────────────────────────────────────────────
if [ -f "$VENV/bin/activate" ]; then
  source "$VENV/bin/activate"
else
  echo "ERROR: venv not found at $VENV. Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

# ── Load API keys ──────────────────────────────────────────────────────────
# Load OpenRouter key from ~/.kinox/env if available, fallback to .env
if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  if [ -f "$HOME/.kinox/env" ]; then
    export OPENROUTER_API_KEY="$(grep -oP 'OPENROUTER_API_KEY=\K.*' "$HOME/.kinox/env" 2>/dev/null | head -1 || echo '')"
  fi
fi

# ── Set the LLM bridge env vars ────────────────────────────────────────────
# These are the env vars that the idea_spark scripts check for LLM calls.
# The bridge reads stdin (<<SYSTEM>>...<<USER>>) and returns JSON via OpenRouter.
export NOVELTY_LLM_CLASSIFY_FAST_CMD="python3 $LLM_BRIDGE --mode classify-fast"
export NOVELTY_LLM_REASONING_LARGE_CMD="python3 $LLM_BRIDGE --mode reasoning-large"

# ── Commands ────────────────────────────────────────────────────────────────

case "${1:-help}" in
  check|check_connectors)
    echo "═══ Checking IdeaSpark connectors ═══"
    python3 "$SKILL_DIR/scripts/run.py" check_connectors
    echo ""
    echo "═══ Testing LLM bridge ═══"
    echo '<<SYSTEM>>
Return JSON: {"status": "ok"}
<<USER>>
Test.
' | $NOVELTY_LLM_CLASSIFY_FAST_CMD
    echo "LLM bridge OK ✓"
    ;;

  phase0)
    QUERY="${2:-}"
    if [ -z "$QUERY" ]; then
      echo "Usage: $0 phase0 \"your research question\""
      exit 1
    fi
    SLUG="$(echo "$QUERY" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | sed 's/^-//;s/-$//')"
    RUN_DIR="$PWD/ideaspark_run/$SLUG"
    mkdir -p "$RUN_DIR/phase0"
    echo "═══ Phase 0: Literature search ═══"
    echo "  Query:  $QUERY"
    echo "  Output: $RUN_DIR/phase0/"
    echo ""
    python3 "$SKILL_DIR/scripts/run.py" phase0 \
      --query "$QUERY" \
      --out "$RUN_DIR/phase0/"
    echo ""
    echo "Done. Results in: $RUN_DIR/phase0/"
    ;;

  next)
    RUN_DIR="${2:-}"
    if [ -z "$RUN_DIR" ]; then
      # Find the most recent run dir
      RUN_DIR="$(ls -dt $PWD/ideaspark_run/*/ 2>/dev/null | head -1 || echo '')"
      if [ -z "$RUN_DIR" ]; then
        echo "No run directories found in $PWD/ideaspark_run/"
        echo "Usage: $0 next /path/to/run_dir"
        exit 1
      fi
    fi
    echo "═══ Next step for: $RUN_DIR ═══"
    python3 "$SKILL_DIR/scripts/run.py" next --dir "$RUN_DIR"
    ;;

  run)
    QUERY="${2:-}"
    if [ -z "$QUERY" ]; then
      echo "Usage: $0 run \"your research question\""
      exit 1
    fi
    SLUG="$(echo "$QUERY" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | sed 's/^-//;s/-$//')"
    RUN_DIR="$PWD/ideaspark_run/$SLUG"
    mkdir -p "$RUN_DIR"
    echo "═══ IdeaFlow — Full Pipeline ═══"
    echo "  Query:  $QUERY"
    echo "  Run:    $RUN_DIR"
    echo ""

    # Phase 0
    echo "── Phase 0: Literature search ──"
    mkdir -p "$RUN_DIR/phase0"
    python3 "$SKILL_DIR/scripts/run.py" phase0 \
      --query "$QUERY" \
      --out "$RUN_DIR/phase0/" || echo "  (Phase 0 degraded — continuing with available connectors)"

    # Show next step
    echo ""
    echo "── Phase graph (next steps) ──"
    python3 "$SKILL_DIR/scripts/run.py" next --dir "$RUN_DIR" --query "$QUERY" || true
    echo ""
    echo "Run dir: $RUN_DIR"
    echo "To resume: $0 next $RUN_DIR"
    ;;

  help|--help|-h)
    echo "IdeaFlow — ResearchStudio-Idea on OpenRouter"
    echo ""
    echo "Usage:"
    echo "  $0 check                    # verify connectors and LLM bridge"
    echo "  $0 phase0 \"<query>\"        # Phase 0: literature search"
    echo "  $0 next [run_dir]           # show next step (resume)"
    echo "  $0 run \"<query>\"           # full pipeline"
    echo "  $0 help                     # this help"
    echo ""
    echo "Environment:"
    echo "  OPENROUTER_API_KEY   (loaded from ~/.kinox/env)"
    echo "  IDEAS_FLOW_FAST_MODEL   (default: deepseek/deepseek-v4-flash)"
    echo "  IDEAS_FLOW_LARGE_MODEL  (default: deepseek/deepseek-v4-flash)"
    echo "  IDEAS_FLOW_TIMEOUT      (default: 180s)"
    ;;

  *)
    echo "Unknown command: $1"
    echo "Usage: $0 check|phase0|run|next|help"
    exit 1
    ;;
esac