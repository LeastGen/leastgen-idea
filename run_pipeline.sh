#!/usr/bin/env bash
# run_pipeline.sh — IdeaFlow full pipeline orchestrator
#
# Automates the deterministic phases of the IdeaSpark pipeline and manages
# the run directory lifecycle. LLM phases (1, 2.1, 2.2, 2.3, 3.2, 3.3, 4.fill)
# must be run by the agent — this script handles everything else.
#
# Usage:
#   ./run_pipeline.sh start "research query"   # Phase 0 + 0+
#   ./run_pipeline.sh status                   # Show run status
#   ./run_pipeline.sh phase0 "query"           # Run Phase 0 only
#   ./run_pipeline.sh phase0-fulltext          # Run Phase 0+ full-text fetch
#   ./run_pipeline.sh collision                # Phase 3.1 collision check
#   ./run_pipeline.sh skeleton                 # Phase 4 skeleton
#   ./run_pipeline.sh assemble <fill-map>      # Phase 4 assembly
#   ./run_pipeline.sh render                   # Phase 4 render
#   ./run_pipeline.sh list                     # List all runs
#   ./run_pipeline.sh clean <run-id>           # Remove a run

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
SKILL_DIR="$SCRIPT_DIR/researchstudio/ResearchStudio-Idea/skills/idea_spark"
LLM_BRIDGE="$SCRIPT_DIR/llm_bridge.py"
RUN_BASE="$SCRIPT_DIR/ideaspark_run"

# ── Colors ────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}═══${NC} $1"; }
ok()    { echo -e "${GREEN}✓${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠${NC} $1"; }
err()   { echo -e "${RED}✗${NC} $1"; }

# ── Engine guard ─────────────────────────────────────────────────────────────
# researchstudio/ is git-ignored and fetched on demand (scripts/fetch_engine.sh).
require_engine() {
    if [ ! -f "$SKILL_DIR/scripts/run.py" ]; then
        err "ResearchStudio engine not found at researchstudio/."
        err "Fetch it with: bash scripts/fetch_engine.sh"
        exit 1
    fi
}

# ── Activate venv ──────────────────────────────────────────────────────────
activate_venv() {
    require_engine
    if [ -f "$VENV/bin/activate" ]; then
        source "$VENV/bin/activate"
    else
        err "venv not found at $VENV"
        exit 1
    fi
}

# ── Load API keys ──────────────────────────────────────────────────────────
load_env() {
    if [ -z "${OPENROUTER_API_KEY:-}" ]; then
        if [ -f "$HOME/.kinox/env" ]; then
            export OPENROUTER_API_KEY="$(grep -oP 'OPENROUTER_API_KEY=\K.*' "$HOME/.kinox/env" 2>/dev/null | head -1 || echo '')"
        fi
    fi
    export NOVELTY_LLM_CLASSIFY_FAST_CMD="python3 $LLM_BRIDGE --mode classify-fast"
    export NOVELTY_LLM_REASONING_LARGE_CMD="python3 $LLM_BRIDGE --mode reasoning-large"
}

# (legacy shell slugify removed — create_run() uses the backend-parity
# python one-liner above; single rule lives in backend/slugify.py)

# ── Create run directory ────────────────────────────────────────────────────
# Run-id prefix uses the same rule as the backend (backend/slugify.py):
# NFKD-normalize, lowercase, non-[a-z0-9] -> '-', 60-char cap,
# "research-run" fallback when nothing survives (unicode/empty-safe).
create_run() {
    local query="$1"
    local slug
    slug="$(python3 -c 'import re,sys,unicodedata; t=unicodedata.normalize("NFKD",sys.argv[1]).encode("ascii","ignore").decode(); s=re.sub(r"[^a-z0-9]+","-",t.lower()).strip("-"); print((s[:60] if s else "research-run"))' "$query")"
    local run_id="${slug}-$(date +%s | md5sum | head -c 8)"
    local run_dir="$RUN_BASE/$run_id"
    mkdir -p "$run_dir/phase0"
    mkdir -p "$run_dir/phase1"
    mkdir -p "$run_dir/phase2_select"
    mkdir -p "$run_dir/phase2_generate"
    mkdir -p "$run_dir/phase2_coherence"
    mkdir -p "$run_dir/phase3_collision"
    mkdir -p "$run_dir/phase3_critique"
    mkdir -p "$run_dir/phase3_revise"
    mkdir -p "$run_dir/phase4"
    echo "$query" > "$run_dir/query.txt"
    echo "$run_id"
}

# ── Phase 0: Literature search ──────────────────────────────────────────────
run_phase0() {
    local query="$1"
    local run_id="$2"
    local run_dir="$RUN_BASE/$run_id"
    local phase_dir="$run_dir/phase0"

    info "Phase 0: Literature search"
    info "  Query: $query"
    info "  Output: $phase_dir"

    activate_venv
    load_env

    python3 "$SKILL_DIR/scripts/run.py" phase0 \
        --query "$query" \
        --out "$phase_dir/" 2>&1

    if [ -f "$phase_dir/lit_results.json" ]; then
        local n_papers=$(python3 -c "import json; print(len(json.load(open('$phase_dir/lit_results.json'))))" 2>/dev/null || echo "?")
        ok "Phase 0 complete — $n_papers papers"
    else
        warn "Phase 0 may be incomplete (no lit_results.json)"
    fi
    echo ""
}

# ── Phase 0+: Full-text fetch ───────────────────────────────────────────────
run_phase0_fulltext() {
    local run_id="$1"
    local run_dir="$RUN_BASE/$run_id"
    local phase_dir="$run_dir/phase0"

    if [ ! -d "$phase_dir" ]; then
        err "Phase 0 directory not found: $phase_dir"
        err "Run Phase 0 first"
        exit 1
    fi

    info "Phase 0+: Full-text fetch"
    activate_venv
    load_env

    python3 "$SKILL_DIR/scripts/run.py" phase0_fulltext \
        --out "$phase_dir/" 2>&1

    if [ -f "$phase_dir/fulltext_cache.json" ]; then
        local size=$(wc -c < "$phase_dir/fulltext_cache.json")
        # Count successful fetches
        local ok_count=$(python3 -c "
import json
with open('$phase_dir/fulltext_cache.json') as f:
    cache = json.load(f)
ok = [k for k,v in cache.items() if v.get('source_used') != 'failed']
print(len(ok))
" 2>/dev/null || echo "?")
        ok "Full-text fetch complete — $ok_count papers fetched ($size bytes)"
    else
        warn "No fulltext_cache.json produced"
    fi
    echo ""
}

# ── Phase 3.1: Collision check ──────────────────────────────────────────────
# Candidate preference is strictly scoped to the CURRENT run dir (no
# cross-run newest-file fallback): revise output > refined > generate output.
run_collision() {
    local run_id="$1"
    local run_dir="$RUN_BASE/$run_id"
    local candidate=""

    # Preference order within THIS run only (fixed priority, not mtime):
    # final (revise path) > refined (coherence) > raw generate output.
    for f in "$run_dir/phase3_revise/final_candidate.json" \
             "$run_dir/phase2_coherence/refined_candidate.json" \
             "$run_dir/phase2_generate/phase2_generate_output.json"; do
        if [ -f "$f" ]; then
            candidate="$f"
            break
        fi
    done

    if [ -z "$candidate" ]; then
        err "No candidate JSON found — run Phase 2.2 first"
        exit 1
    fi

    info "Phase 3.1: Collision check"
    info "  Candidate: $candidate"
    info "  Output: $run_dir/phase3_collision/"

    activate_venv
    load_env

    python3 "$SKILL_DIR/scripts/run.py" phase3_collision \
        --idea-json "$candidate" \
        --out "$run_dir/phase3_collision/" 2>&1

    if [ -f "$run_dir/phase3_collision/collision_hits.json" ]; then
        local n_hits=$(python3 -c "import json; print(len(json.load(open('$run_dir/phase3_collision/collision_hits.json'))))" 2>/dev/null || echo "?")
        ok "Collision check complete — $n_hits hits"
    fi
    echo ""
}

# ── Phase 4 skeleton ────────────────────────────────────────────────────────
run_skeleton() {
    local run_id="$1"
    local run_dir="$RUN_BASE/$run_id"

    # Find the best available candidate (advance or revise path)
    local candidate="$run_dir/phase3_revise/final_candidate.json"
    if [ ! -f "$candidate" ]; then
        candidate="$run_dir/phase2_coherence/refined_candidate.json"
    fi
    local phase1="$run_dir/phase1/phase1_output.json"
    local select="$run_dir/phase2_select/phase2_select_output.json"
    local critique="$run_dir/phase3_critique/phase3_critique_output.json"
    local revise="$run_dir/phase3_revise/phase3_revise_output.json"

    # Create minimal revise output if it doesn't exist (advance path)
    if [ ! -f "$revise" ]; then
        mkdir -p "$run_dir/phase3_revise"
        echo '{"applied_revisions": []}' > "$revise"
    fi
    local phase0="$run_dir/phase0/"
    local collision="$run_dir/phase3_collision/collision_hits.json"

    # Check prerequisites
    for f in "$candidate" "$phase1" "$select" "$critique" "$revise" "$collision"; do
        if [ ! -f "$f" ]; then
            err "Missing prerequisite: $f"
            exit 1
        fi
    done

    info "Phase 4 skeleton"
    activate_venv
    load_env

    python3 "$SKILL_DIR/scripts/run.py" phase4_skeleton \
        --candidate "$candidate" \
        --phase1 "$phase1" \
        --phase2-select "$select" \
        --phase3-critique "$critique" \
        --phase3-revise "$revise" \
        --phase0-dir "$phase0" \
        --collision "$collision" \
        --out "$run_dir/phase4/" 2>&1

    if [ -f "$run_dir/phase4/phase4_skeleton.json" ]; then
        local n_todos=$(grep -c '<TODO\[' "$run_dir/phase4/phase4_skeleton.json" 2>/dev/null || echo "0")
        ok "Skeleton complete — $n_todos TODO placeholders"
    fi
    echo ""
}

# ── Phase 4 assembly ────────────────────────────────────────────────────────
run_assemble() {
    local run_id="$1"
    local fill_map="$2"
    local run_dir="$RUN_BASE/$run_id"

    if [ ! -f "$run_dir/phase4/phase4_skeleton.json" ]; then
        err "Skeleton not found — run skeleton first"
        exit 1
    fi
    if [ ! -f "$fill_map" ]; then
        err "Fill map not found: $fill_map"
        exit 1
    fi

    info "Phase 4 assembly"
    activate_venv
    load_env

    python3 "$SKILL_DIR/scripts/run.py" phase4_assemble \
        --skeleton "$run_dir/phase4/phase4_skeleton.json" \
        --fill-map "$fill_map" \
        --out "$run_dir/phase4/" 2>&1

    if [ -f "$run_dir/phase4/phase4_expansion.json" ]; then
        ok "Assembly complete"
    fi
    echo ""
}

# ── Phase 4 render ──────────────────────────────────────────────────────────
run_render() {
    local run_id="$1"
    local run_dir="$RUN_BASE/$run_id"

    if [ ! -f "$run_dir/phase4/phase4_expansion.json" ]; then
        err "Expansion not found — run assemble first"
        exit 1
    fi

    info "Phase 4 render"
    activate_venv
    load_env

    python3 "$SKILL_DIR/scripts/run.py" phase4_render \
        --expansion "$run_dir/phase4/phase4_expansion.json" \
        --out "$run_dir/phase4/" 2>&1

    for f in idea.std.en.md idea.std.zh.md idea.detail.en.md; do
        if [ -f "$run_dir/phase4/$f" ]; then
            ok "Rendered: $f ($(wc -c < "$run_dir/phase4/$f") bytes)"
        fi
    done
    echo ""
}

# ── Status: Show run status ─────────────────────────────────────────────────
show_status() {
    local run_id="$1"
    local run_dir="$RUN_BASE/$run_id"

    if [ ! -d "$run_dir" ]; then
        err "Run not found: $run_id"
        exit 1
    fi

    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  Run: $run_id"
    echo "  Query: $(cat "$run_dir/query.txt" 2>/dev/null || echo '?')"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""

    local phases=(
        "phase0:Phase 0 — Literature search:lit_results.json"
        "phase0:Phase 0+ — Full-text fetch:fulltext_cache.json"
        "phase1:Phase 1 — Bottleneck ID:phase1_output.json"
        "phase2_select:Phase 2.1 — Gap selection:phase2_select_output.json"
        "phase2_generate:Phase 2.2 — Generation:phase2_generate_output.json"
        "phase2_coherence:Phase 2.3 — Coherence trace:phase2_coherence_output.json"
        "phase2_coherence:  ↳ Refined candidate:refined_candidate.json"
        "phase3_collision:Phase 3.1 — Collision check:collision_hits.json"
        "phase3_critique:Phase 3.2 — Audit:phase3_critique_output.json"
        "phase3_revise:Phase 3.3 — Revise:phase3_revise_output.json"
        "phase3_revise:  ↳ Final candidate:final_candidate.json"
        "phase4:Phase 4 — Skeleton:phase4_skeleton.json"
        "phase4:Phase 4 — Fill map:fill_map.json"
        "phase4:Phase 4 — Expansion:phase4_expansion.json"
        "phase4:Phase 4 — Idea card:idea.std.en.md"
    )

    for entry in "${phases[@]}"; do
        local dir="${entry%%:*}"
        local rest="${entry#*:}"
        local label="${rest%%:*}"
        local file="${rest##*:}"
        local path="$run_dir/$dir/$file"

        if [ -f "$path" ]; then
            local size=$(wc -c < "$path" 2>/dev/null || echo "0")
            ok "$label ($(numfmt --to=iec $size 2>/dev/null || echo "${size}B"))"
        else
            warn "$label — not yet"
        fi
    done

    echo ""
    echo "═══ LLM phases (run manually) ═══"
    echo "  Phase 1   → run_pipeline.sh phase-llm $run_id 1"
    echo "  Phase 2.1 → run_pipeline.sh phase-llm $run_id 2.1"
    echo "  Phase 2.2 → run_pipeline.sh phase-llm $run_id 2.2"
    echo "  Phase 2.3 → run_pipeline.sh phase-llm $run_id 2.3"
    echo "  Phase 3.2 → run_pipeline.sh phase-llm $run_id 3.2"
    echo "  Phase 3.3 → run_pipeline.sh phase-llm $run_id 3.3"
    echo "  Phase 4.fill → write fill_map.json manually"
    echo ""
}

# ── List all runs ───────────────────────────────────────────────────────────
list_runs() {
    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  IdeaFlow — Pipeline runs"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""

    if [ ! -d "$RUN_BASE" ] || [ -z "$(ls -A "$RUN_BASE")" ]; then
        echo "  No runs yet."
        echo ""
        exit 0
    fi

    for run_dir in "$RUN_BASE"/*/; do
        local run_id="$(basename "$run_dir")"
        local query="$(cat "$run_dir/query.txt" 2>/dev/null || echo '?')"
        local phase0_count="?"
        if [ -f "$run_dir/phase0/lit_results.json" ]; then
            phase0_count=$(python3 -c "import json; print(len(json.load(open('$run_dir/phase0/lit_results.json'))))" 2>/dev/null || echo "?")
        fi
        local has_card=""
        [ -f "$run_dir/phase4/idea.std.en.md" ] && has_card=" ✅ card"
        echo "  $run_id"
        echo "    Query: ${query:0:80}"
        echo "    Phase 0: ${phase0_count}papers | Phase 4: ${has_card:-not yet}"
        echo ""
    done
}

# ── Print LLM phase instructions ────────────────────────────────────────────
print_llm_instructions() {
    local run_id="$1"
    local phase="$2"
    local run_dir="$RUN_BASE/$run_id"

    case "$phase" in
        1)
            echo "Phase 1 — Bottleneck identification"
            echo "  Prompt: $SKILL_DIR/references/system-prompts/bottleneck_identify.txt"
            echo "  Inputs:"
            echo "    - $run_dir/phase0/lit_table.md"
            echo "    - $run_dir/phase0/fulltext_cache.json"
            echo "    - $run_dir/phase0/lit_results.json"
            echo "  Output: $run_dir/phase1/phase1_output.json"
            ;;
        2.1)
            echo "Phase 2.1 — Gap × Pattern selection"
            echo "  Prompt: $SKILL_DIR/references/system-prompts/ideate_select.txt"
            echo "  Inputs:"
            echo "    - $run_dir/phase1/phase1_output.json"
            echo "    - $SKILL_DIR/references/ideation-patterns/overview.md"
            echo "  Output: $run_dir/phase2_select/phase2_select_output.json"
            ;;
        2.2)
            echo "Phase 2.2 — Candidate generation"
            echo "  Prompt: $SKILL_DIR/references/system-prompts/ideate_generate.txt"
            echo "  Inputs:"
            echo "    - $run_dir/phase2_select/phase2_select_output.json"
            echo "    - $SKILL_DIR/references/ideation-sub-patterns/overview.md"
            echo "  Output: $run_dir/phase2_generate/phase2_generate_output.json"
            ;;
        2.3)
            echo "Phase 2.3 — Coherence trace"
            echo "  Prompt: $SKILL_DIR/references/system-prompts/coherence_trace.txt"
            echo "  Inputs:"
            echo "    - $run_dir/phase2_generate/phase2_generate_output.json"
            echo "    - $run_dir/phase2_select/phase2_select_output.json"
            echo "  Output: $run_dir/phase2_coherence/phase2_coherence_output.json"
            echo "  Then run: python3 $SKILL_DIR/scripts/run.py phase3_merge_revisions \\"
            echo "    --phase2 $run_dir/phase2_generate/phase2_generate_output.json \\"
            echo "    --revisions $run_dir/phase2_coherence/phase2_coherence_output.json \\"
            echo "    --out $run_dir/phase2_coherence/ --out-name refined_candidate.json"
            ;;
        3.2)
            echo "Phase 3.2 — Audit"
            echo "  Prompt: $SKILL_DIR/references/system-prompts/critique.txt"
            echo "  Inputs:"
            echo "    - $run_dir/phase2_coherence/refined_candidate.json"
            echo "    - $run_dir/phase2_select/phase2_select_output.json"
            echo "    - $run_dir/phase0/lit_table.md"
            echo "    - $run_dir/phase3_collision/collision_hits.json"
            echo "    - $SKILL_DIR/references/anti-patterns.md"
            echo "  Output: $run_dir/phase3_critique/phase3_critique_output.json"
            ;;
        3.3)
            echo "Phase 3.3 — Revise"
            echo "  Prompt: $SKILL_DIR/references/system-prompts/revise.txt"
            echo "  Inputs:"
            echo "    - $run_dir/phase2_coherence/refined_candidate.json"
            echo "    - $run_dir/phase2_select/phase2_select_output.json"
            echo "    - $run_dir/phase3_critique/phase3_critique_output.json"
            echo "  Output: $run_dir/phase3_revise/phase3_revise_output.json"
            echo "  Then run: python3 $SKILL_DIR/scripts/run.py phase3_merge_revisions \\"
            echo "    --phase2 $run_dir/phase2_coherence/refined_candidate.json \\"
            echo "    --revisions $run_dir/phase3_revise/phase3_revise_output.json \\"
            echo "    --critique $run_dir/phase3_critique/phase3_critique_output.json \\"
            echo "    --out $run_dir/phase3_revise/ --out-name final_candidate.json"
            ;;
        *)
            err "Unknown phase: $phase"
            echo "Valid phases: 1, 2.1, 2.2, 2.3, 3.2, 3.3"
            exit 1
            ;;
    esac
}

# ── Clean run ───────────────────────────────────────────────────────────────
clean_run() {
    local run_id="$1"
    local run_dir="$RUN_BASE/$run_id"
    if [ -d "$run_dir" ]; then
        rm -rf "$run_dir"
        ok "Removed: $run_id"
    else
        err "Not found: $run_id"
    fi
}

# ── Main ────────────────────────────────────────────────────────────────────
case "${1:-help}" in
    start)
        QUERY="${2:-}"
        if [ -z "$QUERY" ]; then
            echo "Usage: $0 start \"your research query\""
            exit 1
        fi
        RUN_ID=$(create_run "$QUERY")
        echo "Run ID: $RUN_ID"
        echo ""
        run_phase0 "$QUERY" "$RUN_ID"
        run_phase0_fulltext "$RUN_ID"
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "  Pipeline started. Next: Phase 1 (LLM phase)"
        echo "  Run: $0 phase-llm $RUN_ID 1"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "$RUN_ID"
        ;;

    phase0)
        QUERY="${2:-}"
        if [ -z "$QUERY" ]; then
            echo "Usage: $0 phase0 \"query\""
            exit 1
        fi
        RUN_ID=$(create_run "$QUERY")
        echo "Run ID: $RUN_ID"
        run_phase0 "$QUERY" "$RUN_ID"
        echo "$RUN_ID"
        ;;

    phase0-fulltext)
        RUN_ID="${2:-}"
        [ -z "$RUN_ID" ] && { echo "Usage: $0 phase0-fulltext <run-id>"; exit 1; }
        run_phase0_fulltext "$RUN_ID"
        ;;

    collision)
        RUN_ID="${2:-}"
        [ -z "$RUN_ID" ] && { echo "Usage: $0 collision <run-id>"; exit 1; }
        run_collision "$RUN_ID"
        ;;

    skeleton)
        RUN_ID="${2:-}"
        [ -z "$RUN_ID" ] && { echo "Usage: $0 skeleton <run-id>"; exit 1; }
        run_skeleton "$RUN_ID"
        ;;

    assemble)
        RUN_ID="${2:-}"
        FILL_MAP="${3:-}"
        [ -z "$RUN_ID" ] && { echo "Usage: $0 assemble <run-id> <fill-map.json>"; exit 1; }
        [ -z "$FILL_MAP" ] && { echo "Usage: $0 assemble <run-id> <fill-map.json>"; exit 1; }
        run_assemble "$RUN_ID" "$FILL_MAP"
        ;;

    render)
        RUN_ID="${2:-}"
        [ -z "$RUN_ID" ] && { echo "Usage: $0 render <run-id>"; exit 1; }
        run_render "$RUN_ID"
        ;;

    status)
        RUN_ID="${2:-}"
        [ -z "$RUN_ID" ] && { echo "Usage: $0 status <run-id>"; exit 1; }
        show_status "$RUN_ID"
        ;;

    list)
        list_runs
        ;;

    phase-llm)
        RUN_ID="${2:-}"
        PHASE="${3:-}"
        [ -z "$RUN_ID" ] && { echo "Usage: $0 phase-llm <run-id> <phase>"; exit 1; }
        [ -z "$PHASE" ] && { echo "Usage: $0 phase-llm <run-id> <phase>"; exit 1; }
        print_llm_instructions "$RUN_ID" "$PHASE"
        ;;

    auto-llm)
        RUN_ID="${2:-}"
        PHASE="${3:-}"
        [ -z "$RUN_ID" ] && { echo "Usage: $0 auto-llm <run-id> <phase>"; exit 1; }
        [ -z "$PHASE" ] && { echo "Usage: $0 auto-llm <run-id> <phase>"; exit 1; }
        RUN_DIR="$RUN_BASE/$RUN_ID"
        if [ ! -d "$RUN_DIR" ]; then
            err "Run not found: $RUN_ID"
            exit 1
        fi
        activate_venv
        load_env
        python3 "$SCRIPT_DIR/run_llm_phase.py" "$RUN_DIR" "$PHASE"
        ;;

    watch)
        # Single-instance guard (portable: flock on Linux, mkdir-lock on macOS).
        # A second `watch` exits instead of double-firing LLM phases.
        LOCK_DIR="$RUN_BASE/.watch.lockdir"
        if command -v flock >/dev/null 2>&1; then
            exec 9>"$RUN_BASE/.watch.lock"
            if ! flock -n 9; then
                err "Another 'watch' is already running — exiting."
                exit 1
            fi
        else
            if ! mkdir "$LOCK_DIR" 2>/dev/null; then
                err "Another 'watch' is already running — exiting."
                exit 1
            fi
            trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
        fi
        info "Watching for pending pipeline phases..."
        activate_venv
        load_env
        for run_dir in "$RUN_BASE"/*/; do
            # Skip the lock file itself and non-run entries.
            [ -f "$run_dir/query.txt" ] || continue
            rid="$(basename "$run_dir")"
            query="$(cat "$run_dir/query.txt" 2>/dev/null || echo '?')"

            # ── LLM phases (via auto-llm) ──
            if [ -f "$run_dir/phase0/fulltext_cache.json" ] && [ ! -f "$run_dir/phase1/phase1_output.json" ]; then
                info "Auto: Phase 1 for $rid"
                python3 "$SCRIPT_DIR/run_llm_phase.py" "$run_dir" "1"
            elif [ -f "$run_dir/phase1/phase1_output.json" ] && [ ! -f "$run_dir/phase2_select/phase2_select_output.json" ]; then
                info "Auto: Phase 2.1 for $rid"
                python3 "$SCRIPT_DIR/run_llm_phase.py" "$run_dir" "2.1"
            elif [ -f "$run_dir/phase2_select/phase2_select_output.json" ] && [ ! -f "$run_dir/phase2_generate/phase2_generate_output.json" ]; then
                info "Auto: Phase 2.2 for $rid"
                python3 "$SCRIPT_DIR/run_llm_phase.py" "$run_dir" "2.2"
            elif [ -f "$run_dir/phase2_generate/phase2_generate_output.json" ] && [ ! -f "$run_dir/phase2_coherence/phase2_coherence_output.json" ]; then
                info "Auto: Phase 2.3 for $rid"
                python3 "$SCRIPT_DIR/run_llm_phase.py" "$run_dir" "2.3"

            # ── Automated: collision check ──
            elif [ -f "$run_dir/phase2_coherence/refined_candidate.json" ] && [ ! -f "$run_dir/phase3_collision/collision_hits.json" ]; then
                info "Auto: Phase 3.1 (collision) for $rid"
                run_collision "$rid"

            # ── LLM: audit ──
            elif [ -f "$run_dir/phase3_collision/collision_hits.json" ] && [ ! -f "$run_dir/phase3_critique/phase3_critique_output.json" ]; then
                info "Auto: Phase 3.2 for $rid"
                python3 "$SCRIPT_DIR/run_llm_phase.py" "$run_dir" "3.2"

            # ── LLM: revise (only if verdict=revise) ──
            elif [ -f "$run_dir/phase3_critique/phase3_critique_output.json" ]; then
                verdict=$(python3 -c "import json; print(json.load(open('$run_dir/phase3_critique/phase3_critique_output.json')).get('verdict',''))" 2>/dev/null)
                if [ "$verdict" = "revise" ] && [ ! -f "$run_dir/phase3_revise/phase3_revise_output.json" ]; then
                    info "Auto: Phase 3.3 for $rid (verdict=revise)"
                    python3 "$SCRIPT_DIR/run_llm_phase.py" "$run_dir" "3.3"

                # ── Run merger after revise → produce final_candidate.json ──
                elif [ "$verdict" = "revise" ] && [ -f "$run_dir/phase3_revise/phase3_revise_output.json" ] && [ ! -f "$run_dir/phase3_revise/final_candidate.json" ]; then
                    info "Auto: Running merger for Phase 3.3 (revise path)..."
                    python3 "$SKILL_DIR/scripts/run.py" phase3_merge_revisions \
                        --phase2 "$run_dir/phase2_coherence/refined_candidate.json" \
                        --revisions "$run_dir/phase3_revise/phase3_revise_output.json" \
                        --critique "$run_dir/phase3_critique/phase3_critique_output.json" \
                        --out "$run_dir/phase3_revise/" \
                        --out-name final_candidate.json 2>&1 || warn "Merger failed (possibly malformed revisions)"

                # ── Automated: Phase 4 skeleton ──
                elif [ "$verdict" = "advance" ] || [ -f "$run_dir/phase3_revise/final_candidate.json" ]; then
                    # advance path: candidate is the refined_candidate
                    candidate="$run_dir/phase2_coherence/refined_candidate.json"
                    if [ -f "$run_dir/phase3_revise/final_candidate.json" ]; then
                        candidate="$run_dir/phase3_revise/final_candidate.json"
                    fi
                    if [ ! -f "$run_dir/phase4/phase4_skeleton.json" ]; then
                        info "Auto: Phase 4 skeleton for $rid"
                        run_skeleton "$rid"
                    fi
                fi
            fi

            # ── Automated: Phase 4 fill (LLM), assemble, render ──
            if [ -f "$run_dir/phase4/phase4_skeleton.json" ] && [ ! -f "$run_dir/phase4/fill_map.json" ]; then
                info "Auto: Phase 4.fill for $rid"
                python3 "$SCRIPT_DIR/run_llm_phase.py" "$run_dir" "4.fill"
            elif [ -f "$run_dir/phase4/fill_map.json" ] && [ ! -f "$run_dir/phase4/phase4_expansion.json" ]; then
                info "Auto: Phase 4 assemble for $rid"
                run_assemble "$rid" "$run_dir/phase4/fill_map.json"
            elif [ -f "$run_dir/phase4/phase4_expansion.json" ] && [ ! -f "$run_dir/phase4/idea.std.en.md" ]; then
                info "Auto: Phase 4 render for $rid"
                run_render "$rid"
                if [ -f "$run_dir/phase4/idea.std.en.md" ]; then
                    ok "Pipeline complete for $rid — idea card ready!"
                fi
            fi
        done
        ok "Watch complete"
        ;;

    help|--help|-h)
        echo "IdeaFlow — Pipeline orchestrator"
        echo ""
        echo "Usage:"
        echo "  $0 start \"<query>\"              # Start full pipeline (Phase 0 + 0+)"
        echo "  $0 phase0 \"<query>\"             # Phase 0: literature search (creates new run)"
        echo "  $0 phase0-fulltext <run-id>      # Phase 0+: full-text fetch"
        echo "  $0 collision <run-id>            # Phase 3.1: collision check"
        echo "  $0 skeleton <run-id>             # Phase 4: skeleton"
        echo "  $0 assemble <run-id> <fill-map>  # Phase 4: assembly"
        echo "  $0 render <run-id>               # Phase 4: render"
        echo "  $0 status <run-id>               # Show run status"
        echo "  $0 phase-llm <run-id> <phase>    # Show LLM phase instructions"
        echo "  $0 auto-llm <run-id> <phase>   # Run LLM phase autonomously via OpenRouter"
        echo "  $0 watch                       # Auto-run all pending phases"
        echo "  $0 list                        # List all runs"
        echo "  $0 clean <run-id>              # Remove a run"
        echo "  $0 help                          # This help"
        echo ""
        echo "LLM phases (run by agent):"
        echo "  Phase 1    → bottleneck identification"
        echo "  Phase 2.1  → gap × pattern selection"
        echo "  Phase 2.2  → candidate generation"
        echo "  Phase 2.3  → coherence trace (dry-run)"
        echo "  Phase 3.2  → audit (5 checks)"
        echo "  Phase 3.3  → revise (patch)"
        echo "  Phase 4.fill → prose expansion"
        echo ""
        echo "See: $0 phase-llm <run-id> <phase>"
        ;;

    *)
        echo "Usage: $0 start|phase0|phase0-fulltext|collision|skeleton|assemble|render|status|phase-llm|list|clean|help"
        exit 1
        ;;
esac