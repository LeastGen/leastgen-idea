"""Results retrieval and exploration endpoints."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_DIR = PROJECT_ROOT / "ideaspark_run"


@router.get("/results/{run_id}")
async def get_results(run_id: str, phase: str = "phase0"):
    """Get results from a specific run and phase."""
    phase_path = RUN_DIR / run_id / phase
    if not phase_path.exists():
        raise HTTPException(status_code=404, detail=f"No results for {run_id}/{phase}")

    files = []
    for f in sorted(phase_path.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            content = None
            if f.suffix in (".json", ".md", ".txt", ".csv"):
                content = f.read_text(encoding="utf-8", errors="replace")[:5000]
            files.append({
                "name": f.name,
                "size": f.stat().st_size,
                "preview": content[:500] if content else None,
            })

    return {
        "run_id": run_id,
        "phase": phase,
        "path": str(phase_path),
        "files": files,
    }


@router.get("/results/{run_id}/file")
async def get_file(run_id: str, phase: str = "phase0", filename: str = ""):
    """Get the full content of a specific result file."""
    file_path = RUN_DIR / run_id / phase / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    content = file_path.read_text(encoding="utf-8", errors="replace")

    return {
        "filename": filename,
        "run_id": run_id,
        "phase": phase,
        "content": content,
        "size": file_path.stat().st_size,
    }