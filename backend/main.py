"""IdeaFlow Backend — FastAPI app entry point."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.routers import phases, results, health, fulltext, pipeline, dashboard, ui, auto, scoop
from backend.routers import auth as auth_router
from backend.routers import billing as billing_router
from backend.routers import export as export_router

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = PROJECT_ROOT / "ideaspark_run"
RUN_DIR.mkdir(exist_ok=True)

# ── Load env from the legacy self-host key file ─────────────────────────────
# The environment takes precedence (setdefault below); this file is only a
# fallback for local self-hosted setups.
_KINOX_ENV = Path("/home/enigma/.kinox/env")  # legacy self-host fallback path
if _KINOX_ENV.exists():
    for line in _KINOX_ENV.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value:
            os.environ.setdefault(key, value)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.project_root = PROJECT_ROOT
    app.state.run_dir = RUN_DIR
    # Initialize database
    from backend.database import init_db
    init_db()
    yield
    # Shutdown


app = FastAPI(
    title="LeastGen Labs API",
    version="0.2.0",
    lifespan=lifespan,
)

# ── CORS ───────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────────────
# Core pipeline
app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(phases.router, prefix="/api", tags=["phases"])
app.include_router(results.router, prefix="/api", tags=["results"])
app.include_router(fulltext.router, prefix="/api", tags=["fulltext"])
app.include_router(pipeline.router, prefix="/api", tags=["pipeline"])
app.include_router(dashboard.router, prefix="/api", tags=["dashboard"])
app.include_router(ui.router, prefix="/api", tags=["ui"])
app.include_router(auto.router, prefix="/api", tags=["auto"])
app.include_router(scoop.router, prefix="/api", tags=["scoop"])

# Hosted services
app.include_router(auth_router.router, prefix="/api", tags=["auth"])
app.include_router(billing_router.router, prefix="/api", tags=["billing"])
app.include_router(export_router.router, prefix="/api", tags=["export"])

# Root redirect to UI
@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/api/ui")


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "detail": "Internal server error"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8756, reload=True)