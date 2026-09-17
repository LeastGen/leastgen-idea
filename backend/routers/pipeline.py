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

from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from backend.database import (
    PIPELINE_PHASE_DEFS,
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

def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] if slug else "research-run"


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
            cmd = [str(PIPELINE_SCRIPT), "phase0", query]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=str(PROJECT_ROOT), env=env)
            success = (proc.returncode == 0) and (run_dir / "phase0" / "lit_results.json").exists()
            if not success:
                error_msg = proc.stderr[-500:] if proc.stderr else "Phase 0 failed to generate lit_results.json"

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
    """Worker thread that executes pipeline phases sequentially until completion or failure."""
    run_dir = RUN_DIR / run_id
    if not run_dir.exists():
        return

    for p in PIPELINE_PHASE_DEFS:
        key = p["key"]
        details = get_pipeline_run_details(run_id)
        if not details:
            break

        phase_status = details.get("phases", {}).get(key, {}).get("status", "pending")
        if phase_status in ("complete", "completed"):
            continue

        # Execute this phase
        ok = _execute_single_phase(run_id, key)
        if not ok:
            print(f"Pipeline {run_id} failed at phase {key}")
            break

    with _worker_lock:
        _active_workers.pop(run_id, None)


# ── Router Endpoints ────────────────────────────────────────────────────────

@router.post("/pipeline/start", response_model=PipelineStartResponse)
async def start_pipeline(req: PipelineStartRequest):
    """Start a research pipeline run. Generates run_id, persists initial state, and launches in background."""
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Research query cannot be empty")
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

    # Launch background worker
    with _worker_lock:
        t = threading.Thread(target=_pipeline_worker_loop, args=(run_id,), daemon=True)
        _active_workers[run_id] = t
        t.start()

    details = get_pipeline_run_details(run_id) or {}

    return PipelineStartResponse(
        run_id=run_id,
        status="running",
        query=query,
        phases=details.get("phases", {}),
    )


@router.get("/pipeline/runs", response_model=list[RunStatusResponse])
async def list_pipeline_runs():
    """List all pipeline runs with their persisted states and phase details."""
    if not RUN_DIR.exists():
        return []

    # Sync any new or modified runs from disk
    for entry in sorted(RUN_DIR.iterdir(), reverse=True):
        if entry.is_dir() and (entry / "query.txt").exists():
            try:
                sync_run_to_disk_and_db(entry.name, entry)
            except Exception:
                pass

    db_runs = list_all_pipeline_runs()
    responses = []
    for r in db_runs:
        phases = r.get("phases", {})
        has_card = phases.get("phase4_card", {}).get("status") in ("complete", "completed")
        responses.append(RunStatusResponse(
            run_id=r["run_id"],
            query=r.get("query", ""),
            status=r.get("status", "pending"),
            current_phase=r.get("current_phase", "phase0"),
            total_duration_sec=float(r.get("total_duration_sec") or 0.0),
            phases=phases,
            has_idea_card=has_card,
        ))
    return responses


@router.get("/pipeline/runs/{run_id}", response_model=RunStatusResponse)
async def get_run_status(run_id: str):
    """Get detailed persisted status of a specific run."""
    run_dir = RUN_DIR / run_id
    if run_dir.exists():
        sync_run_to_disk_and_db(run_id, run_dir)

    details = get_pipeline_run_details(run_id)
    if not details:
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
    )


@router.get("/pipeline/status/{run_id}", response_model=RunStatusResponse)
async def get_pipeline_status(run_id: str):
    """Get per-phase progress and saved state for historical inspection and UI reloads."""
    return await get_run_status(run_id)


@router.get("/pipeline/runs/{run_id}/phases")
async def get_run_phases(run_id: str):
    """Get full phase-by-phase breakdown for inspection."""
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

    t = threading.Thread(target=_pipeline_worker_loop, args=(run_id,), daemon=True)
    _active_workers[run_id] = t
    t.start()

    return {"run_id": run_id, "status": "resumed", "message": "Pipeline execution resumed in background"}


@router.post("/pipeline/runs/{run_id}/phase/{phase_key}")
async def run_single_phase_endpoint(run_id: str, phase_key: str):
    """Execute a single phase on demand and update persisted state."""
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
    # Sanitize inputs to prevent directory traversal
    if ".." in phase_dir or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid path")

    file_path = RUN_DIR / run_id / phase_dir / filename
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
    ok = _execute_single_phase(run_id, "phase0_fulltext")
    return {"run_id": run_id, "success": ok}


@router.post("/pipeline/runs/{run_id}/collision")
async def trigger_collision(run_id: str):
    """Run Phase 3.1 collision check."""
    ok = _execute_single_phase(run_id, "phase3_collision")
    return {"run_id": run_id, "success": ok}


@router.post("/pipeline/runs/{run_id}/skeleton")
async def trigger_skeleton(run_id: str):
    """Run Phase 4 skeleton."""
    ok = _execute_single_phase(run_id, "phase4_skeleton")
    return {"run_id": run_id, "success": ok}
