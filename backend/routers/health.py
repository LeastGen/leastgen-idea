"""Health and diagnostics endpoints."""

from __future__ import annotations

import importlib
import sys

from fastapi import APIRouter

router = APIRouter()

CONNECTOR_MODULES = [
    ("arxiv", "feedparser"),
    ("openalex", None),
    ("semanticscholar", None),
    ("openreview", "openreview"),
]


@router.get("/health")
async def health_check():
    """Basic health check."""
    return {"status": "ok", "version": "0.2.0"}


@router.get("/connectors")
async def connectors_check():
    """Check which connectors are available."""
    results = []
    for label, pip_pkg in CONNECTOR_MODULES:
        if pip_pkg:
            try:
                importlib.import_module(pip_pkg)
                results.append({"name": label, "available": True})
            except ImportError:
                results.append({"name": label, "available": False, "hint": f"pip install {pip_pkg}"})
        else:
            # These are built-in or part of the idea_spark package
            results.append({"name": label, "available": True})

    # Full-text fetch deps
    fetch_deps = []
    for pkg, pip_name in (("fitz", "pymupdf"), ("bs4", "beautifulsoup4")):
        try:
            importlib.import_module(pkg)
            fetch_deps.append({"name": pip_name, "available": True})
        except ImportError:
            fetch_deps.append({"name": pip_name, "available": False, "hint": f"pip install {pip_name}"})

    return {
        "connectors": results,
        "full_text_deps": fetch_deps,
        "python": sys.version,
    }


@router.get("/llm-status")
async def llm_status():
    """Check whether the LLM bridge is configured."""
    import os

    has_key = bool(os.environ.get("OPENROUTER_API_KEY"))
    classify_cmd = os.environ.get("NOVELTY_LLM_CLASSIFY_FAST_CMD")
    reasoning_cmd = os.environ.get("NOVELTY_LLM_REASONING_LARGE_CMD")

    return {
        "has_openrouter_key": has_key,
        "classify_fast_cmd": bool(classify_cmd),
        "reasoning_large_cmd": bool(reasoning_cmd),
        "openrouter_key_set": has_key,
    }