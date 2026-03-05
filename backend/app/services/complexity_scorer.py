"""Project complexity scoring and cost estimation.

REVIEW-FIX: The business model pricing (10K-3L INR) depends on complexity
scores that didn't exist in code. This module provides:
1. Heuristic complexity scoring (no AI call — deterministic, instant)
2. Pre-pipeline cost estimation based on complexity + tech stack
3. Pricing tier mapping for the NexSidi pricing model

Complexity dimensions (1-10 each, weighted):
- entity_count: Number of data entities
- endpoint_count: Number of API endpoints
- integration_count: External service integrations
- role_count: User role types
- auth_complexity: Authentication requirements
- payment_handling: Payment processing needs
- real_time_features: WebSocket/SSE/real-time requirements
- file_handling: Upload/download/processing
- compliance_level: Regulatory requirements (HIPAA, GDPR, PCI-DSS)
- scale_requirements: Expected user/data scale
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Complexity Dimensions and Weights ────────────────────────────────

@dataclass(frozen=True, slots=True)
class ComplexityDimension:
    name: str
    weight: float
    description: str


DIMENSIONS = [
    ComplexityDimension("entity_count", 1.5, "Number of data entities/models"),
    ComplexityDimension("endpoint_count", 1.2, "Number of API endpoints"),
    ComplexityDimension("integration_count", 1.5, "External service integrations"),
    ComplexityDimension("role_count", 1.0, "Number of user role types"),
    ComplexityDimension("auth_complexity", 1.3, "Authentication complexity (basic/OAuth/MFA)"),
    ComplexityDimension("payment_handling", 1.8, "Payment processing needs"),
    ComplexityDimension("real_time_features", 1.3, "Real-time / WebSocket features"),
    ComplexityDimension("file_handling", 0.8, "File upload/download/processing"),
    ComplexityDimension("compliance_level", 1.5, "Regulatory requirements"),
    ComplexityDimension("scale_requirements", 1.0, "Expected user/data scale"),
]

# Dimension name → weight for fast lookup
_DIM_WEIGHTS = {d.name: d.weight for d in DIMENSIONS}
_TOTAL_WEIGHT = sum(d.weight for d in DIMENSIONS)


# ── Keywords for Heuristic Scoring ───────────────────────────────────

_INTEGRATION_KEYWORDS = [
    "stripe", "razorpay", "paypal", "braintree", "square",
    "twilio", "sendgrid", "mailgun", "ses", "sms",
    "firebase", "aws", "gcp", "azure", "cloudinary",
    "elasticsearch", "algolia", "redis", "rabbitmq", "kafka",
    "oauth", "saml", "ldap", "sso",
    "google maps", "mapbox", "geocoding",
    "openai", "anthropic", "gemini", "vertex ai",
    "websocket", "socket.io", "pusher", "ably",
    "s3", "gcs", "blob storage", "cloudfront", "cdn",
]

_AUTH_KEYWORDS = {
    "basic": 2, "jwt": 3, "oauth": 5, "oauth2": 5, "sso": 6,
    "mfa": 7, "2fa": 7, "totp": 7, "passkey": 8, "webauthn": 8,
    "biometric": 9, "fido2": 9,
    "rbac": 4, "abac": 6, "role-based": 4,
}

_PAYMENT_KEYWORDS = {
    "payment": 5, "checkout": 5, "subscription": 7, "recurring": 7,
    "invoice": 6, "billing": 6, "refund": 5, "promo code": 4,
    "coupon": 4, "discount": 4, "tax": 5, "multi-currency": 8,
}

_COMPLIANCE_KEYWORDS = {
    "hipaa": 8, "gdpr": 6, "pci": 8, "pci-dss": 9, "sox": 7,
    "ferpa": 6, "ccpa": 5, "audit trail": 5, "audit log": 5,
    "data encryption": 4, "data residency": 6,
}

_REALTIME_KEYWORDS = [
    "real-time", "realtime", "websocket", "live", "streaming",
    "notification", "push notification", "chat", "messaging",
    "collaborative", "presence", "typing indicator",
]


# ── Complexity Result ────────────────────────────────────────────────

@dataclass(slots=True)
class ComplexityResult:
    """Result of heuristic complexity scoring."""

    overall_score: float  # 1.0 - 10.0 (weighted average)
    dimensions: dict[str, int]  # Individual dimension scores (1-10)
    tier: str  # "basic" | "standard" | "professional" | "enterprise"
    pricing_inr: int  # Estimated price in INR
    estimated_cost_usd: float  # Estimated AI API cost
    estimated_duration_minutes: int  # Estimated pipeline duration
    features_detected: list[str]  # Keywords found
    entities_detected: list[str]  # Entity names found

    @property
    def pricing_usd(self) -> float:
        return round(self.pricing_inr / 84.0, 2)


# ── Pricing Tiers ────────────────────────────────────────────────────

_PRICING_TIERS = [
    # (max_score, tier_name, price_inr)
    (3.0, "basic", 10_000),
    (5.0, "standard", 50_000),
    (7.5, "professional", 1_50_000),
    (10.0, "enterprise", 3_00_000),
]


# ── Cost Estimation Constants ────────────────────────────────────────

# Average tokens per pipeline stage (based on observed runs)
_AVG_TOKENS_PER_STAGE = 8_000  # input + output combined
_PIPELINE_STAGES = 18
_BASE_AI_COST_USD = 0.50  # Minimum AI cost for simplest project

# Price per 1M tokens (weighted average of Gemini + Claude mix)
_COST_PER_MILLION_TOKENS = 5.0  # USD (mixed model pricing)

# Complexity multipliers for cost estimation
_COMPLEXITY_COST_MULTIPLIER = {
    "basic": 0.5,
    "standard": 1.0,
    "professional": 2.0,
    "enterprise": 4.0,
}

# Estimated duration per stage (minutes)
_AVG_MINUTES_PER_STAGE = 2.5


# ── Scoring Functions ────────────────────────────────────────────────

def _count_keywords(text: str, keywords: list[str]) -> int:
    """Count how many keywords appear in the text."""
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in text_lower)


def _max_keyword_score(text: str, keyword_scores: dict[str, int]) -> int:
    """Return the highest matching keyword score."""
    text_lower = text.lower()
    matches = [score for kw, score in keyword_scores.items() if kw.lower() in text_lower]
    return max(matches) if matches else 1


def _score_entity_count(count: int) -> int:
    """Score based on number of entities."""
    if count <= 2:
        return 1
    elif count <= 5:
        return 3
    elif count <= 10:
        return 5
    elif count <= 20:
        return 7
    elif count <= 35:
        return 8
    else:
        return 10


def _score_endpoint_count(count: int) -> int:
    """Score based on number of API endpoints."""
    if count <= 5:
        return 1
    elif count <= 10:
        return 3
    elif count <= 20:
        return 5
    elif count <= 40:
        return 7
    elif count <= 60:
        return 8
    else:
        return 10


def _score_role_count(count: int) -> int:
    """Score based on number of user roles."""
    if count <= 1:
        return 1
    elif count <= 2:
        return 3
    elif count <= 4:
        return 5
    elif count <= 6:
        return 7
    else:
        return 9


# ── Entity Detection ────────────────────────────────────────────────

_ENTITY_PATTERNS = [
    # Common entity keywords
    r"\b(user|account|profile|customer|employee|admin|role)\b",
    r"\b(product|item|catalog|inventory|sku|variant)\b",
    r"\b(order|cart|checkout|payment|invoice|transaction)\b",
    r"\b(post|article|blog|comment|review|rating)\b",
    r"\b(message|chat|notification|conversation|thread)\b",
    r"\b(category|tag|label|collection|group)\b",
    r"\b(file|image|document|attachment|media|upload)\b",
    r"\b(setting|preference|config|permission|policy)\b",
    r"\b(report|analytics|metric|log|audit)\b",
    r"\b(subscription|plan|tier|membership)\b",
    r"\b(address|location|store|warehouse|branch)\b",
    r"\b(ticket|issue|task|project|workflow)\b",
]


def _detect_entities(description: str) -> list[str]:
    """Detect likely data entities from project description."""
    found = set()
    text_lower = description.lower()
    for pattern in _ENTITY_PATTERNS:
        matches = re.findall(pattern, text_lower)
        found.update(matches)
    return sorted(found)


# ── Main Scoring Function ───────────────────────────────────────────

def score_complexity(
    description: str,
    tech_stack: dict[str, str] | None = None,
) -> ComplexityResult:
    """Compute project complexity score from description and optional tech stack.

    This is a PURE HEURISTIC — no AI calls. Returns instantly.

    Args:
        description: Project description / requirements text.
        tech_stack: Optional dict with keys like "backend", "frontend", "database".

    Returns:
        ComplexityResult with overall score, dimensions, tier, pricing.
    """
    if not description:
        description = ""

    tech_stack = tech_stack or {}
    full_text = description + " " + " ".join(tech_stack.values())

    # Detect entities and features
    entities = _detect_entities(description)
    features = [kw for kw in _INTEGRATION_KEYWORDS if kw.lower() in full_text.lower()]

    # Score each dimension
    entity_count = max(len(entities), 2)  # At least 2 for any real project
    endpoint_estimate = entity_count * 4  # ~4 endpoints per entity (CRUD + extras)

    integration_count = _count_keywords(full_text, _INTEGRATION_KEYWORDS)
    role_count = _count_keywords(full_text, ["admin", "user", "manager", "moderator", "editor", "viewer", "guest", "staff", "owner"])

    dimensions = {
        "entity_count": _score_entity_count(entity_count),
        "endpoint_count": _score_endpoint_count(endpoint_estimate),
        "integration_count": min(10, max(1, integration_count * 2)),
        "role_count": _score_role_count(max(role_count, 1)),
        "auth_complexity": _max_keyword_score(full_text, _AUTH_KEYWORDS),
        "payment_handling": _max_keyword_score(full_text, _PAYMENT_KEYWORDS),
        "real_time_features": min(10, max(1, _count_keywords(full_text, _REALTIME_KEYWORDS) * 3)),
        "file_handling": min(8, max(1, _count_keywords(full_text, ["upload", "download", "file", "image", "media", "attachment", "storage"]) * 2)),
        "compliance_level": _max_keyword_score(full_text, _COMPLIANCE_KEYWORDS),
        "scale_requirements": _max_keyword_score(full_text, {
            "millions": 8, "thousands": 5, "hundreds": 3,
            "enterprise": 7, "startup": 3, "mvp": 2, "prototype": 1,
            "high availability": 8, "99.9%": 9, "scalable": 5,
        }),
    }

    # Compute weighted average
    weighted_sum = sum(
        dimensions[d.name] * d.weight for d in DIMENSIONS
    )
    overall = round(weighted_sum / _TOTAL_WEIGHT, 1)
    overall = max(1.0, min(10.0, overall))

    # Determine tier
    tier = "enterprise"
    pricing_inr = 3_00_000
    for max_score, tier_name, price in _PRICING_TIERS:
        if overall <= max_score:
            tier = tier_name
            pricing_inr = price
            break

    # Estimate AI cost
    multiplier = _COMPLEXITY_COST_MULTIPLIER.get(tier, 1.0)
    total_tokens = _AVG_TOKENS_PER_STAGE * _PIPELINE_STAGES * multiplier
    estimated_cost = max(
        _BASE_AI_COST_USD,
        (total_tokens / 1_000_000) * _COST_PER_MILLION_TOKENS,
    )

    # Estimate duration
    estimated_duration = int(_AVG_MINUTES_PER_STAGE * _PIPELINE_STAGES * multiplier)

    return ComplexityResult(
        overall_score=overall,
        dimensions=dimensions,
        tier=tier,
        pricing_inr=pricing_inr,
        estimated_cost_usd=round(estimated_cost, 2),
        estimated_duration_minutes=estimated_duration,
        features_detected=features,
        entities_detected=entities,
    )
