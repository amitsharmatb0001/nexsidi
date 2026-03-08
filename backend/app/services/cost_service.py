"""Token usage and cost calculation service.

COST-FIX: Tracks token consumption per agent per stage and calculates
estimated cost in USD and INR based on current API pricing.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


import structlog

logger = structlog.get_logger(__name__)
# Pricing per million tokens (as of 2026-03) — USD
_ANTHROPIC_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-6": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-haiku-4-6": {"input": 0.8, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
}
_GEMINI_PRICING: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {"input": 0.15, "output": 0.6, "cache_read": 0.0, "cache_write": 0.0},
    "gemini-2.5-pro": {"input": 1.25, "output": 5.0, "cache_read": 0.3125, "cache_write": 0.0},
    "gemini-3-flash-preview": {"input": 0.20, "output": 0.8, "cache_read": 0.0, "cache_write": 0.0},
    "gemini-3.1-flash-lite-preview": {"input": 0.20, "output": 0.8, "cache_read": 0.0, "cache_write": 0.0},
    "gemini-3.1-pro-preview": {"input": 1.75, "output": 7.0, "cache_read": 0.0, "cache_write": 0.0},
}
_ALL_PRICING = {**_ANTHROPIC_PRICING, **_GEMINI_PRICING}

# AUDIT-T3-10: Allow config overrides for pricing (e.g., negotiated rates)
try:
    from app.config import get_settings as _get_cost_settings
    _pricing_overrides = getattr(_get_cost_settings(), "pricing_overrides", None)
    if isinstance(_pricing_overrides, dict):
        for model_name, model_pricing in _pricing_overrides.items():
            if isinstance(model_pricing, dict):
                _ALL_PRICING[model_name] = model_pricing
except Exception:
    pass  # Config not available at import time — use defaults

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


def _reload_pricing_overrides() -> dict[str, dict[str, float]]:
    """AUDIT-B3-FIX: Reload pricing overrides from config at call time.

    Previously overrides were loaded once at import — config changes were ignored.
    """
    pricing = {**_ANTHROPIC_PRICING, **_GEMINI_PRICING}
    try:
        from app.config import get_settings as _get_cost_settings
        overrides = getattr(_get_cost_settings(), "pricing_overrides", None)
        if isinstance(overrides, dict):
            for model_name, model_pricing in overrides.items():
                if isinstance(model_pricing, dict):
                    pricing[model_name] = model_pricing
    except Exception:
        pass
    return pricing


def calculate_pipeline_cost(run_id: str, steps_data: list[dict[str, Any]]) -> PipelineCost:
    """Calculate cost from pipeline step records.

    COST-FIX: Reads token counts from pipeline.steps table rows and
    calculates estimated API cost in USD and INR per agent/stage.
    AUDIT-B3-FIX: Now reloads pricing overrides at call time.
    """
    # AUDIT-B3-FIX: Reload pricing each call so config changes take effect
    current_pricing = _reload_pricing_overrides()
    result = PipelineCost(run_id=run_id)
    for step in steps_data:
        model = step.get("model_used", "")
        pricing = current_pricing.get(
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
