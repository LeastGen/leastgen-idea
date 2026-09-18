"""IdeaFlow UI — serves the modern frontend SPA from frontend/ directory."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from backend.routers.auth import get_current_user

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"


def _read(path: str) -> str | None:
    """Read a frontend file, returning None if not found."""
    f = FRONTEND_DIR / path
    if f.exists() and f.is_file():
        return f.read_text(encoding="utf-8")
    return None


@router.get("/ui", response_class=HTMLResponse)
async def ui_index():
    """Serve the main SPA."""
    html = _read("index.html")
    if html is None:
        raise HTTPException(status_code=404, detail="Frontend not built")
    return HTMLResponse(html)


@router.get("/ui/style.css", response_class=HTMLResponse)
async def ui_css():
    """Serve the stylesheet."""
    css = _read("style.css")
    if css is None:
        raise HTTPException(status_code=404, detail="CSS not found")
    return HTMLResponse(css, media_type="text/css")


@router.get("/ui/app.js", response_class=HTMLResponse)
async def ui_js():
    """Serve the JavaScript application."""
    js = _read("app.js")
    if js is None:
        raise HTTPException(status_code=404, detail="JS not found")
    return HTMLResponse(js, media_type="application/javascript")


@router.get("/ui/card/{run_id}")
async def ui_card(run_id: str, user: dict = Depends(get_current_user)):
    """Serve the rendered idea card markdown for a run."""
    import re
    if not re.match(r"^[A-Za-z0-9_-]+$", run_id or ""):
        raise HTTPException(status_code=400, detail="Invalid run_id")
    run_dir = PROJECT_ROOT / "ideaspark_run" / run_id
    try:
        resolved_base = (PROJECT_ROOT / "ideaspark_run").resolve()
        card_path = run_dir / "phase4" / "idea.std.en.md"
        resolved = card_path.resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid run_id")
    if resolved != resolved_base and resolved_base not in resolved.parents:
        raise HTTPException(status_code=400, detail="Invalid run_id")
    if not card_path.exists():
        raise HTTPException(status_code=404, detail="Card not found")
    return HTMLResponse(card_path.read_text(encoding="utf-8"))
