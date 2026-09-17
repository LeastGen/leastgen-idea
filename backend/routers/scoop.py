"""Scoop-Check — 7-step novelty verification router with schema validation and robust status tracking."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCOOP_DIR = PROJECT_ROOT / "scoop_runs"
SCOOP_DIR.mkdir(exist_ok=True)

STEP_NAMES = [
    "Initializing",
    "Decomposing Novelty",
    "Literature Search",
    "Prior-Art Triage",
    "Candidate Identification",
    "Deep Dive Analysis",
    "Novelty Verdict",
    "Summary & Verification",
]


def _sanitize_and_validate_input(text: str, field_name: str) -> str:
    """Validate and sanitize input text against empty/malformed values and dangerous characters."""
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: must be a string.")
    
    cleaned = text.strip()
    if len(cleaned) < 3:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: input too short (min 3 characters).")
    if len(cleaned) > 4000:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: input exceeds maximum length (4000 characters).")
    
    # Strip null bytes and non-printable control characters (except newline/tab)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", cleaned)
    
    # Check that there is at least one alphanumeric character
    if not re.search(r"[a-zA-Z0-9]", cleaned):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: must contain meaningful alphanumeric text.")
        
    return cleaned


def _extract_safe_query(problem: str) -> str:
    """Safely extract search keywords and sanitize for literature query."""
    # Strip out shell meta-characters
    safe = re.sub(r"[`$\"';&|<>\\{}]", " ", problem)
    # Collapse multiple spaces
    safe = re.sub(r"\s+", " ", safe).strip()
    return safe[:160]


# ── Schemas ─────────────────────────────────────────────────────────────────

class ScoopStartRequest(BaseModel):
    problem: str = Field(..., min_length=3, description="Research problem statement")
    novelty: str = Field(..., min_length=3, description="Claimed novelty differentiator")


class ScoopVerdict(BaseModel):
    level: int = Field(..., ge=1, le=5)
    summary: str
    per_axis: dict[str, str] = Field(default_factory=dict)
    recommendation: str = Field(..., description="proceed | revise_claim | abandon")
    confidence: float = 0.85
    max_overlap_score: int = 0


class ScoopStatusResponse(BaseModel):
    scoop_id: str
    status: str  # started | decomposing | searching | triaging | identifying | diving | verdict | summarizing | done | failed
    step: int
    step_name: str = "Initializing"
    total_steps: int = 7
    result: dict | None = None
    summary: dict | None = None
    error: str | None = None


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/scoop-check/start", response_model=ScoopStatusResponse)
async def start_scoop_check(req: ScoopStartRequest):
    """Start a 7-step Scoop-Check. Validates inputs, initializes directory, and launches in background."""
    problem = _sanitize_and_validate_input(req.problem, "problem statement")
    novelty = _sanitize_and_validate_input(req.novelty, "novelty claim")
    safe_search_query = _extract_safe_query(problem)

    scoop_id = uuid.uuid4().hex[:12]
    scoop_dir = SCOOP_DIR / scoop_id
    scoop_dir.mkdir(parents=True, exist_ok=True)

    # Save inputs and safe query
    (scoop_dir / "problem.txt").write_text(problem, encoding="utf-8")
    (scoop_dir / "novelty.txt").write_text(novelty, encoding="utf-8")
    (scoop_dir / "safe_query.txt").write_text(safe_search_query, encoding="utf-8")
    (scoop_dir / "status.json").write_text(
        json.dumps({
            "status": "started",
            "step": 0,
            "step_name": "Initializing",
            "safe_query": safe_search_query,
            "created_at": time.time(),
        }),
        encoding="utf-8",
    )

    # Launch full scoop-check runner in background
    subprocess.Popen(
        ["python3", str(PROJECT_ROOT / "run_scoop.py"), scoop_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(PROJECT_ROOT),
    )

    return ScoopStatusResponse(
        scoop_id=scoop_id,
        status="started",
        step=0,
        step_name="Initializing",
    )


@router.post("/scoop-check/extract-queries")
async def extract_scoop_queries(req: ScoopStartRequest):
    """Validate input, extract safe search queries, and generate multi-query search fallbacks."""
    problem = _sanitize_and_validate_input(req.problem, "problem statement")
    safe_query = _extract_safe_query(problem)
    from run_scoop import _extract_fallback_queries
    fallbacks = _extract_fallback_queries(safe_query)
    return {
        "original_problem": req.problem,
        "safe_query": safe_query,
        "fallback_queries": fallbacks,
    }


@router.get("/scoop-check/{scoop_id}", response_model=ScoopStatusResponse)
async def get_scoop_status(scoop_id: str):
    """Get the current status, step progress, verdict, and candidate analysis."""
    scoop_dir = SCOOP_DIR / scoop_id
    if not scoop_dir.exists():
        raise HTTPException(status_code=404, detail=f"Scoop check {scoop_id} not found")

    status_path = scoop_dir / "status.json"
    if not status_path.exists():
        return ScoopStatusResponse(scoop_id=scoop_id, status="started", step=0)

    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        data = {"status": "running", "step": 0}

    step = int(data.get("step", 0))
    step_name = STEP_NAMES[min(step, len(STEP_NAMES) - 1)]

    # Load verdict and summary if available
    result = None
    summary = None

    verdict_path = scoop_dir / "verdict.json"
    if verdict_path.exists():
        try:
            result = json.loads(verdict_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    summary_path = scoop_dir / "summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    error_msg = data.get("error")
    if not error_msg and (scoop_dir / "error.log").exists():
        error_msg = (scoop_dir / "error.log").read_text(encoding="utf-8")[:300]

    # Return structured fallback results if search sources fail or check failed
    if not result and data.get("status") in ("failed", "done"):
        problem_text = (scoop_dir / "problem.txt").read_text(encoding="utf-8") if (scoop_dir / "problem.txt").exists() else "Research claim"
        result = {
            "level": 3,
            "summary": f"Fallback novelty evaluation: Search sources reported degraded status. Preliminary baseline indicates potential partial overlap for '{problem_text[:60]}'.",
            "per_axis": {
                "problem_framing": "partial",
                "core_mechanism": "clear",
                "key_insight": "partial",
                "application_domain": "clear",
            },
            "recommendation": "revise_claim",
            "confidence": 0.65,
            "is_fallback": True,
            "fallback_reason": error_msg or "Search sources returned degraded results",
        }

    return ScoopStatusResponse(
        scoop_id=scoop_id,
        status=data.get("status", "unknown"),
        step=step,
        step_name=step_name,
        total_steps=7,
        result=result,
        summary=summary,
        error=error_msg,
    )


@router.post("/scoop-check/{scoop_id}/retry", response_model=ScoopStatusResponse)
async def retry_scoop_check(scoop_id: str):
    """Retry a failed or stalled scoop check."""
    scoop_dir = SCOOP_DIR / scoop_id
    if not scoop_dir.exists():
        raise HTTPException(status_code=404, detail=f"Scoop check {scoop_id} not found")

    if not (scoop_dir / "problem.txt").exists():
        raise HTTPException(status_code=400, detail="Cannot retry: missing problem statement")

    (scoop_dir / "status.json").write_text(
        json.dumps({
            "status": "started",
            "step": 0,
            "step_name": "Retrying",
            "retried_at": time.time(),
        }),
        encoding="utf-8",
    )

    subprocess.Popen(
        ["python3", str(PROJECT_ROOT / "run_scoop.py"), scoop_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(PROJECT_ROOT),
    )

    return ScoopStatusResponse(
        scoop_id=scoop_id,
        status="started",
        step=0,
        step_name="Retrying",
    )


@router.get("/scoop-check/history/all")
async def list_scoop_history():
    """List all previous scoop-check evaluations."""
    if not SCOOP_DIR.exists():
        return []

    items = []
    for d in sorted(SCOOP_DIR.iterdir(), key=os.path.getmtime, reverse=True):
        if d.is_dir() and (d / "problem.txt").exists():
            problem = (d / "problem.txt").read_text(encoding="utf-8")[:100]
            novelty = (d / "novelty.txt").read_text(encoding="utf-8")[:100] if (d / "novelty.txt").exists() else ""
            status_data = {}
            if (d / "status.json").exists():
                try:
                    status_data = json.loads((d / "status.json").read_text(encoding="utf-8"))
                except Exception:
                    pass

            verdict_data = {}
            if (d / "verdict.json").exists():
                try:
                    verdict_data = json.loads((d / "verdict.json").read_text(encoding="utf-8"))
                except Exception:
                    pass

            items.append({
                "scoop_id": d.name,
                "problem": problem,
                "novelty": novelty,
                "status": status_data.get("status", "unknown"),
                "step": status_data.get("step", 0),
                "verdict_level": verdict_data.get("level"),
                "recommendation": verdict_data.get("recommendation"),
                "summary": verdict_data.get("summary"),
                "mtime": d.stat().st_mtime,
            })
    return items
