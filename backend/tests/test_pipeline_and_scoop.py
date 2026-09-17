"""Unit and integration tests for Option 3:
1. Phase progress persistence (SQLite & disk sync).
2. Pipeline resume and artifact inspection endpoints.
3. Scoop-Check novelty verification error handling, schema validation, and fallback queries.
"""

import json
import os
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.database import (
    init_pipeline_run,
    update_pipeline_phase,
    get_pipeline_run_details,
    list_all_pipeline_runs,
    sync_run_to_disk_and_db,
    PIPELINE_PHASE_DEFS,
)
from run_scoop import (
    _extract_fallback_queries,
    step_1_decompose,
    step_6_verdict,
    extract_json,
    DEFAULT_AXES,
)

client = TestClient(app)


# ── Database & Persistence Tests ───────────────────────────────────────────

def test_pipeline_run_init_and_phase_persistence():
    """Verify that pipeline runs and all 12 phases are initialized and persisted in SQLite."""
    test_run_id = f"test-run-{int(time.time())}"
    query = "Efficient speculative decoding for multilingual LLM inference"

    run = init_pipeline_run(test_run_id, query)
    assert run["run_id"] == test_run_id
    assert run["query"] == query
    assert len(run["phases"]) == 12
    assert "phase0" in run["phases"]
    assert "phase4_card" in run["phases"]
    assert run["phases"]["phase0"]["status"] == "pending"

    # Update phase progress
    updated = update_pipeline_phase(
        run_id=test_run_id,
        phase_key="phase0",
        status="complete",
        started_at="2025-01-01T00:00:00Z",
        completed_at="2025-01-01T00:02:00Z",
        elapsed_seconds=120.0,
        artifacts=[{"name": "lit_results.json", "rel_path": "phase0/lit_results.json", "size": 1024}],
    )
    assert updated is not None
    assert updated["phases"]["phase0"]["status"] == "complete"
    assert updated["phases"]["phase0"]["elapsed_seconds"] == 120.0
    assert len(updated["phases"]["phase0"]["artifacts"]) == 1
    assert updated["phases"]["phase0"]["artifacts"][0]["name"] == "lit_results.json"


def test_sync_run_to_disk_and_db():
    """Verify that filesystem artifacts are synced to both SQLite and state.json."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = Path(tmpdir) / "test-sync-run-1"
        run_dir.mkdir(parents=True)
        (run_dir / "query.txt").write_text("Test query for synchronization", encoding="utf-8")

        # Create phase0 directory and mock artifact
        p0_dir = run_dir / "phase0"
        p0_dir.mkdir()
        (p0_dir / "lit_results.json").write_text(json.dumps([{"title": "Paper A"}]), encoding="utf-8")

        # Run sync
        result = sync_run_to_disk_and_db("test-sync-run-1", run_dir)
        assert result["run_id"] == "test-sync-run-1"
        assert result["phases"]["phase0"]["status"] == "complete"
        assert (run_dir / "state.json").exists()

        state_content = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        assert state_content["run_id"] == "test-sync-run-1"
        assert state_content["phases"]["phase0"]["status"] == "complete"


# ── Pipeline Router API Tests ──────────────────────────────────────────────

def test_pipeline_router_list_and_details():
    """Verify /api/pipeline/runs and /api/pipeline/runs/{run_id} endpoints."""
    # List runs
    resp = client.get("/api/pipeline/runs")
    assert resp.status_code == 200
    runs = resp.json()
    assert isinstance(runs, list)

    if runs:
        first_id = runs[0]["run_id"]
        # Get specific run
        run_resp = client.get(f"/api/pipeline/runs/{first_id}")
        assert run_resp.status_code == 200
        run_data = run_resp.json()
        assert run_data["run_id"] == first_id
        assert "phases" in run_data
        assert "total_duration_sec" in run_data

        # Get phases breakdown
        phases_resp = client.get(f"/api/pipeline/runs/{first_id}/phases")
        assert phases_resp.status_code == 200
        pdata = phases_resp.json()
        assert "phases" in pdata

        # Get artifacts list
        art_resp = client.get(f"/api/pipeline/runs/{first_id}/artifacts")
        assert art_resp.status_code == 200
        assert "artifacts" in art_resp.json()


def test_pipeline_resume_endpoint():
    """Verify /api/pipeline/runs/{run_id}/resume endpoint."""
    resp = client.get("/api/pipeline/runs")
    assert resp.status_code == 200
    runs = resp.json()
    if runs:
        target_id = runs[0]["run_id"]
        resume_resp = client.post(f"/api/pipeline/runs/{target_id}/resume")
        assert resume_resp.status_code == 200
        data = resume_resp.json()
        assert "status" in data


# ── Scoop-Check Novelty Verification Tests ──────────────────────────────────

def test_fallback_literature_query_extraction():
    """Verify query relaxation and keyword extraction for fallback literature search."""
    long_query = "A novel approach for efficient speculative decoding with tree-based verification using lightweight draft models"
    fallbacks = _extract_fallback_queries(long_query)
    assert len(fallbacks) > 0
    # Ensure filler words were removed
    assert not any("a novel approach for" in q.lower() for q in fallbacks)
    # Ensure core keywords are present
    combined = " ".join(fallbacks).lower()
    assert any(k in combined for k in ["speculative", "decoding", "draft", "tree", "verification"])


def test_scoop_step1_decompose_fallback():
    """Verify Step 1 schema validation and fallback axes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        scoop_dir = Path(tmpdir)
        # Without OpenRouter API key, step_1_decompose must fall back to standard 4 axes
        axes = step_1_decompose(scoop_dir, "Test problem", "Test novelty")
        assert len(axes) == 4
        axis_names = [a["name"] for a in axes]
        assert "problem_framing" in axis_names
        assert "core_mechanism" in axis_names
        assert "key_insight" in axis_names
        assert "application_domain" in axis_names
        assert (scoop_dir / "axes.json").exists()


def test_scoop_step6_verdict_schema_validation():
    """Verify Step 6 verdict produces valid 1-5 level, per-axis mapping, and recommendation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        scoop_dir = Path(tmpdir)
        axes = DEFAULT_AXES
        mock_candidates = [
            {"paper": {"title": "Prior Work A"}, "overlap_score": 3, "matched_axes": ["problem_framing", "core_mechanism"], "notes": "Close match"}
        ]
        verdict = step_6_verdict(scoop_dir, "Problem X", "Novelty Y", axes, mock_candidates)

        assert "level" in verdict
        assert 1 <= verdict["level"] <= 5
        assert verdict["recommendation"] in ("proceed", "revise_claim", "abandon")
        assert isinstance(verdict["summary"], str)
        assert len(verdict["summary"]) > 0
        assert "per_axis" in verdict
        assert len(verdict["per_axis"]) == 4
        assert (scoop_dir / "verdict.json").exists()


def test_scoop_endpoints():
    """Verify Scoop-Check FastAPI endpoints: start, status, history, retry."""
    # 1. Start scoop check
    start_resp = client.post(
        "/api/scoop-check/start",
        json={"problem": "Fast transformer inference", "novelty": "Tree-structured KV cache reuse"},
    )
    assert start_resp.status_code == 200
    start_data = start_resp.json()
    assert "scoop_id" in start_data
    assert start_data["status"] == "started"
    scoop_id = start_data["scoop_id"]

    # 2. Get status
    status_resp = client.get(f"/api/scoop-check/{scoop_id}")
    assert status_resp.status_code == 200
    s_data = status_resp.json()
    assert s_data["scoop_id"] == scoop_id
    assert s_data["total_steps"] == 7

    # 3. Get history
    hist_resp = client.get("/api/scoop-check/history/all")
    assert hist_resp.status_code == 200
    assert isinstance(hist_resp.json(), list)

    # 4. Retry endpoint
    retry_resp = client.post(f"/api/scoop-check/{scoop_id}/retry")
    assert retry_resp.status_code == 200
    assert retry_resp.json()["status"] == "started"
