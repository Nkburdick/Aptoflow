"""Cost tracking for LLM calls."""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class CostInfo:
    """Cost information extracted from a single LLM response."""

    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


def extract_cost(response) -> CostInfo:
    """Extract cost information from an OpenAI-style response object.

    Args:
        response: Response from OpenAI SDK (with usage attribute).

    Returns:
        CostInfo with token counts and cost.
    """
    model = getattr(response, "model", "unknown")
    usage = getattr(response, "usage", None)

    if usage is None:
        return CostInfo(model=model)

    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    total_tokens = prompt_tokens + completion_tokens

    return CostInfo(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


@dataclass
class CostTracker:
    """Accumulates costs across multiple LLM calls."""

    records: list[CostInfo] = field(default_factory=list)

    def add(self, cost_info: CostInfo) -> None:
        """Record a cost entry."""
        self.records.append(cost_info)

    @property
    def total_tokens(self) -> int:
        return sum(r.total_tokens for r in self.records)

    @property
    def total_cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.records)

    def summary(self) -> dict:
        """Return a summary of costs by model."""
        by_model: dict[str, dict] = {}
        for r in self.records:
            if r.model not in by_model:
                by_model[r.model] = {"calls": 0, "tokens": 0, "cost_usd": 0.0}
            by_model[r.model]["calls"] += 1
            by_model[r.model]["tokens"] += r.total_tokens
            by_model[r.model]["cost_usd"] += r.cost_usd

        return {
            "total_calls": len(self.records),
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "by_model": by_model,
        }
