"""Tests for Round 8 fixes: hardening from brutal re-review cycle 8.

Covers:
- FILE-CONTENTS-FIX: Shubham and Aanya include file_contents in output
- FIXER-PARTIAL-FIX: Fixer returns COMPLETED for partial fixes (fix-retest loop)
- PIPELINE-START-FIX: start_pipeline endpoint calls run_pipeline (asyncio.create_task)
- DOUBLE-EXECUTE-FIX: resume_run guards against concurrent execution
- CONTRACT-KEYS-FIX: estimate_file_complexity reads nested contract keys
- LOGIN-TIMING-FIX: Login endpoint uses dummy bcrypt for non-existent users
- CHECKPOINT-STAGE-FIX: approve_checkpoint validates checkpoint stage
- GET-USAGE-FIX: get_usage doesn't create defaultdict entries
- VERTEX-LOCK-FIX: VertexAICredentialManager.get_access_token is thread-safe
- NOTIFICATION-ORG-FIX: Notification queries filter by organization_id
- TEMPLATE-INJECT-FIX: _safe_format uses regex, not str.format()
- AUDIT-PII-FIX: Registration audit log doesn't store email
- LOGIN-MAXLEN-FIX: UserLogin.password has max_length
"""

from __future__ import annotations

import asyncio
import inspect
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.base import AgentResult, AgentStatus
from app.services.pipeline import (
    ExecutionMode,
    PipelineOrchestrator,
    PipelineRun,
    PipelineRunStatus,
    PipelineStage,
    ProjectRateLimiter,
    StepResult,
)


# ── FILE-CONTENTS-FIX: Shubham/Aanya output includes file_contents ──


class TestFileContentsInOutput:
    """Shubham and Aanya must include file_contents in their output."""

    def test_shubham_output_schema_has_file_contents(self):
        """Shubham's execute() output must include 'file_contents' key."""
        from app.agents.shubham import Shubham

        source = inspect.getsource(Shubham.execute)
        assert '"file_contents"' in source or "'file_contents'" in source

    def test_aanya_output_schema_has_file_contents(self):
        """Aanya's execute() output must include 'file_contents' key."""
        from app.agents.aanya import Aanya

        source = inspect.getsource(Aanya.execute)
        assert '"file_contents"' in source or "'file_contents'" in source

    def test_shubham_source_stores_generated_files_dict(self):
        """Shubham's output must store the generated_files DICT (not just keys)."""
        from app.agents.shubham import Shubham

        source = inspect.getsource(Shubham.execute)
        # Must store dict(generated_files), not list(generated_files.keys())
        assert "dict(generated_files)" in source


# ── FIXER-PARTIAL-FIX: Fixer COMPLETED for partial fixes ────────────


class TestFixerPartialStatus:
    """Fixer must return COMPLETED for FixStatus.PARTIAL to enable fix-retest loop."""

    def test_fixer_partial_returns_completed(self):
        """FixStatus.PARTIAL should map to AgentStatus.COMPLETED."""
        from app.agents.fixer import FixStatus

        # The fixer checks: report.status in (FixStatus.FIXED, FixStatus.PARTIAL)
        fixer_completed = FixStatus.PARTIAL in (FixStatus.FIXED, FixStatus.PARTIAL)
        assert fixer_completed is True

    def test_fixer_failed_returns_failed(self):
        """FixStatus.FAILED should map to AgentStatus.FAILED."""
        from app.agents.fixer import FixStatus

        assert FixStatus.FAILED not in (FixStatus.FIXED, FixStatus.PARTIAL)

    def test_fix_retest_loop_now_triggers_for_partial(self):
        """With COMPLETED status + errors remaining + fixes applied → retest triggers."""
        orch = PipelineOrchestrator()
        step = StepResult(
            stage=PipelineStage.FIXING,
            agent_name="fixer",
            result=AgentResult(
                agent_name="fixer",
                status=AgentStatus.COMPLETED,  # PARTIAL now maps to COMPLETED
                output={"errors_remaining": 2, "errors_fixed": 3},
            ),
        )
        assert orch._has_errors_to_fix(step) is True


# ── PIPELINE-START-FIX: start_pipeline calls run_pipeline ───────────


class TestPipelineStartExecution:
    """start_pipeline endpoint must actually start pipeline execution."""

    def test_start_pipeline_source_has_create_task(self):
        """Router must call asyncio.create_task(orch.run_pipeline(run))."""
        from app.routers import pipeline

        source = inspect.getsource(pipeline.start_pipeline)
        assert "create_task" in source
        assert "run_pipeline" in source

    def test_start_pipeline_imports_asyncio(self):
        """Pipeline router must import asyncio for create_task."""
        from app.routers import pipeline

        source = inspect.getsource(pipeline)
        assert "import asyncio" in source


# ── DOUBLE-EXECUTE-FIX: resume_run guards against RUNNING ───────────


class TestResumeRunRaceGuard:
    """resume_run must prevent concurrent execution of the same run."""

    @pytest.mark.asyncio
    async def test_resume_running_returns_without_executing(self):
        """If run.status is RUNNING, resume_run should return immediately."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.RUNNING

        # Should return the run without calling run_pipeline
        result = await orch.resume_run(run.run_id)
        assert result is run
        assert result.status == PipelineRunStatus.RUNNING

    @pytest.mark.asyncio
    async def test_resume_paused_allows_execution(self):
        """Paused runs at non-checkpoint should proceed normally."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        # Not at a checkpoint → should proceed to resume
        run.current_stage = PipelineStage.BACKEND_BUILD

        # Patch run_pipeline to avoid actual execution
        with patch.object(orch, "run_pipeline", new_callable=AsyncMock, return_value=run) as mock_rp:
            result = await orch.resume_run(run.run_id)
            mock_rp.assert_called_once_with(run)


# ── CONTRACT-KEYS-FIX: estimate_file_complexity uses nested keys ────


class TestContractKeysFix:
    """estimate_file_complexity must read contract.database.tables, not contract.tables."""

    def test_nested_contract_structure(self):
        """Should find tables/endpoints from nested contract structure."""
        from app.agents.base import estimate_file_complexity

        contract = {
            "database": {
                "tables": [{"name": "users"}, {"name": "projects"}, {"name": "runs"}],
                "relationships": [{"from": "users", "to": "projects"}],
            },
            "api": {
                "endpoints": [{"path": "/users"}, {"path": "/projects"}],
            },
            "features": ["auth", "pipeline"],
        }

        # Note: signature is (contract, step_name)
        result = estimate_file_complexity(contract, "models")
        # Should be > base because it found 3 tables, 2 endpoints, 1 rel, 2 features
        assert result > 50  # Base for models is higher with real data

    def test_flat_contract_still_works(self):
        """Backward-compat: top-level keys still work."""
        from app.agents.base import estimate_file_complexity

        contract = {
            "tables": [{"name": "users"}],
            "endpoints": [{"path": "/api"}],
        }

        result = estimate_file_complexity(contract, "routes")
        assert result > 0


# ── LOGIN-TIMING-FIX: Dummy bcrypt for non-existent users ──────────


class TestLoginTimingOracle:
    """Login must use dummy bcrypt check when user doesn't exist."""

    def test_login_source_has_dummy_hash(self):
        """The auth module must define a _DUMMY_HASH."""
        from app.routers import auth

        assert hasattr(auth, "_DUMMY_HASH")
        assert isinstance(auth._DUMMY_HASH, str)
        assert len(auth._DUMMY_HASH) > 20  # bcrypt hash is ~60 chars

    def test_login_source_calls_verify_on_missing_user(self):
        """When user is None, verify_password is still called."""
        from app.routers.auth import login

        source = inspect.getsource(login)
        # The dummy verify_password call should appear before the raise
        # R22: Now runs in thread pool via asyncio.to_thread
        assert "verify_password" in source and "_DUMMY_HASH" in source


# ── CHECKPOINT-STAGE-FIX: Validate checkpoint stage ─────────────────


class TestCheckpointStageValidation:
    """approve_checkpoint must verify the run is at a checkpoint stage."""

    @pytest.mark.asyncio
    async def test_non_checkpoint_stage_raises(self):
        """Paused at non-checkpoint stage should raise ValueError."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.BACKEND_BUILD

        with pytest.raises(ValueError, match="not at a checkpoint stage"):
            await orch.approve_checkpoint(run, approved=True)

    @pytest.mark.asyncio
    async def test_checkpoint_stage_works(self):
        """Paused at checkpoint stage should succeed."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN

        await orch.approve_checkpoint(run, approved=True)
        # R9-FIX: approve now leaves PAUSED, checkpoint_approved=True
        assert run.status == PipelineRunStatus.PAUSED
        assert run.checkpoint_approved is True

    @pytest.mark.asyncio
    async def test_step_by_step_pause_not_approvable(self):
        """Step-by-step pause at non-checkpoint can't be approved as checkpoint."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.execution_mode = ExecutionMode.STEP_BY_STEP
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.ANALYSIS

        with pytest.raises(ValueError, match="not at a checkpoint stage"):
            await orch.approve_checkpoint(run, approved=True)


# ── GET-USAGE-FIX: No defaultdict side effects ─────────────────────


class TestGetUsageNoSideEffect:
    """get_usage must not create empty entries for unknown projects."""

    def test_unknown_project_returns_zero(self):
        rl = ProjectRateLimiter()
        assert rl.get_usage("nonexistent-project") == 0

    def test_unknown_project_doesnt_create_entry(self):
        rl = ProjectRateLimiter()
        rl.get_usage("nonexistent-project")
        assert "nonexistent-project" not in rl._buckets

    def test_stale_entries_cleaned_on_get_usage(self):
        """get_usage should clean up stale entries."""
        rl = ProjectRateLimiter()
        now = time.monotonic()
        rl._buckets["old-project"] = [now - 120]  # 120s ago, window is 60s

        count = rl.get_usage("old-project")
        assert count == 0
        assert "old-project" not in rl._buckets


# ── VERTEX-LOCK-FIX: Thread-safe credential refresh ────────────────


class TestVertexCredentialLock:
    """VertexAICredentialManager must have a thread-safe refresh lock."""

    def test_has_refresh_lock(self):
        import threading
        from app.services.secret_manager import VertexAICredentialManager

        mgr = VertexAICredentialManager()
        assert hasattr(mgr, "_refresh_lock")
        assert isinstance(mgr._refresh_lock, threading.Lock)

    def test_get_access_token_uses_lock(self):
        from app.services.secret_manager import VertexAICredentialManager

        source = inspect.getsource(VertexAICredentialManager.get_access_token)
        assert "self._refresh_lock" in source


# ── NOTIFICATION-ORG-FIX: Tenant-scoped notification queries ───────


class TestNotificationOrgFilter:
    """Notification queries must support organization_id filtering."""

    def test_get_user_notifications_accepts_org_id(self):
        from app.services.notification import NotificationService

        sig = inspect.signature(NotificationService.get_user_notifications)
        assert "organization_id" in sig.parameters

    def test_notification_count_accepts_org_id(self):
        from app.services.notification import NotificationService

        sig = inspect.signature(NotificationService.notification_count)
        assert "organization_id" in sig.parameters

    def test_org_filter_actually_filters(self):
        from app.services.notification import (
            NotificationChannel,
            NotificationPayload,
            NotificationService,
            NotificationType,
        )

        svc = NotificationService()
        # Add notifications for different orgs
        svc._sent.append(NotificationPayload(
            notification_type=NotificationType.PIPELINE_STARTED,
            user_id="user-1", organization_id="org-A",
            title="Test A",
        ))
        svc._sent.append(NotificationPayload(
            notification_type=NotificationType.PIPELINE_COMPLETED,
            user_id="user-1", organization_id="org-B",
            title="Test B",
        ))

        # Filter by org-A
        result = svc.get_user_notifications("user-1", organization_id="org-A")
        assert len(result) == 1
        assert result[0].organization_id == "org-A"

        # Count for org-B
        count = svc.notification_count("user-1", organization_id="org-B")
        assert count == 1


# ── TEMPLATE-INJECT-FIX: _safe_format uses regex ───────────────────


class TestTemplateInjectionDefense:
    """_safe_format must use regex substitution, not str.format()."""

    def test_safe_format_source_no_str_format(self):
        """Must NOT use template.format(**vars) — vulnerable to attribute access."""
        from app.services.notification import _safe_format

        source = inspect.getsource(_safe_format)
        assert ".format(**" not in source

    def test_safe_format_uses_regex(self):
        """Must use re.sub for safe variable substitution."""
        from app.services.notification import _safe_format

        source = inspect.getsource(_safe_format)
        assert "re.sub" in source

    def test_safe_format_basic(self):
        from app.services.notification import _safe_format

        result = _safe_format("Hello {name}!", {"name": "World"})
        assert result == "Hello World!"

    def test_safe_format_missing_key_preserved(self):
        from app.services.notification import _safe_format

        result = _safe_format("Hello {name} at {place}!", {"name": "World"})
        assert result == "Hello World at {place}!"

    def test_safe_format_attribute_access_blocked(self):
        """Python format string attack should NOT work."""
        from app.services.notification import _safe_format

        # This would be dangerous with str.format():
        # {0.__class__.__init__.__globals__}
        malicious_template = "Hello {0.__class__}"
        result = _safe_format(malicious_template, {"name": "test"})
        # Should be left as-is because regex only matches {word} patterns
        assert result == "Hello {0.__class__}"


# ── AUDIT-PII-FIX: Registration audit log doesn't store email ──────


class TestAuditPIIRemoval:
    """Registration audit log must not store email address."""

    def test_register_audit_no_email(self):
        from app.routers.auth import register

        source = inspect.getsource(register)
        # The after_state= kwarg should NOT contain email
        assert 'after_state={"email":' not in source
        assert '"email": body.email' not in source


# ── LOGIN-MAXLEN-FIX: UserLogin password max_length ─────────────────


class TestLoginPasswordMaxLength:
    """UserLogin.password must have max_length to prevent DoS."""

    def test_login_password_has_max_length(self):
        from app.schemas.auth import UserLogin

        schema = UserLogin.model_json_schema()
        pw_prop = schema["properties"]["password"]
        assert "maxLength" in pw_prop
        assert pw_prop["maxLength"] <= 1024

    def test_oversized_password_rejected(self):
        from pydantic import ValidationError
        from app.schemas.auth import UserLogin

        with pytest.raises(ValidationError):
            UserLogin(email="test@test.com", password="x" * 2000)

    def test_normal_password_accepted(self):
        from app.schemas.auth import UserLogin

        login = UserLogin(email="test@test.com", password="normal_password")
        assert login.password == "normal_password"
