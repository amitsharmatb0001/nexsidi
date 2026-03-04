"""Typed data contracts between NexSidi agents.

Ensures type safety and clear expectations at every agent handoff.
Each contract is a frozen dataclass — immutable once created, preventing
accidental mutation as data flows through the 18-stage pipeline.

Usage:
    output = TilotmaOutput(project_id="...", ...)
    validate_contract(output)  # Raises ContractViolation if invalid

Design:
    - frozen=True: prevents mutation after creation (immutable pipeline data)
    - slots=True: memory-efficient (matches base.py style)
    - tuple instead of list: frozen dataclasses can't contain mutable defaults
    - validate_contract(): runtime check that all required fields are non-empty
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


class ContractViolation(ValueError):
    """Raised when an agent output violates its contract."""

    def __init__(self, contract_name: str, field_name: str, reason: str) -> None:
        self.contract_name = contract_name
        self.field_name = field_name
        self.reason = reason
        super().__init__(f"{contract_name}.{field_name}: {reason}")


# ── Contracts ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TilotmaOutput:
    """What Tilotma gives to Saanvi — structured requirements."""

    project_id: str
    description: str
    key_features: tuple[str, ...]
    project_type: str = "web_app"
    tech_preferences: dict[str, Any] | None = None
    compliance_flags: tuple[str, ...] = ()
    capability_validated: bool = False
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class SaanviOutput:
    """What Saanvi gives to Vikram — requirements analysis."""

    project_id: str
    requirements: dict[str, Any]  # Structured requirements
    complexity_score: int  # 1-10
    estimated_cost_inr: int
    estimated_hours: int
    recommended_tech_stack: dict[str, str]
    database_requirements: tuple[str, ...]
    api_endpoints_needed: tuple[str, ...]
    domain: str | None = None
    domain_models: tuple[dict[str, Any], ...] | None = None
    domain_endpoints: tuple[dict[str, Any], ...] | None = None
    domain_pages: tuple[dict[str, Any], ...] | None = None


@dataclass(frozen=True, slots=True)
class VikramOutput:
    """What Vikram gives to downstream agents — Architecture Contract.

    This is the single source of truth for all downstream agents.
    Immutable after CHECKPOINT_DESIGN approval.
    """

    project_id: str
    blueprint: dict[str, Any]
    tech_stack: dict[str, str]
    database_schema: dict[str, Any]
    api_contracts: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class DhruvOutput:
    """What Dhruv gives to Shubham — database artifacts."""

    project_id: str
    schema_ddl: str
    migration_code: str
    seed_data: str
    indexing_strategy: dict[str, Any]
    provisioning_config: dict[str, Any] | None = None
    er_diagram_description: str = ""
    performance_notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ShubhamOutput:
    """What Shubham produces — backend code."""

    project_id: str
    files_generated: tuple[str, ...]
    file_contents: dict[str, str]  # path -> content
    backend_framework: str
    workspace_path: str = ""
    self_check: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AanyaOutput:
    """What Aanya produces — frontend code."""

    project_id: str
    files_generated: tuple[str, ...]
    file_contents: dict[str, str]  # path -> content
    frontend_framework: str
    workspace_path: str = ""
    self_check: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AaravOutput:
    """What Aarav produces — test results."""

    project_id: str
    all_passed: bool
    total_tests: int
    total_passed: int
    total_failed: int
    phase_results: tuple[dict[str, Any], ...]
    is_simulation_sandbox: bool = True


@dataclass(frozen=True, slots=True)
class KaranOutput:
    """What Karan produces — security audit."""

    project_id: str
    findings: tuple[dict[str, Any], ...]
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    security_score: float = 0.0  # 0-100


@dataclass(frozen=True, slots=True)
class FixerOutput:
    """What Fixer produces — error corrections."""

    project_id: str
    errors_found: int
    errors_fixed: int
    errors_remaining: int
    fix_iterations: int
    files_modified: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TilotmaReviewOutput:
    """What Tilotma produces in TILOTMA_REVIEW stage — GO/NO-GO."""

    project_id: str
    decision: str  # "approved" or "rejected"
    reasoning: str
    all_tests_passed: bool
    security_clean: bool
    errors_remaining: int
    confidence: float = 0.0


# ── Validation ─────────────────────────────────────────────────────


def validate_contract(contract: Any) -> list[str]:
    """Validate a contract dataclass has all required fields populated.

    Returns a list of violation messages (empty if valid).
    Does NOT raise — caller decides how to handle violations.

    Example:
        violations = validate_contract(output)
        if violations:
            logger.warning("Contract violations", violations=violations)
    """
    violations: list[str] = []
    contract_name = type(contract).__name__

    for f in fields(contract):
        value = getattr(contract, f.name)

        # Required string fields must be non-empty
        if f.type in ("str", "str | None") and isinstance(value, str) and not value and f.default != "":
            # Only flag if there's no explicit default of ""
            if f.default != "":
                violations.append(f"{contract_name}.{f.name}: required string is empty")

        # Required dict fields must be non-empty
        if "dict" in str(f.type) and isinstance(value, dict) and not value:
            if f.default is not None and "None" not in str(f.type):
                violations.append(f"{contract_name}.{f.name}: required dict is empty")

        # Required tuple fields must be non-empty
        if "tuple" in str(f.type) and isinstance(value, tuple) and len(value) == 0:
            if f.default != () and "None" not in str(f.type):
                violations.append(f"{contract_name}.{f.name}: required tuple is empty")

    return violations


def contract_to_dict(contract: Any) -> dict[str, Any]:
    """Convert a frozen contract dataclass to a mutable dict for context storage.

    Recursively converts tuples back to lists for JSON serialization.
    """
    from dataclasses import asdict
    result = asdict(contract)

    def _tuples_to_lists(obj: Any) -> Any:
        if isinstance(obj, tuple):
            return [_tuples_to_lists(item) for item in obj]
        if isinstance(obj, dict):
            return {k: _tuples_to_lists(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_tuples_to_lists(item) for item in obj]
        return obj

    return _tuples_to_lists(result)
