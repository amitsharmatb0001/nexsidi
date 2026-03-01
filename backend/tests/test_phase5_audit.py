"""Phase 5 — Audit & Logging: comprehensive tests for pipeline_audit.py.

Covers:
- All 18 new AuditEventType enum members
- All new audit methods (AI, code, test, security, user, agent, cost)
- SHA-256 chain integrity (creation, verification, tamper detection)
- Chain hash determinism, genesis hash, multi-pipeline isolation
- AuditEvent frozen dataclass, to_dict, _event_data_for_hash
- Edge cases (empty strings, long strings/truncation, unicode, special chars)
- Query helpers (get_events, get_events_by_type, get_timeline, event_count)
- Singleton factory
- PipelineAuditService full lifecycle
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from app.services.pipeline_audit import (
    GENESIS_HASH,
    AuditEvent,
    AuditEventType,
    PipelineAuditService,
    _compute_chain_hash,
    get_pipeline_audit,
)


# ────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def audit() -> PipelineAuditService:
    """Fresh audit service per test — no singleton, no shared state."""
    return PipelineAuditService()


RUN_ID = "run-phase5-001"
RUN_ID_2 = "run-phase5-002"


# ════════════════════════════════════════════════════════════════════
# Section 1 — AuditEventType enum completeness
# ════════════════════════════════════════════════════════════════════


class TestAuditEventTypeEnum:
    """Verify all expected event types exist and have correct string values."""

    # Original event types (pre-Phase 5)
    ORIGINAL_TYPES = {
        "PIPELINE_CREATED": "pipeline.created",
        "PIPELINE_STARTED": "pipeline.started",
        "PIPELINE_COMPLETED": "pipeline.completed",
        "PIPELINE_FAILED": "pipeline.failed",
        "PIPELINE_CANCELLED": "pipeline.cancelled",
        "STAGE_STARTED": "stage.started",
        "STAGE_COMPLETED": "stage.completed",
        "STAGE_FAILED": "stage.failed",
        "STAGE_SKIPPED": "stage.skipped",
        "CHECKPOINT_PAUSED": "checkpoint.paused",
        "CHECKPOINT_APPROVED": "checkpoint.approved",
        "CHECKPOINT_REJECTED": "checkpoint.rejected",
        "AGENT_STARTED": "agent.started",
        "AGENT_COMPLETED": "agent.completed",
        "AGENT_FAILED": "agent.failed",
        "FIXER_ITERATION": "fixer.iteration",
        "FIXER_COMPLETED": "fixer.completed",
        "DEPLOY_STARTED": "deploy.started",
        "DEPLOY_COMPLETED": "deploy.completed",
        "DEPLOY_FAILED": "deploy.failed",
        "DELIVERY_CREATED": "delivery.created",
    }

    # New Phase 5 event types
    PHASE5_TYPES = {
        "AI_PROMPT_SENT": "ai.prompt_sent",
        "AI_RESPONSE_RECEIVED": "ai.response_received",
        "AI_ERROR": "ai.error",
        "CODE_GENERATED": "code.generated",
        "CODE_FIXED": "code.fixed",
        "TEST_PASSED": "test.passed",
        "TEST_FAILED": "test.failed",
        "SECURITY_SCAN_STARTED": "security.scan_started",
        "SECURITY_FINDING": "security.finding",
        "SECURITY_SCAN_COMPLETED": "security.scan_completed",
        "USER_APPROVED": "user.approved",
        "USER_REJECTED": "user.rejected",
        "USER_FEEDBACK": "user.feedback",
        "AGENT_DECISION": "agent.decision",
        "COST_RECORDED": "cost.recorded",
    }

    @pytest.mark.parametrize(
        "name,value",
        list(ORIGINAL_TYPES.items()) + list(PHASE5_TYPES.items()),
    )
    def test_event_type_exists_and_has_correct_value(self, name: str, value: str):
        member = AuditEventType[name]
        assert member.value == value

    def test_total_enum_member_count(self):
        expected = len(self.ORIGINAL_TYPES) + len(self.PHASE5_TYPES)
        assert len(AuditEventType) == expected, (
            f"Expected {expected} enum members, got {len(AuditEventType)}. "
            f"Members: {[m.name for m in AuditEventType]}"
        )

    @pytest.mark.parametrize("name", list(PHASE5_TYPES.keys()))
    def test_phase5_types_are_str_enum(self, name: str):
        member = AuditEventType[name]
        assert isinstance(member, str)
        assert isinstance(member, AuditEventType)

    def test_all_values_unique(self):
        values = [m.value for m in AuditEventType]
        assert len(values) == len(set(values)), "Duplicate enum values detected"


# ════════════════════════════════════════════════════════════════════
# Section 2 — AuditEvent dataclass
# ════════════════════════════════════════════════════════════════════


class TestAuditEventDataclass:
    """Verify AuditEvent is frozen, serializable, and hash-deterministic."""

    def _make_event(self, **kwargs) -> AuditEvent:
        defaults = {
            "event_type": AuditEventType.AI_PROMPT_SENT,
            "pipeline_run_id": RUN_ID,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "agent_name": "shubham",
            "stage": "backend_gen",
            "duration_ms": 123.4,
            "metadata": {"model": "sonnet"},
            "chain_hash": "abc123",
        }
        defaults.update(kwargs)
        return AuditEvent(**defaults)

    def test_frozen_cannot_modify(self):
        event = self._make_event()
        with pytest.raises(FrozenInstanceError):
            event.agent_name = "other"  # type: ignore[misc]

    def test_to_dict_includes_all_populated_fields(self):
        event = self._make_event()
        d = event.to_dict()
        assert d["event_type"] == "ai.prompt_sent"
        assert d["pipeline_run_id"] == RUN_ID
        assert d["timestamp"] == "2026-01-01T00:00:00+00:00"
        assert d["agent_name"] == "shubham"
        assert d["stage"] == "backend_gen"
        assert d["duration_ms"] == 123.4
        assert d["metadata"] == {"model": "sonnet"}
        assert d["chain_hash"] == "abc123"

    def test_to_dict_omits_empty_fields(self):
        event = AuditEvent(
            event_type=AuditEventType.PIPELINE_STARTED,
            pipeline_run_id=RUN_ID,
            timestamp="2026-01-01T00:00:00+00:00",
        )
        d = event.to_dict()
        assert "agent_name" not in d
        assert "stage" not in d
        assert "duration_ms" not in d
        assert "metadata" not in d
        assert "chain_hash" not in d

    def test_to_dict_duration_rounds(self):
        event = self._make_event(duration_ms=99.9999)
        d = event.to_dict()
        assert d["duration_ms"] == 100.0

    def test_event_data_for_hash_deterministic(self):
        event = self._make_event()
        h1 = event._event_data_for_hash()
        h2 = event._event_data_for_hash()
        assert h1 == h2

    def test_event_data_for_hash_includes_metadata_sorted(self):
        event = self._make_event(metadata={"z": 1, "a": 2})
        data = event._event_data_for_hash()
        # json.dumps(sort_keys=True) should put "a" before "z"
        assert '"a": 2' in data
        assert '"z": 1' in data
        assert data.index('"a"') < data.index('"z"')

    def test_event_data_for_hash_no_metadata_no_json(self):
        event = AuditEvent(
            event_type=AuditEventType.PIPELINE_STARTED,
            pipeline_run_id=RUN_ID,
            timestamp="T0",
        )
        data = event._event_data_for_hash()
        assert data == "pipeline.started|run-phase5-001|T0"

    def test_event_data_for_hash_with_agent_and_stage(self):
        event = AuditEvent(
            event_type=AuditEventType.STAGE_STARTED,
            pipeline_run_id=RUN_ID,
            timestamp="T1",
            agent_name="navya",
            stage="logic_review",
        )
        data = event._event_data_for_hash()
        assert "navya" in data
        assert "logic_review" in data


# ════════════════════════════════════════════════════════════════════
# Section 3 — _compute_chain_hash helper
# ════════════════════════════════════════════════════════════════════


class TestComputeChainHash:
    """Verify the low-level SHA-256 chain hash function."""

    def test_returns_64_char_hex(self):
        h = _compute_chain_hash("prev", "data")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        h1 = _compute_chain_hash("a", "b")
        h2 = _compute_chain_hash("a", "b")
        assert h1 == h2

    def test_different_prev_different_hash(self):
        h1 = _compute_chain_hash("a", "data")
        h2 = _compute_chain_hash("b", "data")
        assert h1 != h2

    def test_different_data_different_hash(self):
        h1 = _compute_chain_hash("prev", "data1")
        h2 = _compute_chain_hash("prev", "data2")
        assert h1 != h2

    def test_matches_manual_sha256(self):
        prev, data = "prev_hash", "event_data"
        expected = hashlib.sha256(f"{prev}:{data}".encode("utf-8")).hexdigest()
        assert _compute_chain_hash(prev, data) == expected

    def test_genesis_hash_is_64_zeros(self):
        assert GENESIS_HASH == "0" * 64
        assert len(GENESIS_HASH) == 64

    def test_unicode_input(self):
        h = _compute_chain_hash("prev", "日本語テスト")
        assert len(h) == 64

    def test_empty_strings(self):
        h = _compute_chain_hash("", "")
        assert len(h) == 64


# ════════════════════════════════════════════════════════════════════
# Section 4 — record() method and chain hash integration
# ════════════════════════════════════════════════════════════════════


class TestRecordAndChainHash:
    """Verify that record() builds events with correct chain hashes."""

    def test_first_event_uses_genesis_hash(self, audit: PipelineAuditService):
        event = audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        # First event's chain_hash should be sha256(GENESIS_HASH + event_data)
        assert event.chain_hash != ""
        assert len(event.chain_hash) == 64

    def test_chain_hash_changes_per_event(self, audit: PipelineAuditService):
        e1 = audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        e2 = audit.record(AuditEventType.STAGE_STARTED, RUN_ID, agent_name="shubham", stage="gen")
        assert e1.chain_hash != e2.chain_hash

    def test_chain_hash_depends_on_previous(self, audit: PipelineAuditService):
        e1 = audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        e2 = audit.record(AuditEventType.PIPELINE_COMPLETED, RUN_ID)
        # Manually verify e2's hash
        expected = _compute_chain_hash(e1.chain_hash, e2._event_data_for_hash())
        assert e2.chain_hash == expected

    def test_get_chain_hash_returns_latest(self, audit: PipelineAuditService):
        assert audit.get_chain_hash(RUN_ID) == GENESIS_HASH
        e1 = audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        assert audit.get_chain_hash(RUN_ID) == e1.chain_hash
        e2 = audit.record(AuditEventType.PIPELINE_COMPLETED, RUN_ID)
        assert audit.get_chain_hash(RUN_ID) == e2.chain_hash

    def test_multi_pipeline_isolation(self, audit: PipelineAuditService):
        e1 = audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        e2 = audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID_2)
        # Different pipelines should independently chain from genesis
        assert audit.get_chain_hash(RUN_ID) == e1.chain_hash
        assert audit.get_chain_hash(RUN_ID_2) == e2.chain_hash
        # They should NOT be equal (different pipeline_run_ids in event_data)
        assert e1.chain_hash != e2.chain_hash

    def test_record_stores_event(self, audit: PipelineAuditService):
        audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        events = audit.get_events(RUN_ID)
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.PIPELINE_STARTED

    def test_record_preserves_metadata(self, audit: PipelineAuditService):
        event = audit.record(
            AuditEventType.AI_PROMPT_SENT,
            RUN_ID,
            agent_name="shubham",
            metadata={"model": "sonnet", "tokens": 500},
        )
        assert event.metadata["model"] == "sonnet"
        assert event.metadata["tokens"] == 500
        assert event.agent_name == "shubham"

    def test_record_sets_iso_timestamp(self, audit: PipelineAuditService):
        event = audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        # Should be parseable ISO format
        dt = datetime.fromisoformat(event.timestamp)
        assert dt.tzinfo is not None  # UTC

    def test_record_empty_metadata_becomes_empty_dict(self, audit: PipelineAuditService):
        event = audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        assert event.metadata == {}

    def test_record_none_metadata_becomes_empty_dict(self, audit: PipelineAuditService):
        event = audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID, metadata=None)
        assert event.metadata == {}


# ════════════════════════════════════════════════════════════════════
# Section 5 — Chain verification
# ════════════════════════════════════════════════════════════════════


class TestChainVerification:
    """Verify audit chain integrity checking."""

    def test_empty_chain_is_valid(self, audit: PipelineAuditService):
        assert audit.verify_audit_chain(RUN_ID) is True

    def test_single_event_chain_valid(self, audit: PipelineAuditService):
        audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        assert audit.verify_audit_chain(RUN_ID) is True

    def test_multi_event_chain_valid(self, audit: PipelineAuditService):
        audit.record(AuditEventType.PIPELINE_CREATED, RUN_ID)
        audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        audit.record(AuditEventType.STAGE_STARTED, RUN_ID, agent_name="shubham", stage="gen")
        audit.record(AuditEventType.STAGE_COMPLETED, RUN_ID, agent_name="shubham", stage="gen")
        audit.record(AuditEventType.PIPELINE_COMPLETED, RUN_ID)
        assert audit.verify_audit_chain(RUN_ID) is True

    def test_tampered_event_detected(self, audit: PipelineAuditService):
        audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        audit.record(AuditEventType.PIPELINE_COMPLETED, RUN_ID)

        # Tamper: replace the second event with a different chain_hash
        events = audit._events[RUN_ID]
        original = events[1]
        tampered = AuditEvent(
            event_type=original.event_type,
            pipeline_run_id=original.pipeline_run_id,
            timestamp=original.timestamp,
            agent_name=original.agent_name,
            stage=original.stage,
            duration_ms=original.duration_ms,
            metadata=original.metadata,
            chain_hash="deadbeef" * 8,  # Fake hash
        )
        events[1] = tampered

        assert audit.verify_audit_chain(RUN_ID) is False

    def test_tampered_first_event_detected(self, audit: PipelineAuditService):
        audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        audit.record(AuditEventType.PIPELINE_COMPLETED, RUN_ID)

        # Tamper first event
        events = audit._events[RUN_ID]
        original = events[0]
        tampered = AuditEvent(
            event_type=original.event_type,
            pipeline_run_id=original.pipeline_run_id,
            timestamp=original.timestamp,
            chain_hash="0000" * 16,
        )
        events[0] = tampered

        assert audit.verify_audit_chain(RUN_ID) is False

    def test_deleted_event_breaks_chain(self, audit: PipelineAuditService):
        audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        audit.record(AuditEventType.STAGE_STARTED, RUN_ID, agent_name="a", stage="s")
        audit.record(AuditEventType.PIPELINE_COMPLETED, RUN_ID)

        # Delete middle event
        del audit._events[RUN_ID][1]
        assert audit.verify_audit_chain(RUN_ID) is False

    def test_inserted_event_breaks_chain(self, audit: PipelineAuditService):
        audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        audit.record(AuditEventType.PIPELINE_COMPLETED, RUN_ID)

        # Insert a bogus event in the middle
        fake = AuditEvent(
            event_type=AuditEventType.STAGE_STARTED,
            pipeline_run_id=RUN_ID,
            timestamp="2026-01-01T00:00:00+00:00",
            chain_hash="abcd" * 16,
        )
        audit._events[RUN_ID].insert(1, fake)
        assert audit.verify_audit_chain(RUN_ID) is False

    def test_chain_valid_across_many_events(self, audit: PipelineAuditService):
        """100 events in sequence should still verify."""
        for i in range(100):
            audit.record(
                AuditEventType.STAGE_STARTED,
                RUN_ID,
                agent_name=f"agent_{i}",
                stage=f"stage_{i}",
            )
        assert audit.verify_audit_chain(RUN_ID) is True
        assert audit.event_count(RUN_ID) == 100

    def test_two_pipelines_both_valid(self, audit: PipelineAuditService):
        audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID_2)
        audit.record(AuditEventType.PIPELINE_COMPLETED, RUN_ID)
        audit.record(AuditEventType.PIPELINE_COMPLETED, RUN_ID_2)
        assert audit.verify_audit_chain(RUN_ID) is True
        assert audit.verify_audit_chain(RUN_ID_2) is True


# ════════════════════════════════════════════════════════════════════
# Section 6 — AI Prompt/Response Logging (Gaps 206-207)
# ════════════════════════════════════════════════════════════════════


class TestAILogging:
    """Test AI prompt/response/error audit methods."""

    def test_ai_prompt_sent(self, audit: PipelineAuditService):
        event = audit.ai_prompt_sent(
            pipeline_run_id=RUN_ID,
            agent_name="shubham",
            model="claude-sonnet-4-20250514",
            prompt_hash="abc123",
            token_count=1500,
        )
        assert event.event_type == AuditEventType.AI_PROMPT_SENT
        assert event.agent_name == "shubham"
        assert event.metadata["model"] == "claude-sonnet-4-20250514"
        assert event.metadata["prompt_hash"] == "abc123"
        assert event.metadata["input_tokens"] == 1500
        assert event.chain_hash != ""

    def test_ai_response_received(self, audit: PipelineAuditService):
        event = audit.ai_response_received(
            pipeline_run_id=RUN_ID,
            agent_name="shubham",
            model="claude-sonnet-4-20250514",
            response_hash="def456",
            input_tokens=1500,
            output_tokens=3000,
            latency_ms=2345.6,
            cost=0.045,
        )
        assert event.event_type == AuditEventType.AI_RESPONSE_RECEIVED
        assert event.duration_ms == 2345.6
        assert event.metadata["response_hash"] == "def456"
        assert event.metadata["input_tokens"] == 1500
        assert event.metadata["output_tokens"] == 3000
        assert event.metadata["cost_usd"] == 0.045

    def test_ai_response_cost_rounds_to_6_decimals(self, audit: PipelineAuditService):
        event = audit.ai_response_received(
            pipeline_run_id=RUN_ID,
            agent_name="a",
            model="m",
            response_hash="h",
            input_tokens=100,
            output_tokens=200,
            latency_ms=100.0,
            cost=0.1234567890,
        )
        assert event.metadata["cost_usd"] == 0.123457  # rounded

    def test_ai_error(self, audit: PipelineAuditService):
        event = audit.ai_error(
            pipeline_run_id=RUN_ID,
            agent_name="navya",
            model="claude-sonnet-4-20250514",
            error="Rate limit exceeded (429)",
        )
        assert event.event_type == AuditEventType.AI_ERROR
        assert event.agent_name == "navya"
        assert event.metadata["error"] == "Rate limit exceeded (429)"

    def test_ai_error_truncates_long_error(self, audit: PipelineAuditService):
        long_error = "x" * 1000
        event = audit.ai_error(
            pipeline_run_id=RUN_ID,
            agent_name="a",
            model="m",
            error=long_error,
        )
        assert len(event.metadata["error"]) == 500

    def test_ai_prompt_empty_hash(self, audit: PipelineAuditService):
        event = audit.ai_prompt_sent(
            pipeline_run_id=RUN_ID,
            agent_name="a",
            model="m",
            prompt_hash="",
            token_count=0,
        )
        assert event.metadata["prompt_hash"] == ""
        assert event.metadata["input_tokens"] == 0


# ════════════════════════════════════════════════════════════════════
# Section 7 — Code Generation Audit (Gap 209)
# ════════════════════════════════════════════════════════════════════


class TestCodeGenerationAudit:
    """Test code generation and code fix audit methods."""

    def test_code_generated(self, audit: PipelineAuditService):
        event = audit.code_generated(
            pipeline_run_id=RUN_ID,
            agent_name="shubham",
            file_path="backend/app/models/user.py",
            content_hash="sha256abc",
            line_count=120,
        )
        assert event.event_type == AuditEventType.CODE_GENERATED
        assert event.agent_name == "shubham"
        assert event.metadata["file_path"] == "backend/app/models/user.py"
        assert event.metadata["content_hash"] == "sha256abc"
        assert event.metadata["line_count"] == 120

    def test_code_fixed(self, audit: PipelineAuditService):
        event = audit.code_fixed(
            pipeline_run_id=RUN_ID,
            file_path="backend/app/routes/auth.py",
            before_hash="aaa",
            after_hash="bbb",
            fix_reason="Missing import for HTTPException",
        )
        assert event.event_type == AuditEventType.CODE_FIXED
        assert event.agent_name == "fixer"  # Always "fixer"
        assert event.metadata["before_hash"] == "aaa"
        assert event.metadata["after_hash"] == "bbb"
        assert "Missing import" in event.metadata["fix_reason"]

    def test_code_fixed_truncates_reason(self, audit: PipelineAuditService):
        long_reason = "r" * 500
        event = audit.code_fixed(
            pipeline_run_id=RUN_ID,
            file_path="f.py",
            before_hash="a",
            after_hash="b",
            fix_reason=long_reason,
        )
        assert len(event.metadata["fix_reason"]) == 200

    def test_code_generated_zero_lines(self, audit: PipelineAuditService):
        event = audit.code_generated(
            pipeline_run_id=RUN_ID,
            agent_name="a",
            file_path="empty.py",
            content_hash="h",
            line_count=0,
        )
        assert event.metadata["line_count"] == 0

    def test_code_generated_unicode_path(self, audit: PipelineAuditService):
        event = audit.code_generated(
            pipeline_run_id=RUN_ID,
            agent_name="a",
            file_path="프로젝트/모델.py",
            content_hash="h",
            line_count=10,
        )
        assert event.metadata["file_path"] == "프로젝트/모델.py"


# ════════════════════════════════════════════════════════════════════
# Section 8 — Test Result Logging (Gap 210)
# ════════════════════════════════════════════════════════════════════


class TestResultLogging:
    """Test pass/fail audit methods."""

    def test_test_passed(self, audit: PipelineAuditService):
        event = audit.test_passed(
            pipeline_run_id=RUN_ID,
            test_name="test_user_creation",
            phase="unit",
            duration_ms=45.2,
        )
        assert event.event_type == AuditEventType.TEST_PASSED
        assert event.agent_name == "aarav"
        assert event.stage == "unit"
        assert event.duration_ms == 45.2
        assert event.metadata["test_name"] == "test_user_creation"

    def test_test_failed(self, audit: PipelineAuditService):
        event = audit.test_failed(
            pipeline_run_id=RUN_ID,
            test_name="test_login_endpoint",
            phase="integration",
            error_details="AssertionError: expected 200, got 401",
        )
        assert event.event_type == AuditEventType.TEST_FAILED
        assert event.agent_name == "aarav"
        assert event.stage == "integration"
        assert "AssertionError" in event.metadata["error"]

    def test_test_failed_truncates_error(self, audit: PipelineAuditService):
        long_error = "E" * 1000
        event = audit.test_failed(
            pipeline_run_id=RUN_ID,
            test_name="t",
            phase="unit",
            error_details=long_error,
        )
        assert len(event.metadata["error"]) == 500

    def test_test_passed_zero_duration(self, audit: PipelineAuditService):
        event = audit.test_passed(
            pipeline_run_id=RUN_ID,
            test_name="t",
            phase="unit",
        )
        assert event.duration_ms == 0.0

    def test_test_passed_special_chars_in_name(self, audit: PipelineAuditService):
        event = audit.test_passed(
            pipeline_run_id=RUN_ID,
            test_name="test[parametrize-0-<user@example.com>]",
            phase="unit",
        )
        assert "parametrize" in event.metadata["test_name"]


# ════════════════════════════════════════════════════════════════════
# Section 9 — Security Scan Logging (Gap 211)
# ════════════════════════════════════════════════════════════════════


class TestSecurityScanLogging:
    """Test security scan start/finding/complete audit methods."""

    def test_security_scan_started(self, audit: PipelineAuditService):
        event = audit.security_scan_started(
            pipeline_run_id=RUN_ID,
            scan_type="owasp_top10",
        )
        assert event.event_type == AuditEventType.SECURITY_SCAN_STARTED
        assert event.agent_name == "karan"
        assert event.metadata["scan_type"] == "owasp_top10"

    def test_security_finding(self, audit: PipelineAuditService):
        event = audit.security_finding(
            pipeline_run_id=RUN_ID,
            severity="critical",
            category="sql_injection",
            file_path="app/routes/users.py",
            message="Raw SQL query uses string formatting instead of parameters",
        )
        assert event.event_type == AuditEventType.SECURITY_FINDING
        assert event.agent_name == "karan"
        assert event.metadata["severity"] == "critical"
        assert event.metadata["category"] == "sql_injection"
        assert event.metadata["file_path"] == "app/routes/users.py"
        assert "Raw SQL" in event.metadata["message"]

    def test_security_finding_truncates_message(self, audit: PipelineAuditService):
        long_msg = "M" * 600
        event = audit.security_finding(
            pipeline_run_id=RUN_ID,
            severity="low",
            category="info",
            file_path="f.py",
            message=long_msg,
        )
        assert len(event.metadata["message"]) == 300

    def test_security_scan_completed(self, audit: PipelineAuditService):
        event = audit.security_scan_completed(
            pipeline_run_id=RUN_ID,
            scan_type="dependency_audit",
            findings_count=12,
            critical_count=2,
        )
        assert event.event_type == AuditEventType.SECURITY_SCAN_COMPLETED
        assert event.metadata["scan_type"] == "dependency_audit"
        assert event.metadata["findings_count"] == 12
        assert event.metadata["critical_count"] == 2

    def test_security_scan_completed_zero_findings(self, audit: PipelineAuditService):
        event = audit.security_scan_completed(
            pipeline_run_id=RUN_ID,
            scan_type="xss",
            findings_count=0,
            critical_count=0,
        )
        assert event.metadata["findings_count"] == 0

    def test_security_scan_full_lifecycle(self, audit: PipelineAuditService):
        """Start → finding → finding → complete lifecycle."""
        audit.security_scan_started(RUN_ID, "full_scan")
        audit.security_finding(RUN_ID, "high", "xss", "f1.py", "XSS via innerHTML")
        audit.security_finding(RUN_ID, "medium", "cors", "f2.py", "Wildcard CORS")
        audit.security_scan_completed(RUN_ID, "full_scan", 2, 0)

        events = audit.get_events_by_type(RUN_ID, AuditEventType.SECURITY_FINDING)
        assert len(events) == 2

        completed = audit.get_events_by_type(RUN_ID, AuditEventType.SECURITY_SCAN_COMPLETED)
        assert len(completed) == 1
        assert completed[0].metadata["findings_count"] == 2


# ════════════════════════════════════════════════════════════════════
# Section 10 — User Decision Logging (Gap 213)
# ════════════════════════════════════════════════════════════════════


class TestUserDecisionLogging:
    """Test user approve/reject/feedback audit methods."""

    def test_user_approved(self, audit: PipelineAuditService):
        event = audit.user_approved(
            pipeline_run_id=RUN_ID,
            user_id="usr_123",
            stage="checkpoint_1",
            comments="Looks good to me!",
        )
        assert event.event_type == AuditEventType.USER_APPROVED
        assert event.stage == "checkpoint_1"
        assert event.metadata["user_id"] == "usr_123"
        assert event.metadata["comments"] == "Looks good to me!"

    def test_user_approved_no_comments(self, audit: PipelineAuditService):
        event = audit.user_approved(
            pipeline_run_id=RUN_ID,
            user_id="usr_123",
            stage="cp1",
        )
        assert event.metadata["comments"] == ""

    def test_user_rejected(self, audit: PipelineAuditService):
        event = audit.user_rejected(
            pipeline_run_id=RUN_ID,
            user_id="usr_456",
            stage="checkpoint_2",
            reason="Missing error handling in auth routes",
        )
        assert event.event_type == AuditEventType.USER_REJECTED
        assert event.metadata["user_id"] == "usr_456"
        assert "Missing error handling" in event.metadata["reason"]

    def test_user_rejected_truncates_reason(self, audit: PipelineAuditService):
        long_reason = "R" * 1000
        event = audit.user_rejected(
            pipeline_run_id=RUN_ID,
            user_id="u",
            stage="cp",
            reason=long_reason,
        )
        assert len(event.metadata["reason"]) == 500

    def test_user_feedback_with_rating(self, audit: PipelineAuditService):
        event = audit.user_feedback(
            pipeline_run_id=RUN_ID,
            user_id="usr_789",
            feedback_text="Great code quality but needs more tests",
            rating=4,
        )
        assert event.event_type == AuditEventType.USER_FEEDBACK
        assert event.metadata["user_id"] == "usr_789"
        assert "Great code quality" in event.metadata["feedback"]
        assert event.metadata["rating"] == 4

    def test_user_feedback_no_rating(self, audit: PipelineAuditService):
        event = audit.user_feedback(
            pipeline_run_id=RUN_ID,
            user_id="u",
            feedback_text="LGTM",
        )
        assert "rating" not in event.metadata

    def test_user_feedback_truncates(self, audit: PipelineAuditService):
        long_fb = "F" * 2000
        event = audit.user_feedback(
            pipeline_run_id=RUN_ID,
            user_id="u",
            feedback_text=long_fb,
        )
        assert len(event.metadata["feedback"]) == 1000

    def test_user_approved_truncates_comments(self, audit: PipelineAuditService):
        long_comments = "C" * 1000
        event = audit.user_approved(
            pipeline_run_id=RUN_ID,
            user_id="u",
            stage="cp",
            comments=long_comments,
        )
        assert len(event.metadata["comments"]) == 500


# ════════════════════════════════════════════════════════════════════
# Section 11 — Agent Decision Logging (Gap 214)
# ════════════════════════════════════════════════════════════════════


class TestAgentDecisionLogging:
    """Test agent decision audit method."""

    def test_agent_decision_basic(self, audit: PipelineAuditService):
        event = audit.agent_decision(
            pipeline_run_id=RUN_ID,
            agent_name="navya",
            decision="Split service into 3 files",
            reasoning="Estimated >500 lines per service, exceeds single-file limit",
        )
        assert event.event_type == AuditEventType.AGENT_DECISION
        assert event.agent_name == "navya"
        assert event.metadata["decision"] == "Split service into 3 files"
        assert "500 lines" in event.metadata["reasoning"]

    def test_agent_decision_with_alternatives(self, audit: PipelineAuditService):
        event = audit.agent_decision(
            pipeline_run_id=RUN_ID,
            agent_name="shubham",
            decision="Use SQLAlchemy ORM",
            reasoning="Project specifies PostgreSQL with complex relations",
            alternatives=["Raw SQL queries", "SQLModel", "Tortoise ORM"],
        )
        assert event.metadata["alternatives"] == [
            "Raw SQL queries",
            "SQLModel",
            "Tortoise ORM",
        ]

    def test_agent_decision_no_alternatives(self, audit: PipelineAuditService):
        event = audit.agent_decision(
            pipeline_run_id=RUN_ID,
            agent_name="a",
            decision="d",
            reasoning="r",
        )
        assert "alternatives" not in event.metadata

    def test_agent_decision_truncates_decision(self, audit: PipelineAuditService):
        event = audit.agent_decision(
            pipeline_run_id=RUN_ID,
            agent_name="a",
            decision="D" * 500,
            reasoning="r",
        )
        assert len(event.metadata["decision"]) == 200

    def test_agent_decision_truncates_reasoning(self, audit: PipelineAuditService):
        event = audit.agent_decision(
            pipeline_run_id=RUN_ID,
            agent_name="a",
            decision="d",
            reasoning="R" * 1000,
        )
        assert len(event.metadata["reasoning"]) == 500

    def test_agent_decision_truncates_alternatives(self, audit: PipelineAuditService):
        event = audit.agent_decision(
            pipeline_run_id=RUN_ID,
            agent_name="a",
            decision="d",
            reasoning="r",
            alternatives=["A" * 200 for _ in range(10)],
        )
        # Max 5 alternatives, each max 100 chars
        alts = event.metadata["alternatives"]
        assert len(alts) == 5
        for alt in alts:
            assert len(alt) == 100

    def test_agent_decision_empty_alternatives_list(self, audit: PipelineAuditService):
        event = audit.agent_decision(
            pipeline_run_id=RUN_ID,
            agent_name="a",
            decision="d",
            reasoning="r",
            alternatives=[],
        )
        # Empty list is falsy, so should not be included
        assert "alternatives" not in event.metadata


# ════════════════════════════════════════════════════════════════════
# Section 12 — Cost Tracking (Gap 216)
# ════════════════════════════════════════════════════════════════════


class TestCostTracking:
    """Test cost recording audit method."""

    def test_cost_recorded(self, audit: PipelineAuditService):
        event = audit.cost_recorded(
            pipeline_run_id=RUN_ID,
            agent_name="shubham",
            model="claude-sonnet-4-20250514",
            input_tokens=2000,
            output_tokens=8000,
            cost_usd=0.075,
        )
        assert event.event_type == AuditEventType.COST_RECORDED
        assert event.agent_name == "shubham"
        assert event.metadata["model"] == "claude-sonnet-4-20250514"
        assert event.metadata["input_tokens"] == 2000
        assert event.metadata["output_tokens"] == 8000
        assert event.metadata["cost_usd"] == 0.075

    def test_cost_rounds_to_6_decimals(self, audit: PipelineAuditService):
        event = audit.cost_recorded(
            pipeline_run_id=RUN_ID,
            agent_name="a",
            model="m",
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.00000123456789,
        )
        assert event.metadata["cost_usd"] == 0.000001

    def test_cost_zero(self, audit: PipelineAuditService):
        event = audit.cost_recorded(
            pipeline_run_id=RUN_ID,
            agent_name="a",
            model="m",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )
        assert event.metadata["cost_usd"] == 0.0
        assert event.metadata["input_tokens"] == 0

    def test_multiple_costs_aggregate(self, audit: PipelineAuditService):
        """Multiple cost events per pipeline should all be recorded."""
        audit.cost_recorded(RUN_ID, "shubham", "sonnet", 1000, 2000, 0.01)
        audit.cost_recorded(RUN_ID, "aarav", "sonnet", 500, 1000, 0.005)
        audit.cost_recorded(RUN_ID, "karan", "haiku", 200, 300, 0.001)

        cost_events = audit.get_events_by_type(RUN_ID, AuditEventType.COST_RECORDED)
        assert len(cost_events) == 3
        total = sum(e.metadata["cost_usd"] for e in cost_events)
        assert abs(total - 0.016) < 0.0001


# ════════════════════════════════════════════════════════════════════
# Section 13 — Query Methods
# ════════════════════════════════════════════════════════════════════


class TestQueryMethods:
    """Test get_events, get_events_by_type, get_timeline, event_count."""

    def test_get_events_empty(self, audit: PipelineAuditService):
        assert audit.get_events("nonexistent") == []

    def test_get_events_returns_copy(self, audit: PipelineAuditService):
        audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        events = audit.get_events(RUN_ID)
        events.clear()  # Mutating returned list
        assert audit.event_count(RUN_ID) == 1  # Original unaffected

    def test_get_events_by_type_filters_correctly(self, audit: PipelineAuditService):
        audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        audit.record(AuditEventType.STAGE_STARTED, RUN_ID, agent_name="a", stage="s")
        audit.record(AuditEventType.STAGE_COMPLETED, RUN_ID, agent_name="a", stage="s")
        audit.record(AuditEventType.PIPELINE_COMPLETED, RUN_ID)

        stages = audit.get_events_by_type(RUN_ID, AuditEventType.STAGE_STARTED)
        assert len(stages) == 1
        assert stages[0].event_type == AuditEventType.STAGE_STARTED

    def test_get_events_by_type_empty(self, audit: PipelineAuditService):
        audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        result = audit.get_events_by_type(RUN_ID, AuditEventType.AI_PROMPT_SENT)
        assert result == []

    def test_get_timeline_serializable(self, audit: PipelineAuditService):
        audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        audit.record(AuditEventType.PIPELINE_COMPLETED, RUN_ID)
        timeline = audit.get_timeline(RUN_ID)
        assert len(timeline) == 2
        # Should be JSON-serializable
        json_str = json.dumps(timeline)
        assert isinstance(json_str, str)

    def test_get_timeline_empty(self, audit: PipelineAuditService):
        assert audit.get_timeline("nonexistent") == []

    def test_event_count(self, audit: PipelineAuditService):
        assert audit.event_count(RUN_ID) == 0
        audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        assert audit.event_count(RUN_ID) == 1
        audit.record(AuditEventType.PIPELINE_COMPLETED, RUN_ID)
        assert audit.event_count(RUN_ID) == 2

    def test_event_count_nonexistent(self, audit: PipelineAuditService):
        assert audit.event_count("nope") == 0


# ════════════════════════════════════════════════════════════════════
# Section 14 — Original Methods (pre-Phase 5) still work
# ════════════════════════════════════════════════════════════════════


class TestOriginalMethods:
    """Ensure pre-Phase-5 methods still work with chain hashing."""

    def test_pipeline_created(self, audit: PipelineAuditService):
        event = audit.pipeline_created(
            RUN_ID, "proj_1", "org_1", "usr_1", "auto",
        )
        assert event.event_type == AuditEventType.PIPELINE_CREATED
        assert event.metadata["project_id"] == "proj_1"
        assert event.metadata["execution_mode"] == "auto"
        assert event.chain_hash != ""

    def test_pipeline_started(self, audit: PipelineAuditService):
        event = audit.pipeline_started(RUN_ID)
        assert event.event_type == AuditEventType.PIPELINE_STARTED

    def test_pipeline_completed(self, audit: PipelineAuditService):
        audit.pipeline_created(RUN_ID, "p", "o", "u", "auto")
        event = audit.pipeline_completed(RUN_ID)
        assert event.event_type == AuditEventType.PIPELINE_COMPLETED

    def test_pipeline_failed(self, audit: PipelineAuditService):
        event = audit.pipeline_failed(RUN_ID, "Out of memory")
        assert event.event_type == AuditEventType.PIPELINE_FAILED
        assert event.metadata["error"] == "Out of memory"

    def test_pipeline_failed_truncates_error(self, audit: PipelineAuditService):
        long_err = "E" * 1000
        event = audit.pipeline_failed(RUN_ID, long_err)
        assert len(event.metadata["error"]) == 500

    def test_stage_started(self, audit: PipelineAuditService):
        event = audit.stage_started(RUN_ID, "backend_gen", "shubham")
        assert event.stage == "backend_gen"
        assert event.agent_name == "shubham"

    def test_stage_completed(self, audit: PipelineAuditService):
        event = audit.stage_completed(RUN_ID, "backend_gen", "shubham", 5000.0)
        assert event.duration_ms == 5000.0

    def test_stage_failed(self, audit: PipelineAuditService):
        event = audit.stage_failed(RUN_ID, "testing", "aarav", "Tests failed")
        assert event.metadata["error"] == "Tests failed"

    def test_checkpoint_paused(self, audit: PipelineAuditService):
        event = audit.checkpoint_paused(RUN_ID, "checkpoint_1")
        assert event.event_type == AuditEventType.CHECKPOINT_PAUSED

    def test_checkpoint_approved(self, audit: PipelineAuditService):
        event = audit.checkpoint_approved(RUN_ID, "checkpoint_1", "usr_1")
        assert event.metadata["approved_by"] == "usr_1"

    def test_checkpoint_rejected(self, audit: PipelineAuditService):
        event = audit.checkpoint_rejected(RUN_ID, "checkpoint_1", "usr_1", "Bad code")
        assert event.metadata["rejected_by"] == "usr_1"
        assert event.metadata["reason"] == "Bad code"

    def test_deploy_started(self, audit: PipelineAuditService):
        event = audit.deploy_started(RUN_ID, "vercel")
        assert event.agent_name == "pranav"
        assert event.metadata["provider"] == "vercel"

    def test_deploy_completed(self, audit: PipelineAuditService):
        event = audit.deploy_completed(RUN_ID, "https://app.vercel.app", "vercel")
        assert event.metadata["deployment_url"] == "https://app.vercel.app"

    def test_deploy_failed(self, audit: PipelineAuditService):
        event = audit.deploy_failed(RUN_ID, "Build failed: exit code 1")
        assert "Build failed" in event.metadata["error"]

    def test_delivery_created(self, audit: PipelineAuditService):
        event = audit.delivery_created(RUN_ID, 45, 1200)
        assert event.metadata["total_files"] == 45
        assert event.metadata["zip_size_kb"] == 1200


# ════════════════════════════════════════════════════════════════════
# Section 15 — Full Pipeline Lifecycle with Chain Verification
# ════════════════════════════════════════════════════════════════════


class TestFullLifecycle:
    """End-to-end pipeline audit trail with chain verification."""

    def test_full_pipeline_lifecycle(self, audit: PipelineAuditService):
        """Simulate a full pipeline run with all new event types."""
        # 1. Pipeline creation
        audit.pipeline_created(RUN_ID, "proj_1", "org_1", "usr_1", "auto")
        audit.pipeline_started(RUN_ID)

        # 2. Backend generation with AI logging
        audit.stage_started(RUN_ID, "backend_gen", "shubham")
        audit.ai_prompt_sent(RUN_ID, "shubham", "sonnet", "hash_p1", 2000)
        audit.ai_response_received(RUN_ID, "shubham", "sonnet", "hash_r1", 2000, 5000, 3200.0, 0.05)
        audit.code_generated(RUN_ID, "shubham", "models/user.py", "ch1", 80)
        audit.code_generated(RUN_ID, "shubham", "routes/auth.py", "ch2", 120)
        audit.agent_decision(RUN_ID, "shubham", "Use JWT auth", "Contract specifies token-based auth")
        audit.cost_recorded(RUN_ID, "shubham", "sonnet", 2000, 5000, 0.05)
        audit.stage_completed(RUN_ID, "backend_gen", "shubham", 15000.0)

        # 3. Testing
        audit.stage_started(RUN_ID, "testing", "aarav")
        audit.test_passed(RUN_ID, "test_create_user", "unit", 12.3)
        audit.test_passed(RUN_ID, "test_login", "unit", 8.1)
        audit.test_failed(RUN_ID, "test_auth_middleware", "unit", "Missing token handler")
        audit.stage_completed(RUN_ID, "testing", "aarav", 5000.0)

        # 4. Fix cycle
        audit.code_fixed(RUN_ID, "routes/auth.py", "ch2", "ch2_fixed", "Add token handler")

        # 5. Security scan
        audit.stage_started(RUN_ID, "security_review", "karan")
        audit.security_scan_started(RUN_ID, "owasp_top10")
        audit.security_finding(RUN_ID, "medium", "cors", "main.py", "Wildcard CORS origin")
        audit.security_scan_completed(RUN_ID, "owasp_top10", 1, 0)
        audit.stage_completed(RUN_ID, "security_review", "karan", 3000.0)

        # 6. User checkpoint
        audit.checkpoint_paused(RUN_ID, "checkpoint_1")
        audit.user_approved(RUN_ID, "usr_1", "checkpoint_1", "LGTM")
        audit.user_feedback(RUN_ID, "usr_1", "Good progress, add more tests", 4)
        audit.checkpoint_approved(RUN_ID, "checkpoint_1", "usr_1")

        # 7. Pipeline completes
        audit.pipeline_completed(RUN_ID)

        # Verify chain integrity
        assert audit.verify_audit_chain(RUN_ID) is True

        # Verify event counts (26 events recorded above)
        total = audit.event_count(RUN_ID)
        assert total == 26

        # Verify timeline is serializable
        timeline = audit.get_timeline(RUN_ID)
        assert len(timeline) == 26
        json_str = json.dumps(timeline)
        assert len(json_str) > 0

        # Verify specific event type queries
        ai_prompts = audit.get_events_by_type(RUN_ID, AuditEventType.AI_PROMPT_SENT)
        assert len(ai_prompts) == 1
        code_events = audit.get_events_by_type(RUN_ID, AuditEventType.CODE_GENERATED)
        assert len(code_events) == 2
        test_passes = audit.get_events_by_type(RUN_ID, AuditEventType.TEST_PASSED)
        assert len(test_passes) == 2
        test_fails = audit.get_events_by_type(RUN_ID, AuditEventType.TEST_FAILED)
        assert len(test_fails) == 1

    def test_failed_pipeline_lifecycle(self, audit: PipelineAuditService):
        """Pipeline that fails with AI error."""
        audit.pipeline_created(RUN_ID, "proj_2", "org_1", "usr_1", "auto")
        audit.pipeline_started(RUN_ID)
        audit.stage_started(RUN_ID, "backend_gen", "shubham")
        audit.ai_prompt_sent(RUN_ID, "shubham", "sonnet", "h", 1000)
        audit.ai_error(RUN_ID, "shubham", "sonnet", "Rate limit exceeded")
        audit.stage_failed(RUN_ID, "backend_gen", "shubham", "AI call failed")
        audit.pipeline_failed(RUN_ID, "Backend generation failed")

        assert audit.verify_audit_chain(RUN_ID) is True
        assert audit.event_count(RUN_ID) == 7

        errors = audit.get_events_by_type(RUN_ID, AuditEventType.AI_ERROR)
        assert len(errors) == 1
        assert errors[0].metadata["error"] == "Rate limit exceeded"


# ════════════════════════════════════════════════════════════════════
# Section 16 — Singleton Factory
# ════════════════════════════════════════════════════════════════════


class TestSingletonFactory:
    """Test get_pipeline_audit() singleton."""

    def test_returns_same_instance(self):
        svc1 = get_pipeline_audit()
        svc2 = get_pipeline_audit()
        assert svc1 is svc2

    def test_is_pipeline_audit_service(self):
        svc = get_pipeline_audit()
        assert isinstance(svc, PipelineAuditService)

    def test_has_all_new_methods(self):
        svc = get_pipeline_audit()
        new_methods = [
            "ai_prompt_sent",
            "ai_response_received",
            "ai_error",
            "code_generated",
            "code_fixed",
            "test_passed",
            "test_failed",
            "security_scan_started",
            "security_finding",
            "security_scan_completed",
            "user_approved",
            "user_rejected",
            "user_feedback",
            "agent_decision",
            "cost_recorded",
            "verify_audit_chain",
            "get_chain_hash",
        ]
        for method_name in new_methods:
            assert hasattr(svc, method_name), f"Missing method: {method_name}"
            assert callable(getattr(svc, method_name)), f"Not callable: {method_name}"


# ════════════════════════════════════════════════════════════════════
# Section 17 — Edge Cases
# ════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge cases: unicode, empty strings, special chars, boundary values."""

    def test_unicode_in_metadata(self, audit: PipelineAuditService):
        event = audit.ai_prompt_sent(
            pipeline_run_id=RUN_ID,
            agent_name="日本語エージェント",
            model="模型",
            prompt_hash="ハッシュ",
            token_count=100,
        )
        assert event.agent_name == "日本語エージェント"
        assert audit.verify_audit_chain(RUN_ID) is True

    def test_special_chars_in_decision(self, audit: PipelineAuditService):
        event = audit.agent_decision(
            pipeline_run_id=RUN_ID,
            agent_name="a",
            decision='Use "quotes" & <brackets>',
            reasoning="Reasoning with 'apostrophes' & ampersands",
        )
        assert '"quotes"' in event.metadata["decision"]
        assert audit.verify_audit_chain(RUN_ID) is True

    def test_newlines_in_error(self, audit: PipelineAuditService):
        event = audit.ai_error(
            pipeline_run_id=RUN_ID,
            agent_name="a",
            model="m",
            error="Line 1\nLine 2\nLine 3",
        )
        assert "\n" in event.metadata["error"]

    def test_empty_pipeline_run_id(self, audit: PipelineAuditService):
        """Should work with empty run ID (degenerate but valid)."""
        event = audit.record(AuditEventType.PIPELINE_STARTED, "")
        assert event.pipeline_run_id == ""
        assert audit.verify_audit_chain("") is True

    def test_very_long_pipeline_run_id(self, audit: PipelineAuditService):
        long_id = "run_" + "x" * 1000
        event = audit.record(AuditEventType.PIPELINE_STARTED, long_id)
        assert event.pipeline_run_id == long_id
        assert audit.verify_audit_chain(long_id) is True

    def test_metadata_with_nested_dicts(self, audit: PipelineAuditService):
        event = audit.record(
            AuditEventType.AI_RESPONSE_RECEIVED,
            RUN_ID,
            metadata={
                "model": "sonnet",
                "config": {"temperature": 0.7, "max_tokens": 4096},
                "tags": ["gen", "backend"],
            },
        )
        assert event.metadata["config"]["temperature"] == 0.7
        assert event.metadata["tags"] == ["gen", "backend"]
        assert audit.verify_audit_chain(RUN_ID) is True

    def test_metadata_with_none_values(self, audit: PipelineAuditService):
        event = audit.record(
            AuditEventType.PIPELINE_STARTED,
            RUN_ID,
            metadata={"key": None, "other": "value"},
        )
        assert event.metadata["key"] is None

    def test_high_precision_duration(self, audit: PipelineAuditService):
        event = audit.record(
            AuditEventType.STAGE_COMPLETED,
            RUN_ID,
            duration_ms=0.000001,
        )
        assert event.duration_ms == 0.000001

    def test_negative_duration(self, audit: PipelineAuditService):
        """Negative durations shouldn't crash, but won't appear in to_dict."""
        event = audit.record(
            AuditEventType.STAGE_COMPLETED,
            RUN_ID,
            duration_ms=-1.0,
        )
        d = event.to_dict()
        # duration_ms > 0 check means negative won't appear
        assert "duration_ms" not in d

    def test_rapid_sequential_events_unique_hashes(self, audit: PipelineAuditService):
        """Events recorded rapidly should still have unique chain hashes."""
        hashes = set()
        for i in range(50):
            event = audit.record(
                AuditEventType.STAGE_STARTED,
                RUN_ID,
                agent_name=f"agent_{i}",
            )
            hashes.add(event.chain_hash)
        assert len(hashes) == 50  # All unique


# ════════════════════════════════════════════════════════════════════
# Section 18 — Chain Hash Determinism & Cross-Verification
# ════════════════════════════════════════════════════════════════════


class TestChainHashDeterminism:
    """Verify chain hashes are reproducible and cross-verifiable."""

    def test_manual_chain_verification(self, audit: PipelineAuditService):
        """Manually compute expected chain hashes and compare."""
        e1 = audit.record(AuditEventType.PIPELINE_STARTED, RUN_ID)
        e2 = audit.record(AuditEventType.PIPELINE_COMPLETED, RUN_ID)

        # Verify e1
        expected_h1 = _compute_chain_hash(GENESIS_HASH, e1._event_data_for_hash())
        assert e1.chain_hash == expected_h1

        # Verify e2
        expected_h2 = _compute_chain_hash(e1.chain_hash, e2._event_data_for_hash())
        assert e2.chain_hash == expected_h2

    def test_chain_hash_includes_event_type(self):
        """Same data but different event type → different hash."""
        ts = "2026-01-01T00:00:00+00:00"
        e1 = AuditEvent(
            event_type=AuditEventType.PIPELINE_STARTED,
            pipeline_run_id=RUN_ID,
            timestamp=ts,
        )
        e2 = AuditEvent(
            event_type=AuditEventType.PIPELINE_COMPLETED,
            pipeline_run_id=RUN_ID,
            timestamp=ts,
        )
        h1 = _compute_chain_hash(GENESIS_HASH, e1._event_data_for_hash())
        h2 = _compute_chain_hash(GENESIS_HASH, e2._event_data_for_hash())
        assert h1 != h2

    def test_chain_hash_includes_pipeline_run_id(self):
        """Same event type but different run ID → different hash."""
        ts = "2026-01-01T00:00:00+00:00"
        e1 = AuditEvent(
            event_type=AuditEventType.PIPELINE_STARTED,
            pipeline_run_id="run_A",
            timestamp=ts,
        )
        e2 = AuditEvent(
            event_type=AuditEventType.PIPELINE_STARTED,
            pipeline_run_id="run_B",
            timestamp=ts,
        )
        h1 = _compute_chain_hash(GENESIS_HASH, e1._event_data_for_hash())
        h2 = _compute_chain_hash(GENESIS_HASH, e2._event_data_for_hash())
        assert h1 != h2

    def test_chain_hash_includes_metadata(self):
        """Same event but different metadata → different hash."""
        ts = "2026-01-01T00:00:00+00:00"
        e1 = AuditEvent(
            event_type=AuditEventType.AI_PROMPT_SENT,
            pipeline_run_id=RUN_ID,
            timestamp=ts,
            metadata={"model": "sonnet"},
        )
        e2 = AuditEvent(
            event_type=AuditEventType.AI_PROMPT_SENT,
            pipeline_run_id=RUN_ID,
            timestamp=ts,
            metadata={"model": "haiku"},
        )
        h1 = _compute_chain_hash(GENESIS_HASH, e1._event_data_for_hash())
        h2 = _compute_chain_hash(GENESIS_HASH, e2._event_data_for_hash())
        assert h1 != h2
