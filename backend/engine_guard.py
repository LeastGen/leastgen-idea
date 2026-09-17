"""Shared guard for the vendored ResearchStudio-Idea engine.

The engine lives under researchstudio/ (git-ignored, fetched on demand via
scripts/fetch_engine.sh). Every code path that shells out to the engine must
call require_engine() first so a fresh clone fails with an actionable message
instead of a bare FileNotFoundError / 500.
"""

from __future__ import annotations

from pathlib import Path

ENGINE_REL = Path("researchstudio") / "ResearchStudio-Idea" / "skills" / "idea_spark"
ENGINE_RUN = Path("scripts") / "run.py"

MISSING_ENGINE_MSG = (
    "ResearchStudio engine not found at researchstudio/. "
    "Fetch it with: bash scripts/fetch_engine.sh "
    "(clones the pinned Microsoft ResearchStudio ref; MIT-licensed)."
)


def engine_dir(project_root: Path) -> Path:
    return project_root / ENGINE_REL


def engine_run_script(project_root: Path) -> Path:
    return engine_dir(project_root) / ENGINE_RUN


def engine_present(project_root: Path) -> bool:
    return engine_run_script(project_root).is_file()


def require_engine(project_root: Path) -> Path:
    """Return the engine skill dir, or raise with the fetch hint."""
    skill = engine_dir(project_root)
    if not engine_run_script(project_root).is_file():
        raise RuntimeError(MISSING_ENGINE_MSG)
    return skill
