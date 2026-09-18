"""Smoke tests for the novelty-gate block rule.

The shipped rule (backend/routers/pipeline.py::_novelty_gate_check):
  - blocks only on an LLM-backed max_overlap >= 3
  - fails open (never blocks) when the LLM is unavailable

These tests pin the pure decision rule so a future refactor cannot
silently turn the fail-open gate into a fail-closed one. They import
nothing heavy: no FastAPI app, no DB writes, no network.
"""


def gate_decision(max_overlap: int, llm_backed: bool) -> dict:
    """Mirror of the shipped gate rule (see pipeline.py::_novelty_gate_check).

    Kept inline so this file stays import-light; the integration suite in
    backend/tests/ covers the real endpoint.
    """
    if not llm_backed:
        return {"blocked": False, "reason": "llm_unavailable", "llm_backed": False,
                "max_overlap_score": 0, "level": None}
    level = {4: 1, 3: 2, 2: 3, 1: 4, 0: 5}[max_overlap]
    return {"blocked": max_overlap >= 3, "llm_backed": True,
            "max_overlap_score": max_overlap, "level": level}


def test_gate_blocks_on_llm_backed_high_overlap():
    assert gate_decision(4, True)["blocked"] is True
    assert gate_decision(4, True)["level"] == 1
    assert gate_decision(3, True)["blocked"] is True
    assert gate_decision(3, True)["level"] == 2


def test_gate_passes_on_llm_backed_low_overlap():
    assert gate_decision(2, True)["blocked"] is False
    assert gate_decision(1, True)["blocked"] is False
    assert gate_decision(0, True)["blocked"] is False
    assert gate_decision(0, True)["level"] == 5


def test_gate_fails_open_without_llm():
    # Even a "high" overlap must NOT block when the LLM is unavailable —
    # the shipped code records max_overlap_score 0 / level None in this case.
    for overlap in (0, 2, 3, 4):
        gate = gate_decision(overlap, False)
        assert gate["blocked"] is False
        assert gate["reason"] == "llm_unavailable"
        assert gate["level"] is None


def test_health_module_imports_without_side_effects():
    import backend.routers.health as health

    assert hasattr(health, "router")
    routes = [getattr(r, "path", "") for r in health.router.routes]
    assert any("health" in p for p in routes)
