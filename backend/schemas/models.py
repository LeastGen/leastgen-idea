"""Pydantic models for the IdeaFlow API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PhaseRequest(BaseModel):
    """Request body for running a pipeline phase."""
    query: str = Field(..., min_length=1, max_length=2000, description="Research query / idea description")
    config: dict = Field(default_factory=dict, description="Optional config overrides")


class PhaseResponse(BaseModel):
    """Response from a phase execution."""
    run_id: str
    phase: str
    query: str
    output_dir: str
    stdout: str = ""
    stderr: str = ""
    success: bool


class RunStatus(BaseModel):
    """Status of a research run."""
    run_id: str
    phases: dict[str, dict] = Field(default_factory=dict)
    path: str = ""


class ConnectorStatus(BaseModel):
    """Status of a single connector."""
    name: str
    available: bool
    hint: str | None = None