"""Pipeline orchestration endpoints — run management, phase execution, persistence & resume."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from backend.database import (
    PIPELINE_PHASE_DEFS,
    cancel_pipeline_run,
    check_run_limit,
    delete_pipeline_run,
    get_user_by_id,
    increment_run_count,
    init_pipeline_run,
    update_pipeline_phase,
    get_pipeline_run_details,
    list_all_pipeline_runs,
    sync_run_to_disk_and_db,
)
from backend.engine_guard import MISSING_ENGINE_MSG, engine_present

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_DIR = PROJECT_ROOT / "ideaspark_run"
PIPELINE_SCRIPT = PROJECT_ROOT / "run_pipeline.sh"
LLM_RUNNER = PROJECT_ROOT / "run_llm_phase.py"
SKILL_DIR = PROJECT_ROOT / "researchstudio" / "ResearchStudio-Idea" / "skills" / "idea_spark"

RUN_DIR.mkdir(exist_ok=True)

# Legacy self-host fallback path for the API key file. The OPENROUTER_API_KEY
# env var takes precedence; this file is only consulted when it is unset.
LEGACY_KEY_FILE = Path("/home/enigma/.kinox/env")

# Active pipeline background threads
_active_workers: dict[str, threading.Thread] = {}
_worker_lock = threading.Lock()

# Per-run cooperative cancel flags: set by the cancel endpoint, checked by
# the worker loop between phases for a clean stop.
_cancel_flags: dict[str, threading.Event] = {}


def _worker_alive(run_id: str) -> bool:
    """True if a live worker thread is already executing this run."""
    t = _active_workers.get(run_id)
    return t is not None and t.is_alive()


def _try_spawn_worker(run_id: str) -> bool:
    """Spawn the pipeline worker loop unless one is already in flight.

    Returns True when this call started a new thread, False when a live
    worker already owns the run (no duplicate spawned). Stale (dead)
    thread entries are reaped so a fresh worker can start.
    A previous cancel flag is cleared so a resume starts clean.
    """
    with _worker_lock:
        existing = _active_workers.get(run_id)
        if existing is not None:
            if existing.is_alive():
                return False
            _active_workers.pop(run_id, None)
        flag = _cancel_flags.get(run_id)
        if flag is not None:
            flag.clear()
        t = threading.Thread(target=_pipeline_worker_loop, args=(run_id,), daemon=True)
        _active_workers[run_id] = t
        t.start()
        return True


def _request_cancel(run_id: str) -> None:
    """Set the cooperative cancel flag for a run (created if absent)."""
    with _worker_lock:
        flag = _cancel_flags.get(run_id)
        if flag is None:
            flag = threading.Event()
            _cancel_flags[run_id] = flag
        flag.set()


def _is_cancelled(run_id: str) -> bool:
    flag = _cancel_flags.get(run_id)
    return flag is not None and flag.is_set()

# ── Input caps / guards ───────────────────────────────────────────────────
MAX_QUERY_LEN = 2000
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_FILENAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")
_PHASE_DIR_RE = re.compile(r"^[A-Za-z0-9_]+$")

# Unauthenticated per-IP rate limit for worker-spawning starts: 10/hour.
_ANON_START_WINDOW_SEC = 3600
_ANON_START_MAX = 10
_anon_starts: dict[str, list[float]] = {}
_anon_lock = threading.Lock()


def _validate_run_id(run_id: str) -> str:
    if not _RUN_ID_RE.match(run_id or ""):
        raise HTTPException(status_code=400, detail="Invalid run_id")
    return run_id


def _validate_phase_dir(value: str, field: str = "phase_dir") -> str:
    if not _PHASE_DIR_RE.match(value or "") or ".." in value:
        raise HTTPException(status_code=400, detail=f"Invalid {field}")
    return value


def _validate_filename(filename: str) -> str:
    if (not _FILENAME_RE.match(filename or "") or ".." in filename
            or "/" in filename or "\\" in filename
            or filename.startswith(".") or len(filename) > 255):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return filename


def _sanitize_query(query: str) -> str:
    # Strip control chars (keep newline/tab), collapse nothing else.
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", query).strip()
    return cleaned


async def _optional_user(request: Request) -> dict | None:
    """Best-effort auth: returns user dict or None (lazy import avoids cycles).

    Reads the session cookie directly; no Depends() default (calling the
    function directly would hand us a Depends object instead of credentials).
    """
    from backend.routers.auth import decode_jwt  # lazy: auth never imports pipeline
    token = request.cookies.get("session")
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        return None
    try:
        payload = decode_jwt(token)
    except Exception:
        return None
    if not payload or not payload.get("sub"):
        return None
    try:
        return get_user_by_id(payload["sub"])
    except Exception:
        return None


def _check_anon_rate_limit(request: Request) -> None:
    ip = (request.client.host if request.client else "unknown") or "unknown"
    now = time.time()
    with _anon_lock:
        hits = [t for t in _anon_starts.get(ip, []) if now - t < _ANON_START_WINDOW_SEC]
        if len(hits) >= _ANON_START_MAX:
            raise HTTPException(status_code=429, detail="Rate limit exceeded, try again later")
        hits.append(now)
        _anon_starts[ip] = hits


# ── Pydantic Models ─────────────────────────────────────────────────────────

class PipelineStartRequest(BaseModel):
    query: str = Field(..., description="Research query to run the pipeline on")


class PipelineStartResponse(BaseModel):
    run_id: str
    status: str
    query: str
    phases: dict = Field(default_factory=dict)


class RunStatusResponse(BaseModel):
    run_id: str
    query: str = ""
    status: str = "pending"
    current_phase: str = "phase0"
    total_duration_sec: float = 0.0
    phases: dict = Field(default_factory=dict)
    has_idea_card: bool = False
    paper_count: int = 0


class ArtifactInfo(BaseModel):
    name: str
    rel_path: str
    size: int
    mtime: float
    ext: str
    url: str


class ArtifactListResponse(BaseModel):
    run_id: str
    artifacts: list[ArtifactInfo] = Field(default_factory=list)


# ── Helper Functions ────────────────────────────────────────────────────────
# (slug helper lives in backend.slugify — imported below as _slugify)
from backend.slugify import slugify as _slugify


def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    llm_bridge = str(PROJECT_ROOT / "llm_bridge.py")
    env["NOVELTY_LLM_CLASSIFY_FAST_CMD"] = f"python3 {llm_bridge} --mode classify-fast"
    env["NOVELTY_LLM_REASONING_LARGE_CMD"] = f"python3 {llm_bridge} --mode reasoning-large"

    if "OPENROUTER_API_KEY" not in env or not env["OPENROUTER_API_KEY"]:
        kinox = LEGACY_KEY_FILE
        if kinox.exists():
            for line in kinox.read_text().splitlines():
                if line.startswith("OPENROUTER_API_KEY="):
                    env["OPENROUTER_API_KEY"] = line.split("=", 1)[1].strip()
                    break
    return env


def _get_phase_artifacts(run_dir: Path, phase_key: str) -> list[dict[str, Any]]:
    phase_def = next((p for p in PIPELINE_PHASE_DEFS if p["key"] == phase_key), None)
    if not phase_def:
        return []
    dir_path = run_dir / phase_def["dir"]
    artifacts = []
    if dir_path.exists() and dir_path.is_dir():
        for f in sorted(dir_path.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                artifacts.append({
                    "name": f.name,
                    "rel_path": f"{phase_def['dir']}/{f.name}",
                    "size": f.stat().st_size,
                    "mtime": f.stat().st_mtime,
                    "ext": f.suffix.lstrip("."),
                })
    return artifacts


# ── Background Pipeline Orchestrator ────────────────────────────────────────

def _execute_single_phase(run_id: str, phase_key: str) -> bool:
    """Execute a single phase for run_id and persist status/artifacts."""
    run_dir = RUN_DIR / run_id
    if not run_dir.exists():
        return False

    phase_def = next((p for p in PIPELINE_PHASE_DEFS if p["key"] == phase_key), None)
    if not phase_def:
        return False

    start_time = time.time()
    started_iso = datetime.now(timezone.utc).isoformat()

    update_pipeline_phase(
        run_id=run_id,
        phase_key=phase_key,
        status="running",
        started_at=started_iso,
    )

    env = _build_env()
    success = False
    error_msg = None

    try:
        if phase_key == "phase0":
            query = (run_dir / "query.txt").read_text(encoding="utf-8").strip() if (run_dir / "query.txt").exists() else run_id
            # NOTE: run_pipeline.sh "phase0 <query>" creates its OWN run dir
            # (create_run) and writes lit_results there — NOT into our run_dir.
            # Call the engine directly so artifacts land in run_dir/phase0/.
            engine = SKILL_DIR / "scripts" / "run.py"
            cmd = ["python3", str(engine), "phase0", "--query", query, "--out", str(run_dir / "phase0") + "/"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=str(PROJECT_ROOT), env=env)
            success = (proc.returncode == 0) and (run_dir / "phase0" / "lit_results.json").exists()
            if not success:
                error_msg = (proc.stderr[-500:] if proc.stderr else "") or "Phase 0 failed to generate lit_results.json"

        elif phase_key == "phase0_fulltext":
            cmd = [str(PIPELINE_SCRIPT), "phase0-fulltext", run_id]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=str(PROJECT_ROOT), env=env)
            success = (proc.returncode == 0)
            if not success:
                error_msg = proc.stderr[-500:] if proc.stderr else "Full-text fetch failed"

        elif phase_key in ("phase1", "phase2_select", "phase2_generate", "phase2_coherence", "phase3_critique", "phase3_revise", "phase4_fill"):
            llm_step_map = {
                "phase1": "1",
                "phase2_select": "2.1",
                "phase2_generate": "2.2",
                "phase2_coherence": "2.3",
                "phase3_critique": "3.2",
                "phase3_revise": "3.3",
                "phase4_fill": "4.fill",
            }
            step_arg = llm_step_map[phase_key]

            # Special case for phase3_revise: check if critique verdict was advance
            critique_file = run_dir / "phase3_critique" / "phase3_critique_output.json"
            if phase_key == "phase3_revise" and critique_file.exists():
                try:
                    cdata = json.loads(critique_file.read_text())
                    if cdata.get("verdict") == "advance":
                        # No revision needed, copy refined_candidate to final_candidate
                        refined = run_dir / "phase2_coherence" / "refined_candidate.json"
                        final_c = run_dir / "phase3_revise" / "final_candidate.json"
                        (run_dir / "phase3_revise").mkdir(parents=True, exist_ok=True)
                        if refined.exists():
                            final_c.write_text(refined.read_text())
                        (run_dir / "phase3_revise" / "phase3_revise_output.json").write_text(json.dumps({"applied_revisions": [], "verdict": "advance"}))
                        success = True
                except Exception as ex:
                    print(f"Revision bypass check note: {ex}")

            if not success:
                cmd = ["python3", str(LLM_RUNNER), str(run_dir), step_arg]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=str(PROJECT_ROOT), env=env)
                target_file = run_dir / phase_def["dir"] / phase_def["file"]
                success = (proc.returncode == 0) and target_file.exists()
                if not success:
                    error_msg = proc.stderr[-500:] if proc.stderr else f"LLM phase {step_arg} failed"

        elif phase_key == "phase3_collision":
            cmd = [str(PIPELINE_SCRIPT), "collision", run_id]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(PROJECT_ROOT), env=env)
            success = (proc.returncode == 0) and (run_dir / "phase3_collision" / "collision_hits.json").exists()
            if not success:
                error_msg = proc.stderr[-500:] if proc.stderr else "Collision check failed"

        elif phase_key == "phase4_skeleton":
            cmd = [str(PIPELINE_SCRIPT), "skeleton", run_id]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(PROJECT_ROOT), env=env)
            success = (proc.returncode == 0) and (run_dir / "phase4" / "phase4_skeleton.json").exists()
            if not success:
                error_msg = proc.stderr[-500:] if proc.stderr else "Skeleton generation failed"

        elif phase_key == "phase4_card":
            fill_map = run_dir / "phase4" / "fill_map.json"
            if fill_map.exists():
                cmd_assemble = [str(PIPELINE_SCRIPT), "assemble", run_id, str(fill_map)]
                subprocess.run(cmd_assemble, capture_output=True, text=True, timeout=300, cwd=str(PROJECT_ROOT), env=env)
            cmd_render = [str(PIPELINE_SCRIPT), "render", run_id]
            proc = subprocess.run(cmd_render, capture_output=True, text=True, timeout=300, cwd=str(PROJECT_ROOT), env=env)
            success = (proc.returncode == 0) and (run_dir / "phase4" / "idea.std.en.md").exists()
            if not success:
                error_msg = proc.stderr[-500:] if proc.stderr else "Rendering idea card failed"

    except subprocess.TimeoutExpired:
        success = False
        error_msg = f"Phase {phase_key} timed out"
    except Exception as e:
        success = False
        error_msg = str(e)

    elapsed = max(0.5, round(time.time() - start_time, 1))
    completed_iso = datetime.now(timezone.utc).isoformat()
    status = "complete" if success else "failed"

    artifacts = _get_phase_artifacts(run_dir, phase_key)

    update_pipeline_phase(
        run_id=run_id,
        phase_key=phase_key,
        status=status,
        started_at=started_iso,
        completed_at=completed_iso,
        elapsed_seconds=elapsed,
        artifacts=artifacts,
        error_message=error_msg,
    )

    # Persist state.json on disk
    try:
        sync_run_to_disk_and_db(run_id, run_dir)
    except Exception as ex:
        print(f"Warning: disk sync error: {ex}")

    return success


def _pipeline_worker_loop(run_id: str):
    """Worker thread that executes pipeline phases sequentially until completion or failure.

    Phase 0 doubles as the novelty gate: after literature search, a fast
    verdict is computed from a scoped triage. If the idea looks scooped
    (max_overlap >= 3 and LLM-backed), the worker pauses before the expensive
    LLM phases and waits for an explicit user override (gate_override flag).
    """
    run_dir = RUN_DIR / run_id
    if not run_dir.exists():
        return

    try:
        for p in PIPELINE_PHASE_DEFS:
            # Cooperative cancel: stop cleanly between phases.
            if _is_cancelled(run_id):
                print(f"Pipeline {run_id} cancelled; stopping worker")
                break
            key = p["key"]
            details = get_pipeline_run_details(run_id)
            if not details:
                break
            # A cancel that landed while idle also stops the loop.
            if details.get("status") == "cancelled" or _is_cancelled(run_id):
                print(f"Pipeline {run_id} cancelled; stopping worker")
                break

            phase_status = details.get("phases", {}).get(key, {}).get("status", "pending")
            if phase_status in ("complete", "completed"):
                continue

            # ── Novelty gate: right after phase0, before expensive LLM phases ──
            if key == "phase0_fulltext":
                gate = _novelty_gate_check(run_id)
                if gate.get("blocked"):
                    print(f"Pipeline {run_id} paused at novelty gate: level {gate.get('level')}/5")
                    update_pipeline_phase(
                        run_id=run_id,
                        phase_key="phase0_fulltext",
                        status="awaiting_gate",
                        error_message=(
                            f"Novelty gate: level {gate.get('level')}/5 — "
                            f"{gate.get('summary', '')} Override to continue."
                        ),
                    )
                    try:
                        sync_run_to_disk_and_db(run_id, run_dir)
                    except Exception:
                        pass
                    break

            # Execute this phase
            ok = _execute_single_phase(run_id, key)
            if not ok:
                print(f"Pipeline {run_id} failed at phase {key}")
                break
            # Re-check gate after a resume: if user overrode, clear the flag.
            if key == "phase0_fulltext":
                _clear_gate_override(run_id)
    finally:
        with _worker_lock:
            _active_workers.pop(run_id, None)


def _read_gate_flag(run_id: str) -> bool:
    """True if the user explicitly overrode the novelty gate for this run."""
    try:
        flag = RUN_DIR / run_id / "gate_override"
        return flag.exists()
    except Exception:
        return False


def _clear_gate_override(run_id: str) -> None:
    try:
        flag = RUN_DIR / run_id / "gate_override"
        if flag.exists():
            flag.unlink()
    except Exception:
        pass


def _novelty_gate_check(run_id: str) -> dict[str, Any]:
    """Fast novelty gate over phase0 literature.

    Scores the top phase0 papers against the query with the LLM triage
    prompt (same semantics as Scoop-Check triage). Returns blocked=True only
    when the verdict is LLM-backed (not a baseline fallback) AND the max
    overlap is >= 3 (levels 1-2: scooped). Levels 3-5 and all fallbacks pass.
    """
    if _read_gate_flag(run_id):
        return {"blocked": False, "reason": "override"}

    run_dir = RUN_DIR / run_id
    query_file = run_dir / "query.txt"
    lit_file = run_dir / "phase0" / "lit_results.json"
    gate_file = run_dir / "phase0" / "novelty_gate.json"
    if not query_file.exists() or not lit_file.exists():
        return {"blocked": False, "reason": "no_literature"}

    try:
        query = query_file.read_text(encoding="utf-8").strip()
        papers = json.loads(lit_file.read_text(encoding="utf-8"))
        if not isinstance(papers, list) or not papers:
            return {"blocked": False, "reason": "no_papers"}
    except Exception:
        return {"blocked": False, "reason": "read_error"}

    # Re-rank by query-term relevance BEFORE triage: lit_results.json is in
    # retrieval order (broad surveys first), so blind papers[:8] can miss an
    # exact collision sitting at rank 8+. Score each paper by distinctive
    # query-term hits (title weighted 3x over abstract) and triage the top 8
    # most relevant — same 0-4 overlap semantics as scoop triage.
    from run_scoop import call_llm, extract_json
    import re as _re

    def _qtok(s: str) -> list[str]:
        toks = _re.findall(r"[a-z0-9]{3,}", (s or "").lower())
        stop = {"the", "and", "for", "with", "versus", "from", "that",
                "this", "are", "was", "were", "has", "have", "had", "not",
                "but", "its", "our", "their", "about", "into", "over",
                "task", "tasks", "agent", "agents", "horizon"}
        seen: list[str] = []
        for t in toks:
            if t not in stop and t not in seen:
                seen.append(t)
        return seen

    qterms = _qtok(query)
    # Weight distinctive query phrases (title 5, abstract 2) above single
    # tokens (title 3, abstract 1): a paper matching the full phrase
    # "divide and conquer" outranks one matching a lone "long". Phrases are
    # full-query n-grams of length >= 2, so the whole query matching a title
    # cannot swamp single-term discrimination.
    words = _re.findall(r"[a-z0-9]{3,}", query.lower())
    phrases: list[str] = []
    for n in range(2, min(len(words), 5) + 1):
        for i in range(len(words) - n + 1):
            phrases.append(" ".join(words[i:i + n]))
    ranked: list[tuple[int, dict[str, Any]]] = []
    for p in papers:
        title = str(p.get("title", "") or "").lower()
        abstr = str(p.get("abstract", "") or "").lower()
        score = sum((3 if t in title else 0) + (1 if t in abstr else 0)
                    for t in qterms)
        score += sum((5 if ph in title else 0) + (2 if ph in abstr else 0)
                     for ph in phrases)
        # Tie-break: later retrieval rank = less relevant source ordering.
        ranked.append((score, p))
    ranked.sort(key=lambda sp: sp[0], reverse=True)
    # Keep each paper's original corpus index so paper_index stays a stable
    # pointer into lit_results.json (frontend/backend consumers use it to
    # look up the actual paper).
    top: list[tuple[int, dict[str, Any]]] = [
        (papers.index(p) if p in papers else i, p)
        for i, (_, p) in enumerate(ranked[:8])
    ]
    scored: list[dict[str, Any]] = []
    llm_backed = True
    try:
        batch_text = "\n\n".join([
            f"Paper {orig_idx}: {p.get('title', '?')}\nAbstract: {str(p.get('abstract', ''))[:400]}"
            for orig_idx, p in top
        ])
        system = "You are a prior-art triage system. Score each paper against the research idea. Return ONLY valid JSON array."
        user = f"""Research idea: {query[:800]}

Score each paper 0-4 on overlap with the idea (0 = unrelated, 4 = direct collision):

{batch_text}

Return JSON array:
[{{"paper_index": {top[0][0] if top else 0}, "overlap_score": 0, "notes": "concise explanation"}}] (use each paper's given Paper number as paper_index)"""
        raw = call_llm(system, user, timeout=90)
        parsed = extract_json(raw)
        items = parsed if isinstance(parsed, list) else []
        by_idx = {it.get("paper_index"): it for it in items if isinstance(it, dict)}
        for orig_idx, _ in top:
            it = by_idx.get(orig_idx, {})
            score = it.get("overlap_score", 0)
            try:
                score = max(0, min(int(score), 4))
            except Exception:
                score = 0
            scored.append({
                "paper_index": orig_idx,
                "overlap_score": score,
                "notes": str(it.get("notes", "")),
            })
        # Detect fallback: no usable LLM output at all.
        if not items or all(not str(s.get("notes", "")).strip() or s.get("notes") == "Automated baseline triage" for s in scored):
            llm_backed = False
    except Exception:
        llm_backed = False

    if not llm_backed:
        # Never block on an unbacked verdict — fail open, record why.
        gate = {"blocked": False, "reason": "llm_unavailable", "llm_backed": False,
                "max_overlap_score": 0, "level": None}
        try:
            gate_file.write_text(json.dumps(gate, indent=2), encoding="utf-8")
        except Exception:
            pass
        return gate

    max_overlap = max((s.get("overlap_score", 0) for s in scored), default=0)
    level = {4: 1, 3: 2, 2: 3, 1: 4, 0: 5}[max_overlap]
    blocked = max_overlap >= 3
    gate = {
        "blocked": blocked,
        "llm_backed": True,
        "max_overlap_score": max_overlap,
        "level": level,
        "summary": (
            "Strong prior-art collision; revision advised." if blocked
            else "No blocking prior art; proceeding."
        ),
        "scored": scored,
    }
    try:
        gate_file.write_text(json.dumps(gate, indent=2), encoding="utf-8")
    except Exception:
        pass
    return gate


# ── Router Endpoints ────────────────────────────────────────────────────────

@router.post("/pipeline/start", response_model=PipelineStartResponse)
async def start_pipeline(req: PipelineStartRequest, request: Request):
    """Start a research pipeline run. Generates run_id, persists initial state, and launches in background."""
    query = _sanitize_query(req.query)
    if not query:
        raise HTTPException(status_code=400, detail="Research query cannot be empty")
    if len(query) > MAX_QUERY_LEN:
        raise HTTPException(status_code=413, detail=f"Query exceeds maximum length ({MAX_QUERY_LEN} chars)")
    user = await _optional_user(request)
    if user is not None:
        can_run, used, limit = check_run_limit(user["id"])
        if not can_run:
            raise HTTPException(status_code=402, detail="Run quota exceeded")
        increment_run_count(user["id"])
    else:
        _check_anon_rate_limit(request)
    if not engine_present(PROJECT_ROOT):
        raise HTTPException(status_code=500, detail=MISSING_ENGINE_MSG)

    slug = _slugify(query)
    run_id = f"{slug}-{secrets.token_hex(4)}"
    run_dir = RUN_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Initialize subdirectories
    for d in ["phase0", "phase1", "phase2_select", "phase2_generate", "phase2_coherence", "phase3_collision", "phase3_critique", "phase3_revise", "phase4"]:
        (run_dir / d).mkdir(parents=True, exist_ok=True)

    (run_dir / "query.txt").write_text(query, encoding="utf-8")

    # Persist in DB and disk
    init_pipeline_run(run_id, query)
    sync_run_to_disk_and_db(run_id, run_dir)

    # Launch background worker (in-flight guarded: no duplicate threads)
    _try_spawn_worker(run_id)

    details = get_pipeline_run_details(run_id) or {}

    return PipelineStartResponse(
        run_id=run_id,
        status="running",
        query=query,
        phases=details.get("phases", {}),
    )


# ── Paper counts / legacy stubs / delete ──────────────────────────────────

def _paper_count_for(run_id: str) -> int:
    """Count papers in phase0 lit_results.json; 0 when absent/unparseable.

    Supports dict payloads ({papers:[...]}, {results:[...]}) and bare lists.
    Never raises — a missing paper count must not break run-status reads.
    """
    try:
        raw = (RUN_DIR / run_id / "phase0" / "lit_results.json").read_text(encoding="utf-8")
    except Exception:
        return 0
    try:
        data = json.loads(raw)
    except Exception:
        return 0
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("papers", "results", "hits", "items"):
            val = data.get(key)
            if isinstance(val, list):
                return len(val)
        total = data.get("total") or data.get("count") or data.get("num_papers")
        if isinstance(total, (int, float)) and total >= 0:
            return int(total)
    return 0


def _legacy_disk_stub(run_id: str) -> dict[str, Any] | None:
    """Graceful fallback for legacy phase0-only runs missing from the DB.

    Returns a disk-backed partial payload (200) instead of a 404 so old
    dashboard links keep rendering. None when no on-disk trace exists.
    """
    run_dir = RUN_DIR / run_id
    if not run_dir.exists() or not run_dir.is_dir():
        return None
    lit = run_dir / "phase0" / "lit_results.json"
    query_file = run_dir / "query.txt"
    query = run_id
    try:
        if query_file.exists():
            query = query_file.read_text(encoding="utf-8").strip() or run_id
    except Exception:
        pass
    phases: dict[str, Any] = {}
    if lit.exists():
        phases["phase0"] = {"status": "complete"}
    return {
        "run_id": run_id,
        "query": query,
        "status": "completed" if lit.exists() else "pending",
        "current_phase": "phase0",
        "total_duration_sec": 0.0,
        "phases": phases,
        "has_idea_card": False,
        "paper_count": _paper_count_for(run_id),
        "legacy": True,
    }


class DeleteRunRequest(BaseModel):
    run_id: str = Field(..., description="Run id to delete")


def _delete_run(run_id: str) -> dict[str, Any]:
    """Remove a run from SQLite + disk (idempotent on missing rows)."""
    _validate_run_id(run_id)
    run_dir = RUN_DIR / run_id
    db_gone = delete_pipeline_run(run_id)
    disk_gone = False
    if run_dir.exists():
        import shutil
        try:
            resolved = run_dir.resolve()
            base = RUN_DIR.resolve()
            if resolved != base and base in resolved.parents:
                shutil.rmtree(run_dir)
                disk_gone = True
        except Exception:
            pass
    with _worker_lock:
        _active_workers.pop(run_id, None)
        flag = _cancel_flags.pop(run_id, None)
        if flag is not None:
            try:
                flag.set()
            except Exception:
                pass
    if not db_gone and not disk_gone:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return {"run_id": run_id, "status": "deleted"}


@router.delete("/pipeline/runs/{run_id}")
async def delete_pipeline_run_endpoint(run_id: str):
    """Delete a run (DB row + on-disk artifacts). Canonical REST path."""
    return _delete_run(run_id)


@router.post("/pipeline/delete-run")
async def delete_pipeline_run_compat(req: DeleteRunRequest):
    """Legacy delete path kept for the dashboard caller (POST {run_id}).

    Same semantics as DELETE /pipeline/runs/{run_id}; kept so older
    dashboard builds never hit a dead route.
    """
    return _delete_run(req.run_id)


@router.get("/pipeline/runs", response_model=list[RunStatusResponse])
async def list_pipeline_runs(limit: int = 20, offset: int = 0, include_phases: bool = False):
    """List pipeline runs, newest first — single JOIN query, paginated.

    The polled list path never touches disk (no per-run sync/stat storm) and
    never fans out per-run detail queries. Per-run phase detail (12-phase
    map, artifact stats) is lazy via GET /runs/{id}, /runs/{id}/phases, and
    /runs/{id}/artifacts.
    """
    limit = max(1, min(int(limit or 20), 100))
    offset = max(0, int(offset or 0))
    db_runs = list_all_pipeline_runs(limit=limit, offset=offset, include_phases=include_phases)
    responses = []
    for r in db_runs:
        phases = r.get("phases", {}) or {}
        has_card = bool(r.get("has_idea_card")) or phases.get("phase4_card", {}).get("status") in ("complete", "completed")
        responses.append(RunStatusResponse(
            run_id=r["run_id"],
            query=r.get("query", ""),
            status=r.get("status", "pending"),
            current_phase=r.get("current_phase", "phase0"),
            total_duration_sec=float(r.get("total_duration_sec") or 0.0),
            phases=phases,
            has_idea_card=has_card,
            paper_count=_paper_count_for(r["run_id"]),
        ))
    return responses


@router.get("/pipeline/runs/{run_id}")
async def get_run_status(run_id: str):
    """Get detailed persisted status of a specific run.

    Legacy phase0-only runs missing from the DB fall back to a disk-backed
    partial stub (200) instead of a 404 so old links keep rendering.
    """
    _validate_run_id(run_id)
    run_dir = RUN_DIR / run_id
    if run_dir.exists():
        sync_run_to_disk_and_db(run_id, run_dir)

    details = get_pipeline_run_details(run_id)
    if not details:
        stub = _legacy_disk_stub(run_id)
        if stub is not None:
            return stub
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    phases = details.get("phases", {})
    has_card = phases.get("phase4_card", {}).get("status") in ("complete", "completed")

    return RunStatusResponse(
        run_id=run_id,
        query=details.get("query", ""),
        status=details.get("status", "pending"),
        current_phase=details.get("current_phase", "phase0"),
        total_duration_sec=float(details.get("total_duration_sec") or 0.0),
        phases=phases,
        has_idea_card=has_card,
        paper_count=_paper_count_for(run_id),
    )


@router.get("/pipeline/status/{run_id}", response_model=RunStatusResponse)
async def get_pipeline_status(run_id: str):
    """Get per-phase progress and saved state for historical inspection and UI reloads."""
    _validate_run_id(run_id)
    return await get_run_status(run_id)


@router.get("/pipeline/runs/{run_id}/phases")
async def get_run_phases(run_id: str):
    """Get full phase-by-phase breakdown for inspection."""
    _validate_run_id(run_id)
    run_dir = RUN_DIR / run_id
    if run_dir.exists():
        sync_run_to_disk_and_db(run_id, run_dir)

    details = get_pipeline_run_details(run_id)
    if not details:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    return {
        "run_id": run_id,
        "query": details.get("query", ""),
        "status": details.get("status", "pending"),
        "total_duration_sec": details.get("total_duration_sec", 0.0),
        "phases": details.get("phases", {}),
    }


@router.post("/pipeline/runs/{run_id}/resume")
async def resume_pipeline(run_id: str):
    """Resume execution of an interrupted or failed pipeline run."""
    _validate_run_id(run_id)
    run_dir = RUN_DIR / run_id
    if run_dir.exists():
        sync_run_to_disk_and_db(run_id, run_dir)

    details = get_pipeline_run_details(run_id)
    if not details and not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    run_dir.mkdir(parents=True, exist_ok=True)
    if details and details.get("query") and not (run_dir / "query.txt").exists():
        (run_dir / "query.txt").write_text(details["query"], encoding="utf-8")

    phases = (details or {}).get("phases", {})
    all_complete = bool(phases) and all(phases.get(p["key"], {}).get("status") in ("complete", "completed") for p in PIPELINE_PHASE_DEFS)
    if all_complete:
        return {"run_id": run_id, "status": "already_completed", "message": "All 12 phases already complete"}

    if not _try_spawn_worker(run_id):
        return {"run_id": run_id, "status": "already_running", "message": "Pipeline worker already in flight"}

    return {"run_id": run_id, "status": "resumed", "message": "Pipeline execution resumed in background"}


class GateOverrideRequest(BaseModel):
    override: bool = Field(default=True, description="Set true to build anyway despite the gate")


@router.post("/pipeline/runs/{run_id}/gate-override")
async def override_novelty_gate(run_id: str, req: GateOverrideRequest):
    """Override the novelty gate: continue building despite a low verdict ('Build anyway')."""
    _validate_run_id(run_id)
    run_dir = RUN_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if not req.override:
        raise HTTPException(status_code=400, detail="override must be true")
    (run_dir / "gate_override").write_text(
        datetime.now(timezone.utc).isoformat(), encoding="utf-8"
    )
    # Reset the gate marker so the worker re-evaluates and proceeds.
    update_pipeline_phase(run_id=run_id, phase_key="phase0_fulltext", status="pending")
    try:
        sync_run_to_disk_and_db(run_id, run_dir)
    except Exception:
        pass
    if not _try_spawn_worker(run_id):
        return {"run_id": run_id, "status": "already_running", "message": "Pipeline worker already in flight"}
    return {"run_id": run_id, "status": "resumed", "message": "Gate overridden — building anyway"}


@router.get("/pipeline/runs/{run_id}/gate")
async def get_novelty_gate(run_id: str):
    """Read the novelty gate verdict for a run (if computed)."""
    _validate_run_id(run_id)
    gate_file = RUN_DIR / run_id / "phase0" / "novelty_gate.json"
    if not gate_file.exists():
        return {"run_id": run_id, "gate": None}
    try:
        return {"run_id": run_id, "gate": json.loads(gate_file.read_text(encoding="utf-8"))}
    except Exception:
        return {"run_id": run_id, "gate": None}


@router.post("/pipeline/runs/{run_id}/cancel")
async def cancel_pipeline(run_id: str):
    """Cancel a pipeline run: flag it so the worker loop stops between phases.

    Cancelling a terminal run (completed/failed/cancelled) is a no-op
    success. Unknown run ids return 404.
    """
    _validate_run_id(run_id)
    run_dir = RUN_DIR / run_id
    details = get_pipeline_run_details(run_id)
    if not details and not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    status = (details or {}).get("status", "")
    if status in ("completed", "failed", "cancelled"):
        return {"run_id": run_id, "status": status, "message": f"Run already {status}"}

    # Cooperative stop: checked by the worker loop between phases.
    _request_cancel(run_id)
    cancelled = cancel_pipeline_run(run_id)
    if not cancelled and not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    return {"run_id": run_id, "status": "cancelled", "message": "Pipeline run cancelled"}


@router.post("/pipeline/runs/{run_id}/phase/{phase_key}")
async def run_single_phase_endpoint(run_id: str, phase_key: str):
    """Execute a single phase on demand and update persisted state."""
    _validate_run_id(run_id)
    _validate_phase_dir(phase_key, "phase_key")
    run_dir = RUN_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    valid_keys = [p["key"] for p in PIPELINE_PHASE_DEFS]
    if phase_key not in valid_keys:
        raise HTTPException(status_code=400, detail=f"Invalid phase_key: {phase_key}")
    if not engine_present(PROJECT_ROOT):
        raise HTTPException(status_code=500, detail=MISSING_ENGINE_MSG)

    ok = _execute_single_phase(run_id, phase_key)
    details = get_pipeline_run_details(run_id) or {}
    phase_info = details.get("phases", {}).get(phase_key, {})

    return {
        "run_id": run_id,
        "phase_key": phase_key,
        "success": ok,
        "phase": phase_info,
    }


@router.get("/pipeline/runs/{run_id}/artifacts", response_model=ArtifactListResponse)
async def list_run_artifacts(run_id: str):
    """List all artifacts across all phases for a given run."""
    _validate_run_id(run_id)
    run_dir = RUN_DIR / run_id
    if run_dir.exists():
        sync_run_to_disk_and_db(run_id, run_dir)

    details = get_pipeline_run_details(run_id) or {}
    if not details and not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    phases = details.get("phases", {})

    all_artifacts = []
    for pkey, pdata in phases.items():
        for art in pdata.get("artifacts", []):
            rel = art.get("rel_path", "")
            if rel and "/" in rel:
                pdir, fname = rel.split("/", 1)
                all_artifacts.append(ArtifactInfo(
                    name=art.get("name", fname),
                    rel_path=rel,
                    size=art.get("size", 0),
                    mtime=art.get("mtime", 0.0),
                    ext=art.get("ext", ""),
                    url=f"/api/pipeline/runs/{run_id}/artifacts/{pdir}/{fname}",
                ))

    return ArtifactListResponse(run_id=run_id, artifacts=all_artifacts)


@router.get("/pipeline/runs/{run_id}/artifacts/{phase_dir}/{filename}")
async def get_artifact_file(run_id: str, phase_dir: str, filename: str):
    """Serve artifact file content directly."""
    _validate_run_id(run_id)
    _validate_phase_dir(phase_dir)
    _validate_filename(filename)

    file_path = RUN_DIR / run_id / phase_dir / filename
    # Resolve and confine under RUN_DIR/run_id (defense in depth).
    try:
        resolved = file_path.resolve()
        base = (RUN_DIR / run_id).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")
    if resolved != base and base not in resolved.parents:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"Artifact {filename} not found")

    media_type, _ = mimetypes.guess_type(str(file_path))
    if not media_type:
        if filename.endswith(".md"):
            media_type = "text/markdown; charset=utf-8"
        elif filename.endswith(".json"):
            media_type = "application/json"
        elif filename.endswith((".tex", ".txt", ".csv")):
            media_type = "text/plain; charset=utf-8"
        else:
            media_type = "application/octet-stream"

    return FileResponse(file_path, media_type=media_type, filename=filename)


# ── Backward Compatible Phase Endpoints ────────────────────────────────────

@router.post("/pipeline/runs/{run_id}/phase0-fulltext")
async def trigger_phase0_fulltext(run_id: str):
    """Run Phase 0+ full-text fetch."""
    _validate_run_id(run_id)
    ok = _execute_single_phase(run_id, "phase0_fulltext")
    return {"run_id": run_id, "success": ok}


@router.post("/pipeline/runs/{run_id}/collision")
async def trigger_collision(run_id: str):
    """Run Phase 3.1 collision check."""
    _validate_run_id(run_id)
    ok = _execute_single_phase(run_id, "phase3_collision")
    return {"run_id": run_id, "success": ok}


@router.post("/pipeline/runs/{run_id}/skeleton")
async def trigger_skeleton(run_id: str):
    """Run Phase 4 skeleton."""
    _validate_run_id(run_id)
    ok = _execute_single_phase(run_id, "phase4_skeleton")
    return {"run_id": run_id, "success": ok}
