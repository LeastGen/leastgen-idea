"""Pipeline phase execution endpoints."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.schemas.models import PhaseRequest, PhaseResponse, RunStatus
from backend.engine_guard import require_engine

router = APIRouter()

# ── Resolve paths ──────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_DIR = PROJECT_ROOT / "researchstudio" / "ResearchStudio-Idea" / "skills" / "idea_spark"
RUN_DIR = PROJECT_ROOT / "ideaspark_run"


def _engine_script() -> Path:
    """Engine entry point, with actionable error when the engine is unfetched."""
    require_engine(PROJECT_ROOT)
    return SKILL_DIR / "scripts" / "run.py"


def _slugify(text: str) -> str:
    return (
        text.lower()
        .replace(" ", "-")
        .encode("ascii", "ignore")
        .decode()
    )


def _get_venv_python() -> str:
    """Return the venv's python3 path."""
    venv = PROJECT_ROOT / ".venv"
    return str(venv / "bin" / "python3")


def _env_with_bridge() -> dict[str, str]:
    """Build env with LLM bridge commands set."""
    env = os.environ.copy()
    llm_bridge = str(PROJECT_ROOT / "llm_bridge.py")
    env["NOVELTY_LLM_CLASSIFY_FAST_CMD"] = f"python3 {llm_bridge} --mode classify-fast"
    env["NOVELTY_LLM_REASONING_LARGE_CMD"] = f"python3 {llm_bridge} --mode reasoning-large"
    return env


@router.post("/phase0", response_model=PhaseResponse)
async def run_phase0(req: PhaseRequest):
    """Run Phase 0: literature search for a research query."""
    query = req.query
    slug = _slugify(query)
    run_id = str(uuid.uuid4())[:8]
    out_dir = RUN_DIR / f"{slug}-{run_id}" / "phase0"
    out_dir.mkdir(parents=True, exist_ok=True)

    python = _get_venv_python()
    try:
        script = _engine_script()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    cmd = [
        python, str(script), "phase0",
        "--query", query,
        "--out", str(out_dir),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            env=_env_with_bridge(),
            cwd=str(PROJECT_ROOT),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Phase 0 timed out (300s)")

    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Phase 0 failed",
                "stderr": result.stderr[-1000:] if result.stderr else "",
            },
        )

    return PhaseResponse(
        run_id=f"{slug}-{run_id}",
        phase="phase0",
        query=query,
        output_dir=str(out_dir),
        stdout=result.stdout[-2000:] if result.stdout else "",
        stderr=result.stderr[-500:] if result.stderr else "",
        success=True,
    )


@router.post("/next", response_model=dict)
async def get_next_step(run_id: str):
    """Get the next step for a run directory."""
    run_path = RUN_DIR / run_id
    if not run_path.exists():
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    python = _get_venv_python()
    try:
        script = _engine_script()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    cmd = [python, str(script), "next", "--dir", str(run_path)]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env=_env_with_bridge(),
            cwd=str(PROJECT_ROOT),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Next-step check timed out")

    return {
        "run_id": run_id,
        "next_step": result.stdout[-2000:] if result.stdout else "",
        "success": result.returncode == 0,
    }


@router.get("/runs", response_model=list[dict])
async def list_runs():
    """List all runs in the ideaspark_run directory."""
    if not RUN_DIR.exists():
        return []
    runs = []
    for entry in sorted(RUN_DIR.iterdir(), reverse=True):
        if entry.is_dir():
            phase_dirs = [p.name for p in entry.iterdir() if p.is_dir()]
            runs.append({
                "run_id": entry.name,
                "phases": phase_dirs,
                "path": str(entry),
            })
    return runs


@router.get("/runs/{run_id}", response_model=RunStatus)
async def get_run_status(run_id: str):
    """Get detailed status of a specific run."""
    run_path = RUN_DIR / run_id
    if not run_path.exists():
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    phases_found = {}
    for phase_dir in sorted(run_path.iterdir()):
        if phase_dir.is_dir():
            files = [f.name for f in phase_dir.iterdir() if f.is_file()]
            # Check for completion markers
            has_complete = any(f.startswith(".complete") for f in files)
            has_pending = any(f.startswith("._") or f.endswith("_pending") for f in files)
            has_results = any(
                f.endswith((".json", ".md", ".csv", ".txt")) for f in files
            )
            phases_found[phase_dir.name] = {
                "completed": has_complete or has_results,
                "pending": has_pending,
                "files": files[-10:],  # last 10 files
            }

    return RunStatus(
        run_id=run_id,
        phases=phases_found,
        path=str(run_path),
    )


@router.post("/run-full", response_model=PhaseResponse)
async def run_full_pipeline(req: PhaseRequest):
    """Run the full ideaflow pipeline (phase0 only for now)."""
    # For now, runs phase0, then returns. More phases added later.
    return await run_phase0(req)