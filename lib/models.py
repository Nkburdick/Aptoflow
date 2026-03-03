"""Pydantic base models for Aptoflow workflows."""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class WorkflowInput(BaseModel):
    """Base input model — extend per workflow."""

    pass


class WorkflowOutput(BaseModel):
    """Base output model — extend per workflow."""

    pass


class WebhookRequest(BaseModel):
    """Standard webhook request envelope."""

    input: dict[str, Any] = Field(default_factory=dict)


class WebhookResponse(BaseModel):
    """Standard webhook response envelope."""

    success: bool
    result: Any | None = None
    error: str | None = None
    cost_usd: float | None = None
    model: str | None = None


class ToolCall(BaseModel):
    """Represents a tool call from the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


class CostRecord(BaseModel):
    """Records cost for a single LLM call."""

    model: str
    tokens: int
    cost_usd: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
