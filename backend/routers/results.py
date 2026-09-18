"""Results retrieval and exploration endpoints."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_DIR = PROJECT_ROOT / "ideaspark_run"

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_PHASE_RE = re.compile(r"^[A-Za-z0-9_]+$")
_FILENAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")


def _validate_run_id(run_id: str) -> str:
    if not _RUN_ID_RE.match(run_id or ""):
        raise HTTPException(status_code=400, detail="Invalid run_id")
    return run_id


def _validate_phase(phase: str) -> str:
    if not _PHASE_RE.match(phase or ""):
        raise HTTPException(status_code=400, detail="Invalid phase")
    return phase


def _validate_filename(filename: str) -> str:
    if (not _FILENAME_RE.match(filename or "") or ".." in filename
            or "/" in filename or "\\" in filename
            or filename.startswith(".") or len(filename) > 255):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return filename


def _confine(base: Path, target: Path) -> Path:
    try:
        resolved = target.resolve()
        base_r = base.resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")
    if resolved != base_r and base_r not in resolved.parents:
        raise HTTPException(status_code=400, detail="Invalid path")
    return resolved


@router.get("/results/{run_id}")
async def get_results(run_id: str, phase: str = "phase0"):
    """Get results from a specific run and phase."""
    _validate_run_id(run_id)
    _validate_phase(phase)
    phase_path = _confine(RUN_DIR, RUN_DIR / run_id / phase)
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
    _validate_run_id(run_id)
    _validate_phase(phase)
    _validate_filename(filename)
    file_path = _confine(RUN_DIR, RUN_DIR / run_id / phase / filename)
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
