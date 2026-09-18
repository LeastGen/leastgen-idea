"""Auto phase trigger endpoint for the segmented UI."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_DIR = PROJECT_ROOT / "ideaspark_run"
PIPELINE_SCRIPT = PROJECT_ROOT / "run_pipeline.sh"
LLM_RUNNER = PROJECT_ROOT / "run_llm_phase.py"

PHASE_MAP = {
    "1": "phase1/phase1_output.json",
    "2.1": "phase2_select/phase2_select_output.json",
    "2.2": "phase2_generate/phase2_generate_output.json",
    "2.3": "phase2_coherence/phase2_coherence_output.json",
    "3.2": "phase3_critique/phase3_critique_output.json",
    "3.3": "phase3_revise/phase3_revise_output.json",
    "4.fill": "phase4/fill_map.json",
}


@router.post("/auto/phase/{run_id}/{phase_num}")
async def trigger_auto_phase(run_id: str, phase_num: str):
    """Trigger an auto-LLM or automated phase for a run."""
    run_dir = RUN_DIR / run_id
    if not run_dir.exists():
        return {"error": f"Run not found: {run_id}"}

    # Automated phases
    if phase_num == "phase0_fulltext":
        cmd = [str(PIPELINE_SCRIPT), "phase0-fulltext", run_id]
    elif phase_num == "3.1":
        cmd = [str(PIPELINE_SCRIPT), "collision", run_id]
    elif phase_num == "4.skeleton":
        cmd = [str(PIPELINE_SCRIPT), "skeleton", run_id]
    elif phase_num in PHASE_MAP:
        # LLM phase via auto-runner
        cmd = ["python3", str(LLM_RUNNER), str(run_dir), phase_num]
    else:
        return {"error": f"Unknown phase: {phase_num}"}

    try:
        # Offload the blocking subprocess (up to 600s) to a worker thread so
        # the async event loop stays responsive to other requests.
        result = await asyncio.to_thread(
            lambda: subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                cwd=str(PROJECT_ROOT),
            )
        )
        return {
            "success": result.returncode == 0,
            "output": (result.stdout[-500:] if result.stdout else ""),
            "error": (result.stderr[-500:] if result.stderr else ""),
        }
    except subprocess.TimeoutExpired:
        return {"error": "Phase timed out (600s)"}