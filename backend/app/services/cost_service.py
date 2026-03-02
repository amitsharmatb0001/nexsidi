"""Token usage and cost calculation service.

COST-FIX: Tracks token consumption per agent per stage and calculates
estimated cost in USD and INR based on current API pricing.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

# Pricing per million tokens (as of 2026-03) — USD
_ANTHROPIC_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-6": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-haiku-4-6": {"input": 0.8, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
}
_GEMINI_PRICING: dict[str, dict[str, float]] = {
    "gemini-2.0-flash": {"input": 0.15, "output": 0.6, "cache_read": 0.0, "cache_write": 0.0},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.0, "cache_read": 0.3125, "cache_write": 0.0},
}
_ALL_PRICING = {**_ANTHROPIC_PRICING, **_GEMINI_PRICING}
_USD_TO_INR = 84.0  # approximate


@dataclass
class AgentCost:
    agent: str
    stage: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    cost_inr: float = 0.0


@dataclass
class PipelineCost:
    run_id: str
    agents: list[AgentCost] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cost_usd: float = 0.0
    total_cost_inr: float = 0.0
    estimated_savings_usd: float = 0.0  # From cache hits


def calculate_pipeline_cost(run_id: str, steps_data: list[dict[str, Any]]) -> PipelineCost:
    """Calculate cost from pipeline step records.

    COST-FIX: Reads token counts from pipeline.steps table rows and
    calculates estimated API cost in USD and INR per agent/stage.
    """
    result = PipelineCost(run_id=run_id)
    for step in steps_data:
        model = step.get("model_used", "")
        pricing = _ALL_PRICING.get(
            model,
            {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
        )

        inp = int(step.get("input_tokens", 0) or 0)
        out = int(step.get("output_tokens", 0) or 0)
        cache_r = int(step.get("cache_read_tokens", 0) or 0)
        cache_w = int(step.get("cache_write_tokens", 0) or 0)

        cost_usd = (
            (inp / 1_000_000) * pricing["input"]
            + (out / 1_000_000) * pricing["output"]
            + (cache_r / 1_000_000) * pricing.get("cache_read", 0)
            + (cache_w / 1_000_000) * pricing.get("cache_write", 0)
        )
        savings_usd = (cache_r / 1_000_000) * (
            pricing["input"] - pricing.get("cache_read", 0)
        )

        agent_cost = AgentCost(
            agent=step.get("agent_name", ""),
            stage=step.get("stage", ""),
            model=model,
            input_tokens=inp,
            output_tokens=out,
            cache_read_tokens=cache_r,
            cache_write_tokens=cache_w,
            cost_usd=cost_usd,
            cost_inr=cost_usd * _USD_TO_INR,
        )
        result.agents.append(agent_cost)
        result.total_input_tokens += inp
        result.total_output_tokens += out
        result.total_cache_read_tokens += cache_r
        result.total_cost_usd += cost_usd
        result.estimated_savings_usd += savings_usd

    result.total_cost_inr = result.total_cost_usd * _USD_TO_INR
    return result
