"""Phase 0+ full-text fetch endpoint."""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SUB_DIR = PROJECT_ROOT / "researchstudio" / "ResearchStudio-Idea" / "skills" / "idea_spark"
RUN_DIR = PROJECT_ROOT / "ideaspark_run"

# Legacy self-host fallback path for the API key file. The OPENROUTER_API_KEY
# env var takes precedence; this file is only consulted when it is unset.
LEGACY_KEY_FILE = Path("/home/enigma/.kinox/env")


class FulltextRequest(BaseModel):
    run_id: str = Field(..., description="Run ID from Phase 0")
    phase: str = Field(default="phase0", description="Phase directory name")


class FulltextResponse(BaseModel):
    run_id: str
    success: bool
    stdout: str = ""
    stderr: str = ""


def _get_venv_python() -> str:
    venv = PROJECT_ROOT / ".venv"
    return str(venv / "bin" / "python3")


def _env_with_bridge() -> dict[str, str]:
    env = dict(PATH=f"{PROJECT_ROOT / '.venv' / 'bin'}:/usr/bin:/bin")
    llm_bridge = str(PROJECT_ROOT / "llm_bridge.py")
    env["NOVELTY_LLM_CLASSIFY_FAST_CMD"] = f"python3 {llm_bridge} --mode classify-fast"
    env["NOVELTY_LLM_REASONING_LARGE_CMD"] = f"python3 {llm_bridge} --mode reasoning-large"
    kinox = LEGACY_KEY_FILE
    if kinox.exists():
        for line in kinox.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                key = line.split("=", 1)[1].strip()
                if key:
                    env["OPENROUTER_API_KEY"] = key
                    break
    return env


@router.post("/phase0-fulltext", response_model=FulltextResponse)
async def run_phase0_fulltext(req: FulltextRequest):
    """Run Phase 0+ full-text fetch for a completed Phase 0 run."""
    phase_dir = RUN_DIR / req.run_id / req.phase
    if not phase_dir.exists():
        raise HTTPException(status_code=404, detail=f"Phase dir not found: {phase_dir}")

    python = _get_venv_python()
    script = SUB_DIR / "scripts" / "run.py"
    cmd = [python, str(script), "phase0_fulltext", "--out", str(phase_dir)]

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
        raise HTTPException(status_code=504, detail="Full-text fetch timed out (600s)")

    return FulltextResponse(
        run_id=req.run_id,
        success=result.returncode == 0,
        stdout=result.stdout[-2000:] if result.stdout else "",
        stderr=result.stderr[-500:] if result.stderr else "",
    )