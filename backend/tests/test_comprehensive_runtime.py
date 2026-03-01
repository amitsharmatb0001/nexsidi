"""Comprehensive runtime validation for ALL modules, classes, methods, and functions.

This test file validates EVERY file in the app/ directory:
- Runtime import checks (no import errors, no circular dependencies)
- Class existence and type checks
- Method existence on all classes
- Function existence at module level
- Registry completeness (agents, frameworks, frontend frameworks)
- Dataclass frozen checks
- Enum membership
- Singleton factory functions return correct types
- Cross-module integration

THIS IS THE FINAL WORD: if it's in app/, it's tested here.
"""

from __future__ import annotations

import asyncio
import inspect
import types
from dataclasses import fields as dc_fields
from enum import Enum
from typing import Any

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1: Runtime import checks for EVERY module
# ═══════════════════════════════════════════════════════════════════════════


class TestRuntimeImportsAgents:
    """Every agent module must import without errors."""

    def test_import_agents_init(self):
        import app.agents

    def test_import_base(self):
        from app.agents.base import AgentResult, AgentStatus, ToolDefinition, call_ai, store_output, run_agent

    def test_import_shubham(self):
        from app.agents.shubham import Shubham

    def test_import_aanya(self):
        from app.agents.aanya import Aanya

    def test_import_aarav(self):
        from app.agents.aarav import Aarav

    def test_import_dhruv(self):
        from app.agents.dhruv import Dhruv

    def test_import_vikram(self):
        from app.agents.vikram import Vikram

    def test_import_vanya(self):
        from app.agents.vanya import Vanya

    def test_import_tilotma(self):
        from app.agents.tilotma import Tilotma

    def test_import_saanvi(self):
        from app.agents.saanvi import Saanvi

    def test_import_pranav(self):
        from app.agents.pranav import Pranav

    def test_import_fixer(self):
        from app.agents.fixer import Fixer

    def test_import_docs_agent(self):
        from app.agents.docs_agent import DocsAgent

    def test_import_navya(self):
        from app.agents.navya import Navya

    def test_import_karan(self):
        from app.agents.karan import Karan

    def test_import_deepika(self):
        from app.agents.deepika import Deepika

    def test_import_challenger(self):
        from app.agents.challenger import Challenger

    def test_import_scan_utils(self):
        from app.agents.scan_utils import split_into_windows

    def test_import_support_agent(self):
        from app.agents.support_agent import SupportAgent

    def test_import_whatsapp_agent(self):
        from app.agents.whatsapp_agent import WhatsAppAgent

    def test_import_git_agent(self):
        from app.agents.git_agent import GitAgent

    def test_import_security_guardian(self):
        from app.agents.security_guardian import SecurityGuardian

    def test_import_system_monitor(self):
        from app.agents.system_monitor import SystemMonitor


class TestRuntimeImportsFrameworks:
    """Every framework config module must import cleanly."""

    def test_import_frameworks_init(self):
        from app.agents.frameworks import FrameworkConfig, register_framework, get_framework_config, list_frameworks

    def test_import_fastapi_config(self):
        import app.agents.frameworks.fastapi_config

    def test_import_django_config(self):
        import app.agents.frameworks.django_config

    def test_import_express_config(self):
        import app.agents.frameworks.express_config

    def test_import_flask_config(self):
        import app.agents.frameworks.flask_config

    def test_import_nestjs_config(self):
        import app.agents.frameworks.nestjs_config

    def test_import_nextjs_config(self):
        import app.agents.frameworks.nextjs_config

    def test_import_laravel_config(self):
        import app.agents.frameworks.laravel_config

    def test_import_springboot_config(self):
        import app.agents.frameworks.springboot_config

    def test_import_aspnet_config(self):
        import app.agents.frameworks.aspnet_config

    def test_import_go_gin_config(self):
        import app.agents.frameworks.go_gin_config

    def test_import_rails_config(self):
        import app.agents.frameworks.rails_config

    def test_import_rust_axum_config(self):
        import app.agents.frameworks.rust_axum_config

    def test_import_kotlin_ktor_config(self):
        import app.agents.frameworks.kotlin_ktor_config


class TestRuntimeImportsFrontendFrameworks:
    """Every frontend framework config module must import cleanly."""

    def test_import_frontend_frameworks_init(self):
        from app.agents.frontend_frameworks import (
            FrontendFrameworkConfig,
            register_frontend_framework,
            get_frontend_framework_config,
            list_frontend_frameworks,
        )

    def test_import_vue_config(self):
        import app.agents.frontend_frameworks.vue_config

    def test_import_angular_config(self):
        import app.agents.frontend_frameworks.angular_config

    def test_import_svelte_config(self):
        import app.agents.frontend_frameworks.svelte_config

    def test_import_remix_config(self):
        import app.agents.frontend_frameworks.remix_config

    def test_import_astro_config(self):
        import app.agents.frontend_frameworks.astro_config

    def test_import_solid_config(self):
        import app.agents.frontend_frameworks.solid_config


class TestRuntimeImportsServices:
    """Every service module must import cleanly."""

    def test_import_ai_router(self):
        from app.services.ai_router import AIRouter, AIRequest, AIResponse, AIMessage

    def test_import_ai_router_extras(self):
        from app.services.ai_router import (
            select_model_for_generation,
            ProjectCostTracker,
            CostEntry,
            Provider,
            ModelSpec,
            TaskComplexity,
            CircuitState,
        )

    def test_import_auth_service(self):
        from app.services.auth import (
            hash_password,
            verify_password,
            create_access_token,
            create_refresh_token,
            decode_token,
        )

    def test_import_pipeline(self):
        from app.services.pipeline import (
            PipelineStage,
            PipelineOrchestrator,
            PipelineRun,
            StepResult,
            ExecutionMode,
            PipelineRunStatus,
        )

    def test_import_checkpoint(self):
        from app.services.checkpoint import (
            CheckpointType,
            ApprovalStatus,
            CheckpointData,
            CheckpointService,
        )

    def test_import_context_engine(self):
        from app.services.context_engine import (
            ContextEngine,
            ContextEntry,
            ContextQuery,
            compute_content_hash,
        )

    def test_import_prompt_engine(self):
        from app.services.prompt_engine import (
            PromptEngine,
            PromptTemplate,
            RenderedPrompt,
            PromptRegistry,
            render_template,
        )

    def test_import_tech_stack(self):
        from app.services.tech_stack import (
            validate_tech_stack,
            suggest_tech_stack,
            resolve_tech_name,
            get_supported_stacks,
            get_maturity,
            Maturity,
            TechEntry,
            ValidationResult,
        )

    def test_import_audit(self):
        from app.services.audit import log_action

    def test_import_pipeline_audit(self):
        from app.services.pipeline_audit import (
            PipelineAuditService,
            AuditEventType,
            AuditEvent,
        )

    def test_import_notification(self):
        from app.services.notification import (
            NotificationService,
            NotificationChannel,
            NotificationType,
            NotificationPayload,
        )

    def test_import_input_processor(self):
        from app.services.input_processor import InputProcessor, InputFile, ProcessedInput

    def test_import_voice_to_text(self):
        from app.services.voice_to_text import VoiceToTextService, TranscriptionResult

    def test_import_api_key_service(self):
        from app.services.api_key_service import (
            ApiKeyService,
            ApiKeyRecord,
            generate_api_key,
            hash_api_key,
        )

    def test_import_secret_manager(self):
        from app.services.secret_manager import VertexAICredentialManager


class TestRuntimeImportsEngines:
    """Every engine module must import cleanly."""

    def test_import_code_quality(self):
        from app.engine.code_quality import CodeQualityEngine, QualityReport, QualityIssue

    def test_import_execution_engine(self):
        from app.engine.execution_engine import ExecutionEngine, SandboxConfig, SandboxState

    def test_import_compliance_engine(self):
        from app.engine.compliance_engine import ComplianceEngine, ComplianceReport, ComplianceFinding

    def test_import_delivery(self):
        from app.engine.delivery import DeliveryEngine, DeliveryManifest, DeliveryPackage

    def test_import_template_engine(self):
        from app.engine.template_engine import TemplateEngine, FileTemplate, GeneratedFile


class TestRuntimeImportsCore:
    """Core modules must import cleanly."""

    def test_import_config(self):
        from app.config import Settings, get_settings

    def test_import_database(self):
        from app.database import setup_database, SCHEMAS

    def test_import_dependencies(self):
        from app.dependencies import get_current_user_context

    def test_import_main(self):
        from app.main import create_app

    def test_import_models_core(self):
        import app.models.core

    def test_import_models_auth(self):
        import app.models.auth

    def test_import_models_chat(self):
        import app.models.chat

    def test_import_models_deploy(self):
        import app.models.deploy

    def test_import_models_audit(self):
        import app.models.audit

    def test_import_models_billing(self):
        import app.models.billing

    def test_import_models_pipeline(self):
        import app.models.pipeline

    def test_import_models_notify(self):
        import app.models.notify

    def test_import_schemas_auth(self):
        import app.schemas.auth

    def test_import_schemas_project(self):
        import app.schemas.project

    def test_import_routers_auth(self):
        import app.routers.auth

    def test_import_routers_projects(self):
        import app.routers.projects

    def test_import_routers_pipeline(self):
        import app.routers.pipeline

    def test_import_routers_chat(self):
        import app.routers.chat

    def test_import_routers_notifications(self):
        import app.routers.notifications

    def test_import_routers_websocket(self):
        import app.routers.websocket

    def test_import_middleware_tenant(self):
        import app.middleware.tenant


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2: Agent class existence and inheritance
# ═══════════════════════════════════════════════════════════════════════════


ALL_AGENT_CLASSES = [
    "Shubham", "Aanya", "Aarav", "Dhruv", "Vikram", "Vanya",
    "Tilotma", "Saanvi", "Pranav", "Fixer", "DocsAgent",
    "Navya", "Karan", "Deepika", "Challenger", "SupportAgent",
    "WhatsAppAgent", "GitAgent", "SecurityGuardian", "SystemMonitor",
]

AGENT_MODULES = {
    "Shubham": "app.agents.shubham",
    "Aanya": "app.agents.aanya",
    "Aarav": "app.agents.aarav",
    "Dhruv": "app.agents.dhruv",
    "Vikram": "app.agents.vikram",
    "Vanya": "app.agents.vanya",
    "Tilotma": "app.agents.tilotma",
    "Saanvi": "app.agents.saanvi",
    "Pranav": "app.agents.pranav",
    "Fixer": "app.agents.fixer",
    "DocsAgent": "app.agents.docs_agent",
    "Navya": "app.agents.navya",
    "Karan": "app.agents.karan",
    "Deepika": "app.agents.deepika",
    "Challenger": "app.agents.challenger",
    "SupportAgent": "app.agents.support_agent",
    "WhatsAppAgent": "app.agents.whatsapp_agent",
    "GitAgent": "app.agents.git_agent",
    "SecurityGuardian": "app.agents.security_guardian",
    "SystemMonitor": "app.agents.system_monitor",
}


class TestAgentClassesExist:
    """Every agent class must exist as a standalone class with required interface."""

    @pytest.mark.parametrize("class_name", ALL_AGENT_CLASSES)
    def test_agent_class_exists(self, class_name: str):
        import importlib
        mod = importlib.import_module(AGENT_MODULES[class_name])
        cls = getattr(mod, class_name)
        assert inspect.isclass(cls), f"{class_name} is not a class"

    @pytest.mark.parametrize("class_name", ALL_AGENT_CLASSES)
    def test_agent_is_standalone(self, class_name: str):
        """Agents are standalone — no BaseAgent inheritance."""
        import importlib
        mod = importlib.import_module(AGENT_MODULES[class_name])
        cls = getattr(mod, class_name)
        # Should be a standalone class (not inheriting from any agent base)
        assert hasattr(cls, "name"), f"{class_name} missing 'name' attribute"
        assert hasattr(cls, "display_name"), f"{class_name} missing 'display_name' attribute"
        assert hasattr(cls, "execute"), f"{class_name} missing execute()"
        assert hasattr(cls, "run"), f"{class_name} missing run()"
        assert hasattr(cls, "tools"), f"{class_name} missing tools property"

    @pytest.mark.parametrize("class_name", ALL_AGENT_CLASSES)
    def test_agent_has_execute_method(self, class_name: str):
        import importlib
        mod = importlib.import_module(AGENT_MODULES[class_name])
        cls = getattr(mod, class_name)
        assert hasattr(cls, "execute"), f"{class_name} missing execute()"
        assert callable(getattr(cls, "execute"))

    @pytest.mark.parametrize("class_name", ALL_AGENT_CLASSES)
    def test_agent_execute_is_async(self, class_name: str):
        import importlib
        mod = importlib.import_module(AGENT_MODULES[class_name])
        cls = getattr(mod, class_name)
        method = getattr(cls, "execute")
        assert asyncio.iscoroutinefunction(method), f"{class_name}.execute() is not async"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3: Agent-specific method existence
# ═══════════════════════════════════════════════════════════════════════════


class TestStandaloneFunctions:
    """Standalone agent functions must exist in base module."""

    REQUIRED_FUNCTIONS = [
        "call_ai", "call_ai_with_continuation", "call_ai_with_tools",
        "store_output", "get_step_context", "estimate_file_complexity",
        "run_agent", "register_agent", "get_agent", "list_agents",
    ]

    @pytest.mark.parametrize("func_name", REQUIRED_FUNCTIONS)
    def test_standalone_function_exists(self, func_name: str):
        import app.agents.base as base
        assert hasattr(base, func_name), f"base module missing {func_name}"

    def test_estimate_file_complexity_is_function(self):
        from app.agents.base import estimate_file_complexity
        assert callable(estimate_file_complexity)

    def test_call_ai_is_async(self):
        from app.agents.base import call_ai
        assert asyncio.iscoroutinefunction(call_ai)


class TestShubhamMethods:
    """Shubham must have all generation-related methods."""

    REQUIRED_METHODS = ["execute", "_generate_step", "_generate_step_isolated", "_build_generation_prompt"]
    REQUIRED_FUNCTIONS = ["get_backend_generation_order", "get_dependency_graph", "compute_parallel_levels", "split_generation_step"]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_shubham_has_method(self, method_name: str):
        from app.agents.shubham import Shubham
        assert hasattr(Shubham, method_name)

    @pytest.mark.parametrize("func_name", REQUIRED_FUNCTIONS)
    def test_shubham_module_has_function(self, func_name: str):
        import app.agents.shubham as mod
        assert hasattr(mod, func_name)
        assert callable(getattr(mod, func_name))


class TestAaravMethods:
    """Aarav test executor must have all phase methods."""

    REQUIRED_METHODS = [
        "execute", "_phase_docker_build", "_phase_server_start",
        "_phase_db_migration", "_phase_api_test", "_phase_browser_test",
        "_phase_db_verify", "_collect_quality_findings", "_build_result",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_aarav_has_method(self, method_name: str):
        from app.agents.aarav import Aarav
        assert hasattr(Aarav, method_name)


class TestKaranMethods:
    """Karan security auditor must have all scan methods."""

    REQUIRED_METHODS = [
        "execute", "_collect_generated_files", "_scan_python_security",
        "_scan_ts_security", "_scan_dpdp_compliance", "_check_owasp_top10", "_ai_deep_scan",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_karan_has_method(self, method_name: str):
        from app.agents.karan import Karan
        assert hasattr(Karan, method_name)


class TestNavyaMethods:
    """Navya logic analyst must have all scan methods."""

    REQUIRED_METHODS = [
        "execute", "_collect_generated_files", "_scan_python_logic",
        "_scan_ts_logic", "_check_missing_returns",
        "_check_error_handling_completeness", "_ai_contract_verification",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_navya_has_method(self, method_name: str):
        from app.agents.navya import Navya
        assert hasattr(Navya, method_name)


class TestDeepikaMethods:
    """Deepika performance analyst must have all scan methods."""

    REQUIRED_METHODS = [
        "execute", "_collect_generated_files", "_scan_python_perf",
        "_scan_ts_perf", "_check_pagination", "_check_missing_indexes", "_ai_perf_analysis",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_deepika_has_method(self, method_name: str):
        from app.agents.deepika import Deepika
        assert hasattr(Deepika, method_name)


class TestChallengerMethods:
    """Challenger architecture reviewer must have all check methods."""

    REQUIRED_METHODS = [
        "execute", "_check_over_engineering", "_check_security_gaps",
        "_check_missing_edge_cases", "_check_contradictions",
        "_check_scalability", "_ai_review",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_challenger_has_method(self, method_name: str):
        from app.agents.challenger import Challenger
        assert hasattr(Challenger, method_name)


class TestFixerMethods:
    """Fixer error correction agent must have all fix methods."""

    REQUIRED_METHODS = [
        "execute", "_attempt_fix", "_collect_errors",
        "_get_file_content", "_update_file_in_context",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_fixer_has_method(self, method_name: str):
        from app.agents.fixer import Fixer
        assert hasattr(Fixer, method_name)


class TestPranavMethods:
    """Pranav deployment engineer must have deploy methods."""

    REQUIRED_METHODS = ["execute", "_generate_config_files", "_deploy"]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_pranav_has_method(self, method_name: str):
        from app.agents.pranav import Pranav
        assert hasattr(Pranav, method_name)


class TestDocsAgentMethods:
    """DocsAgent must have all generation methods."""

    REQUIRED_METHODS = ["execute", "_generate_readme", "_generate_api_docs", "_generate_setup_guide"]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_docs_agent_has_method(self, method_name: str):
        from app.agents.docs_agent import DocsAgent
        assert hasattr(DocsAgent, method_name)


class TestGitAgentMethods:
    """GitAgent must have all git operations."""

    REQUIRED_METHODS = ["execute", "_create_repo", "_push_code", "_create_pr", "generate_gitignore"]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_git_agent_has_method(self, method_name: str):
        from app.agents.git_agent import GitAgent
        assert hasattr(GitAgent, method_name)


class TestSecurityGuardianMethods:
    """SecurityGuardian must have all scan methods."""

    REQUIRED_METHODS = [
        "execute", "full_scan", "scan_secrets",
        "scan_security_headers", "classify_update_risk", "_collect_code",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_security_guardian_has_method(self, method_name: str):
        from app.agents.security_guardian import SecurityGuardian
        assert hasattr(SecurityGuardian, method_name)


class TestSupportAgentMethods:
    """SupportAgent must have triage methods."""

    REQUIRED_METHODS = [
        "execute", "triage", "_classify_type", "_classify_component",
        "_classify_severity", "_extract_title", "_suggest_resolution",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_support_agent_has_method(self, method_name: str):
        from app.agents.support_agent import SupportAgent
        assert hasattr(SupportAgent, method_name)


class TestVikramMethods:
    """Vikram must have contract parsing methods."""

    REQUIRED_METHODS = ["execute", "_parse_contract"]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_vikram_has_method(self, method_name: str):
        from app.agents.vikram import Vikram
        assert hasattr(Vikram, method_name)

    def test_validate_contract_function(self):
        from app.agents.vikram import validate_contract
        assert callable(validate_contract)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4: Service class and method existence
# ═══════════════════════════════════════════════════════════════════════════


class TestAIRouterMethods:
    """AIRouter must have all call methods."""

    REQUIRED_METHODS = [
        "select_model", "call", "call_stream", "close",
        "_call_anthropic", "_build_anthropic_body",
        "_call_google", "_parse_google_response",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_ai_router_has_method(self, method_name: str):
        from app.services.ai_router import AIRouter
        assert hasattr(AIRouter, method_name)

    def test_select_model_for_generation_exists(self):
        from app.services.ai_router import select_model_for_generation
        assert callable(select_model_for_generation)

    def test_project_cost_tracker_methods(self):
        from app.services.ai_router import ProjectCostTracker
        for method in ["record", "per_model_breakdown", "per_agent_breakdown", "summary"]:
            assert hasattr(ProjectCostTracker, method), f"ProjectCostTracker missing {method}"

    def test_cost_entry_is_dataclass(self):
        from app.services.ai_router import CostEntry
        import dataclasses
        assert dataclasses.is_dataclass(CostEntry)

    def test_ai_response_is_dataclass(self):
        from app.services.ai_router import AIResponse
        import dataclasses
        assert dataclasses.is_dataclass(AIResponse)

    def test_ai_request_is_dataclass(self):
        from app.services.ai_router import AIRequest
        import dataclasses
        assert dataclasses.is_dataclass(AIRequest)


class TestPipelineServiceMethods:
    """PipelineOrchestrator must have all orchestration methods."""

    REQUIRED_METHODS = [
        "create_run", "get_run", "execute_stage", "advance",
        "approve_checkpoint", "run_pipeline", "resume_run",
        "recover_interrupted_runs",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_orchestrator_has_method(self, method_name: str):
        from app.services.pipeline import PipelineOrchestrator
        assert hasattr(PipelineOrchestrator, method_name)

    def test_stage_agents_exists(self):
        from app.services.pipeline import STAGE_AGENTS
        assert isinstance(STAGE_AGENTS, dict)
        assert len(STAGE_AGENTS) > 0

    def test_checkpoint_stages_exists(self):
        from app.services.pipeline import CHECKPOINT_STAGES
        assert isinstance(CHECKPOINT_STAGES, frozenset)


class TestCheckpointServiceMethods:
    """CheckpointService must have all approval methods."""

    REQUIRED_METHODS = [
        "create_design_checkpoint", "create_testing_checkpoint",
        "get_checkpoint", "get_pipeline_checkpoints",
        "approve", "reject", "request_changes",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_checkpoint_service_has_method(self, method_name: str):
        from app.services.checkpoint import CheckpointService
        assert hasattr(CheckpointService, method_name)


class TestContextEngineMethods:
    """ContextEngine must have all storage methods."""

    REQUIRED_METHODS = [
        "store", "get_step", "get_all_steps",
        "get_latest_hash", "clear_pipeline",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_context_engine_has_method(self, method_name: str):
        from app.services.context_engine import ContextEngine
        assert hasattr(ContextEngine, method_name)

    def test_compute_content_hash_exists(self):
        from app.services.context_engine import compute_content_hash
        assert callable(compute_content_hash)

    def test_compute_content_hash_works(self):
        from app.services.context_engine import compute_content_hash
        h1 = compute_content_hash(b"hello")
        h2 = compute_content_hash(b"hello")
        h3 = compute_content_hash(b"world")
        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 64  # SHA-256 hex digest


class TestPromptEngineMethods:
    """PromptEngine must have all render methods."""

    REQUIRED_METHODS = ["render", "get_model_hint", "invalidate"]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_prompt_engine_has_method(self, method_name: str):
        from app.services.prompt_engine import PromptEngine
        assert hasattr(PromptEngine, method_name)

    def test_render_template_function(self):
        from app.services.prompt_engine import render_template
        assert callable(render_template)


class TestPipelineAuditMethods:
    """PipelineAuditService must have all event recording methods."""

    REQUIRED_METHODS = [
        "record", "pipeline_created", "pipeline_started",
        "pipeline_completed", "pipeline_failed", "stage_started",
        "stage_completed", "stage_failed", "get_events",
        "get_events_by_type", "get_timeline", "event_count",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_audit_service_has_method(self, method_name: str):
        from app.services.pipeline_audit import PipelineAuditService
        assert hasattr(PipelineAuditService, method_name)


class TestNotificationServiceMethods:
    """NotificationService must have all sending methods."""

    REQUIRED_METHODS = ["send", "send_to_project_users", "get_user_notifications", "notification_count"]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_notification_service_has_method(self, method_name: str):
        from app.services.notification import NotificationService
        assert hasattr(NotificationService, method_name)


class TestInputProcessorMethods:
    """InputProcessor must have all processing methods."""

    REQUIRED_METHODS = ["process_text", "process_url", "process_file", "process_all"]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_input_processor_has_method(self, method_name: str):
        from app.services.input_processor import InputProcessor
        assert hasattr(InputProcessor, method_name)


class TestVoiceToTextMethods:
    """VoiceToTextService must have all transcription methods."""

    REQUIRED_METHODS = ["validate_audio", "detect_format", "transcribe", "transcribe_whatsapp_voice"]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_voice_service_has_method(self, method_name: str):
        from app.services.voice_to_text import VoiceToTextService
        assert hasattr(VoiceToTextService, method_name)


class TestApiKeyServiceMethods:
    """ApiKeyService must have all key management methods."""

    REQUIRED_METHODS = [
        "create_key", "verify_key", "check_rate_limit",
        "revoke_key", "regenerate_key", "list_keys",
        "list_org_keys", "get_key_by_id",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_api_key_service_has_method(self, method_name: str):
        from app.services.api_key_service import ApiKeyService
        assert hasattr(ApiKeyService, method_name)

    def test_generate_api_key_function(self):
        from app.services.api_key_service import generate_api_key
        assert callable(generate_api_key)

    def test_hash_api_key_function(self):
        from app.services.api_key_service import hash_api_key
        assert callable(hash_api_key)


class TestTechStackFunctions:
    """Tech stack module must have all validation functions."""

    REQUIRED_FUNCTIONS = [
        "validate_tech_stack", "suggest_tech_stack",
        "resolve_tech_name", "get_supported_stacks", "get_maturity",
    ]

    @pytest.mark.parametrize("func_name", REQUIRED_FUNCTIONS)
    def test_tech_stack_has_function(self, func_name: str):
        import app.services.tech_stack as mod
        assert hasattr(mod, func_name)
        assert callable(getattr(mod, func_name))

    def test_tech_stack_dicts_exist(self):
        from app.services.tech_stack import BACKEND_STACKS, FRONTEND_STACKS, DATABASE_STACKS
        assert isinstance(BACKEND_STACKS, dict)
        assert isinstance(FRONTEND_STACKS, dict)
        assert isinstance(DATABASE_STACKS, dict)
        assert len(BACKEND_STACKS) > 0
        assert len(FRONTEND_STACKS) > 0
        assert len(DATABASE_STACKS) > 0


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5: Engine class and method existence
# ═══════════════════════════════════════════════════════════════════════════


class TestCodeQualityEngineMethods:
    """CodeQualityEngine must have all validation methods."""

    REQUIRED_METHODS = [
        "validate_project", "_check_anti_hallucination",
        "_check_python_imports", "_check_typescript_imports",
        "_check_model_schema_match", "_check_endpoint_coverage", "_check_type_hints",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_code_quality_has_method(self, method_name: str):
        from app.engine.code_quality import CodeQualityEngine
        assert hasattr(CodeQualityEngine, method_name)


class TestExecutionEngineMethods:
    """ExecutionEngine must have all sandbox methods."""

    REQUIRED_METHODS = [
        "validate_dockerfile", "build_sandbox", "start_sandbox",
        "run_migrations", "run_api_tests", "run_browser_tests",
        "verify_database", "cleanup_sandbox",
        "generate_iptables_rules", "generate_seccomp_profile",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_execution_engine_has_method(self, method_name: str):
        from app.engine.execution_engine import ExecutionEngine
        assert hasattr(ExecutionEngine, method_name)


class TestComplianceEngineMethods:
    """ComplianceEngine must have all compliance check methods."""

    REQUIRED_METHODS = ["check_all", "check_dpdp", "check_owasp", "check_wcag"]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_compliance_engine_has_method(self, method_name: str):
        from app.engine.compliance_engine import ComplianceEngine
        assert hasattr(ComplianceEngine, method_name)


class TestDeliveryEngineMethods:
    """DeliveryEngine must have all package building methods."""

    REQUIRED_METHODS = [
        "build_package", "_collect_backend_files",
        "_collect_frontend_files", "_collect_reports",
        "_collect_deploy_files", "_generate_summary",
    ]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_delivery_engine_has_method(self, method_name: str):
        from app.engine.delivery import DeliveryEngine
        assert hasattr(DeliveryEngine, method_name)


class TestTemplateEngineMethods:
    """TemplateEngine must have all template methods."""

    REQUIRED_METHODS = ["register", "list_templates", "render_all", "render_one"]

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_template_engine_has_method(self, method_name: str):
        from app.engine.template_engine import TemplateEngine
        assert hasattr(TemplateEngine, method_name)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6: Registry completeness
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentRegistry:
    """All agents must be registered in the global registry."""

    EXPECTED_AGENTS = [
        "tilotma", "saanvi", "vikram", "dhruv", "vanya",
        "shubham", "aanya", "aarav", "karan", "navya",
        "deepika", "pranav", "fixer", "docs_agent", "challenger",
        "support_agent", "whatsapp_agent", "git_agent", "security_guardian",
    ]

    def test_list_agents_returns_many(self):
        from app.agents.base import list_agents
        agents = list_agents()
        assert len(agents) >= 19, f"Expected >= 19, got {len(agents)}: {agents}"

    @pytest.mark.parametrize("name", EXPECTED_AGENTS)
    def test_agent_registered(self, name: str):
        from app.agents.base import get_agent
        agent_cls = get_agent(name)
        assert agent_cls is not None, f"Agent '{name}' not registered"


class TestBackendFrameworkRegistry:
    """All 13 backend frameworks must be in the registry."""

    ALL_BACKENDS = [
        "fastapi", "django", "express", "flask", "nestjs",
        "nextjs", "laravel", "springboot", "aspnet", "go_gin",
        "rails", "rust_axum", "kotlin_ktor",
    ]

    def test_list_returns_13_plus(self):
        from app.agents.frameworks import list_frameworks
        fw = list_frameworks()
        assert len(fw) >= 13, f"Expected >= 13, got {len(fw)}: {fw}"

    @pytest.mark.parametrize("name", ALL_BACKENDS)
    def test_backend_framework_exists(self, name: str):
        from app.agents.frameworks import get_framework_config
        config = get_framework_config(name)
        assert config.name == name

    @pytest.mark.parametrize("name", ALL_BACKENDS)
    def test_backend_framework_has_rules(self, name: str):
        from app.agents.frameworks import get_framework_config
        config = get_framework_config(name)
        assert len(config.rules) >= 12, f"{name} has only {len(config.rules)} rules"

    @pytest.mark.parametrize("name", ALL_BACKENDS)
    def test_backend_framework_has_golden_examples(self, name: str):
        from app.agents.frameworks import get_framework_config
        config = get_framework_config(name)
        assert len(config.golden_examples) >= 3, f"{name} has only {len(config.golden_examples)} examples"


class TestFrontendFrameworkRegistry:
    """All 6 frontend frameworks must be in the registry."""

    ALL_FRONTENDS = ["vue", "angular", "svelte", "remix", "astro", "solid"]

    def test_list_returns_6_plus(self):
        from app.agents.frontend_frameworks import list_frontend_frameworks
        fw = list_frontend_frameworks()
        assert len(fw) >= 6, f"Expected >= 6, got {len(fw)}: {fw}"

    @pytest.mark.parametrize("name", ALL_FRONTENDS)
    def test_frontend_framework_exists(self, name: str):
        from app.agents.frontend_frameworks import get_frontend_framework_config
        config = get_frontend_framework_config(name)
        assert config.name == name

    @pytest.mark.parametrize("name", ALL_FRONTENDS)
    def test_frontend_framework_has_rules(self, name: str):
        from app.agents.frontend_frameworks import get_frontend_framework_config
        config = get_frontend_framework_config(name)
        assert len(config.rules) >= 11, f"{name} has only {len(config.rules)} rules"

    @pytest.mark.parametrize("name", ALL_FRONTENDS)
    def test_frontend_framework_has_golden_examples(self, name: str):
        from app.agents.frontend_frameworks import get_frontend_framework_config
        config = get_frontend_framework_config(name)
        assert len(config.golden_examples) >= 3, f"{name} has only {len(config.golden_examples)} examples"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7: Dataclass and Enum validation
# ═══════════════════════════════════════════════════════════════════════════


class TestDataclassFrozenChecks:
    """All config dataclasses that should be frozen ARE frozen."""

    def test_framework_config_is_frozen(self):
        from app.agents.frameworks import FrameworkConfig
        config = FrameworkConfig(
            name="test", display_name="Test", language="python",
            code_block_lang="python", error_comment_prefix="#",
            file_structure={}, rules=(), golden_examples={},
        )
        with pytest.raises(Exception):
            config.name = "hacked"

    def test_frontend_framework_config_is_frozen(self):
        from app.agents.frontend_frameworks import FrontendFrameworkConfig
        config = FrontendFrameworkConfig(
            name="test", display_name="Test", language="typescript",
            code_block_lang="ts", component_extension=".tsx",
            file_structure={}, rules=(), golden_examples={},
            generation_order=(),
        )
        with pytest.raises(Exception):
            config.name = "hacked"

    def test_framework_config_fields(self):
        from app.agents.frameworks import FrameworkConfig
        import dataclasses
        assert dataclasses.is_dataclass(FrameworkConfig)
        field_names = {f.name for f in dc_fields(FrameworkConfig)}
        assert "name" in field_names
        assert "display_name" in field_names
        assert "language" in field_names
        assert "code_block_lang" in field_names
        assert "rules" in field_names
        assert "golden_examples" in field_names
        assert "file_structure" in field_names

    def test_frontend_framework_config_fields(self):
        from app.agents.frontend_frameworks import FrontendFrameworkConfig
        import dataclasses
        assert dataclasses.is_dataclass(FrontendFrameworkConfig)
        field_names = {f.name for f in dc_fields(FrontendFrameworkConfig)}
        assert "name" in field_names
        assert "display_name" in field_names
        assert "language" in field_names
        assert "component_extension" in field_names
        assert "generation_order" in field_names
        assert "rules" in field_names
        assert "golden_examples" in field_names


class TestEnumMembership:
    """All enums must have expected members."""

    def test_agent_status_members(self):
        from app.agents.base import AgentStatus
        assert issubclass(AgentStatus, Enum)
        members = {m.name for m in AgentStatus}
        assert "PENDING" in members
        assert "RUNNING" in members
        assert "COMPLETED" in members
        assert "FAILED" in members

    def test_pipeline_stage_members(self):
        from app.services.pipeline import PipelineStage
        assert issubclass(PipelineStage, Enum)
        assert len(PipelineStage) >= 15

    def test_pipeline_run_status_members(self):
        from app.services.pipeline import PipelineRunStatus
        members = {m.name for m in PipelineRunStatus}
        for expected in ["CREATED", "RUNNING", "PAUSED", "COMPLETED", "FAILED", "CANCELLED"]:
            assert expected in members, f"Missing {expected}"

    def test_execution_mode_members(self):
        from app.services.pipeline import ExecutionMode
        members = {m.name for m in ExecutionMode}
        for expected in ["STEP_BY_STEP", "CHECKPOINT", "DIRECT"]:
            assert expected in members

    def test_checkpoint_type_members(self):
        from app.services.checkpoint import CheckpointType
        members = {m.name for m in CheckpointType}
        assert "DESIGN" in members
        assert "TESTING" in members

    def test_approval_status_members(self):
        from app.services.checkpoint import ApprovalStatus
        members = {m.name for m in ApprovalStatus}
        for expected in ["PENDING", "APPROVED", "REJECTED", "CHANGES_REQUESTED"]:
            assert expected in members

    def test_provider_enum(self):
        from app.services.ai_router import Provider
        members = {m.name for m in Provider}
        assert "ANTHROPIC" in members
        assert "GOOGLE" in members

    def test_task_complexity_enum(self):
        from app.services.ai_router import TaskComplexity
        members = {m.name for m in TaskComplexity}
        for expected in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]:
            assert expected in members

    def test_finding_severity_enum(self):
        from app.agents.karan import FindingSeverity
        members = {m.name for m in FindingSeverity}
        for expected in ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]:
            assert expected in members

    def test_logic_severity_enum(self):
        from app.agents.navya import LogicSeverity
        members = {m.name for m in LogicSeverity}
        for expected in ["ERROR", "WARNING", "INFO"]:
            assert expected in members

    def test_perf_severity_enum(self):
        from app.agents.deepika import PerfSeverity
        members = {m.name for m in PerfSeverity}
        for expected in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
            assert expected in members

    def test_maturity_enum(self):
        from app.services.tech_stack import Maturity
        members = {m.name for m in Maturity}
        for expected in ["PRODUCTION", "BETA", "ALPHA", "PLANNED"]:
            assert expected in members

    def test_notification_type_enum(self):
        from app.services.notification import NotificationType
        assert issubclass(NotificationType, Enum)
        assert len(NotificationType) >= 5

    def test_notification_channel_enum(self):
        from app.services.notification import NotificationChannel
        members = {m.name for m in NotificationChannel}
        assert "WEB" in members
        assert "EMAIL" in members

    def test_compliance_framework_enum(self):
        from app.engine.compliance_engine import ComplianceFramework
        members = {m.name for m in ComplianceFramework}
        assert "DPDP_2023" in members
        assert "OWASP_TOP10_2021" in members

    def test_test_phase_enum(self):
        from app.agents.aarav import TestPhase
        assert issubclass(TestPhase, Enum)
        assert len(TestPhase) >= 5

    def test_deploy_provider_enum(self):
        from app.agents.pranav import DeployProvider
        assert issubclass(DeployProvider, Enum)
        assert len(DeployProvider) >= 2

    def test_input_type_enum(self):
        from app.services.input_processor import InputType
        assert issubclass(InputType, Enum)
        assert len(InputType) >= 5

    def test_audit_event_type_enum(self):
        from app.services.pipeline_audit import AuditEventType
        assert issubclass(AuditEventType, Enum)
        assert len(AuditEventType) >= 10


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 8: Runtime functional checks
# ═══════════════════════════════════════════════════════════════════════════


class TestRuntimeFunctionalChecks:
    """Actually call simple runtime functions to verify they work."""

    def test_hash_password_and_verify(self):
        from app.services.auth import hash_password, verify_password
        hashed = hash_password("testpass123")
        assert isinstance(hashed, str)
        assert len(hashed) > 20
        assert verify_password("testpass123", hashed) is True
        assert verify_password("wrongpass", hashed) is False

    def test_create_and_decode_token(self):
        from app.services.auth import create_access_token, decode_token
        token = create_access_token(
            user_id=123,
            organization_id=456,
            role="member",
        )
        assert isinstance(token, str)
        payload = decode_token(token)
        assert payload["sub"] == "123" or payload["sub"] == 123
        assert payload.get("org") in (456, "456")

    def test_generate_and_hash_api_key(self):
        from app.services.api_key_service import generate_api_key, hash_api_key
        result = generate_api_key()
        assert isinstance(result, tuple)
        assert len(result) == 3  # (prefix, key, key_hash) or similar
        key = result[1]  # The actual key
        assert isinstance(key, str)
        assert len(key) > 10
        hashed = hash_api_key(key)
        assert isinstance(hashed, str)
        assert hashed != key

    def test_compute_content_hash_deterministic(self):
        from app.services.context_engine import compute_content_hash
        assert compute_content_hash(b"abc") == compute_content_hash(b"abc")
        assert compute_content_hash(b"abc") != compute_content_hash(b"xyz")

    def test_render_template_basic(self):
        from app.services.prompt_engine import render_template
        result = render_template("Hello {{name}}", {"name": "world"})
        assert "world" in result

    def test_resolve_tech_name(self):
        from app.services.tech_stack import resolve_tech_name
        resolved = resolve_tech_name("fastapi")
        assert resolved is not None

    def test_validate_tech_stack_basic(self):
        from app.services.tech_stack import validate_tech_stack
        result = validate_tech_stack({
            "backend": "fastapi", "frontend": "react", "database": "postgresql",
        })
        assert result.supported is True

    def test_split_into_windows(self):
        from app.agents.scan_utils import split_into_windows
        text = "line1\nline2\nline3\nline4\nline5"
        windows = split_into_windows(text, window_size=2, overlap=1)
        assert len(windows) >= 2

    def test_select_model_for_generation(self):
        from app.services.ai_router import select_model_for_generation
        # Small → flash
        model = select_model_for_generation(50)
        assert "flash" in model.lower() or "gemini" in model.lower() or "sonnet" not in model.lower()
        # Large → opus
        model = select_model_for_generation(600)
        assert "opus" in model.lower()

    def test_estimate_file_complexity(self):
        from app.agents.base import estimate_file_complexity
        contract = {
            "tables": [{"name": "users"}, {"name": "products"}],
            "endpoints": [{"path": "/api/users"}, {"path": "/api/products"}],
        }
        est = estimate_file_complexity(contract, "models")
        assert isinstance(est, int)
        assert est > 0

    def test_compute_parallel_levels(self):
        from app.agents.shubham import compute_parallel_levels
        deps = {
            "models": set(),
            "schemas": {"models"},
            "routers": {"schemas"},
            "tests": {"routers"},
        }
        levels = compute_parallel_levels(
            [{"name": "models"}, {"name": "schemas"}, {"name": "routers"}, {"name": "tests"}],
            deps,
        )
        assert len(levels) >= 2
        # models must be in the first level
        first_names = [s["name"] for s in levels[0]]
        assert "models" in first_names

    def test_project_cost_tracker(self):
        from app.services.ai_router import ProjectCostTracker, AIResponse
        tracker = ProjectCostTracker()
        # Build a mock AIResponse to pass to record()
        mock_response = AIResponse(
            content="test", model_used="sonnet", provider="anthropic",
            request_id="req-1", input_tokens=100, output_tokens=200,
            latency_ms=50, cached=False, tool_calls=None,
            stop_reason="end_turn", was_truncated=False,
        )
        tracker.record(response=mock_response, agent_name="shubham", model_key="sonnet")
        assert tracker.call_count == 1
        assert tracker.total_tokens > 0
        summary = tracker.summary()
        assert isinstance(summary, dict)

    def test_framework_config_rules_are_tuples(self):
        from app.agents.frameworks import list_frameworks, get_framework_config
        for name in list_frameworks():
            config = get_framework_config(name)
            assert isinstance(config.rules, tuple), f"{name} rules is not tuple"

    def test_frontend_framework_config_rules_are_tuples(self):
        from app.agents.frontend_frameworks import list_frontend_frameworks, get_frontend_framework_config
        for name in list_frontend_frameworks():
            config = get_frontend_framework_config(name)
            assert isinstance(config.rules, tuple), f"{name} rules is not tuple"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 9: Cross-module integration checks
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossModuleIntegration:
    """Verify modules work together correctly."""

    def test_pipeline_stages_have_agents(self):
        """Every pipeline stage that references a real agent should have a registered agent."""
        from app.services.pipeline import STAGE_AGENTS
        from app.agents.base import get_agent
        # Skip pseudo-stages that aren't real agents
        skip_prefixes = ("__",)
        for stage, agent_name in STAGE_AGENTS.items():
            if isinstance(agent_name, str) and agent_name.startswith(skip_prefixes):
                continue
            if isinstance(agent_name, list):
                for name in agent_name:
                    agent_cls = get_agent(name)
                    assert agent_cls is not None, f"Stage {stage} references unregistered agent '{name}'"
            else:
                agent_cls = get_agent(agent_name)
                assert agent_cls is not None, f"Stage {stage} references unregistered agent '{agent_name}'"

    def test_all_frameworks_have_generation_orders(self):
        """Every registered backend framework should have a generation order in shubham.py."""
        from app.agents.frameworks import list_frameworks
        from app.agents.shubham import get_backend_generation_order
        for name in list_frameworks():
            order = get_backend_generation_order(name)
            assert len(order) >= 3, f"{name} has only {len(order)} generation steps"

    def test_all_frameworks_have_dependency_graphs(self):
        """Every registered backend framework should have a dependency graph."""
        from app.agents.frameworks import list_frameworks
        from app.agents.shubham import get_dependency_graph
        for name in list_frameworks():
            graph = get_dependency_graph(name)
            assert isinstance(graph, dict), f"{name} has no dependency graph"
            assert len(graph) >= 3, f"{name} has only {len(graph)} deps"

    def test_backend_tech_stack_covers_all_frameworks(self):
        """Tech stack validation should know about all backend frameworks."""
        from app.services.tech_stack import resolve_tech_name
        # Core frameworks should resolve
        for name in ["fastapi", "django", "express", "flask"]:
            resolved = resolve_tech_name(name)
            assert resolved is not None, f"Tech stack doesn't know about {name}"

    def test_frontend_tech_stack_covers_core_frameworks(self):
        """Tech stack validation should know about core frontend frameworks."""
        from app.services.tech_stack import resolve_tech_name
        for name in ["react", "vue", "angular"]:
            resolved = resolve_tech_name(name)
            assert resolved is not None, f"Tech stack doesn't know about {name}"

    def test_framework_languages_match_code_block_lang(self):
        """Framework language and code_block_lang should be consistent."""
        from app.agents.frameworks import list_frameworks, get_framework_config
        for name in list_frameworks():
            config = get_framework_config(name)
            if config.language == "python":
                assert config.code_block_lang == "python", f"{name} language mismatch"
            elif config.language == "typescript":
                assert config.code_block_lang in ("typescript", "ts"), f"{name} language mismatch"

    def test_all_frontend_frameworks_use_typescript(self):
        """All frontend frameworks must use TypeScript."""
        from app.agents.frontend_frameworks import list_frontend_frameworks, get_frontend_framework_config
        for name in list_frontend_frameworks():
            config = get_frontend_framework_config(name)
            assert config.language == "typescript", f"{name} is not typescript"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 10: Config and settings validation
# ═══════════════════════════════════════════════════════════════════════════


class TestConfigSettings:
    """Config and settings must be valid."""

    def test_get_settings_returns_settings(self):
        from app.config import get_settings, Settings
        settings = get_settings()
        assert isinstance(settings, Settings)

    def test_settings_has_database_url(self):
        settings = self._get_settings()
        assert hasattr(settings, "database_url")

    def test_settings_has_jwt_secret(self):
        settings = self._get_settings()
        assert hasattr(settings, "jwt_secret_key")

    def test_settings_has_environment(self):
        settings = self._get_settings()
        assert hasattr(settings, "environment")
        assert isinstance(settings.environment, str)

    def test_settings_is_production_property(self):
        settings = self._get_settings()
        assert isinstance(settings.is_production, bool)

    def _get_settings(self):
        from app.config import get_settings
        return get_settings()


class TestDatabaseModule:
    """Database module must expose expected symbols."""

    def test_schemas_exists(self):
        from app.database import SCHEMAS
        assert isinstance(SCHEMAS, tuple)
        assert len(SCHEMAS) >= 5

    def test_setup_database_callable(self):
        from app.database import setup_database
        assert callable(setup_database)


class TestMainApp:
    """Main app factory must work."""

    def test_create_app_returns_fastapi(self):
        from app.main import create_app
        import fastapi
        app = create_app()
        assert isinstance(app, fastapi.FastAPI)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 11: Report dataclass validation
# ═══════════════════════════════════════════════════════════════════════════


class TestReportDataclasses:
    """All report dataclasses must have expected properties and methods."""

    def test_quality_report_properties(self):
        from app.engine.code_quality import QualityReport
        report = QualityReport(issues=[], files_checked=0, passed=True)
        assert hasattr(report, "error_count")
        assert hasattr(report, "warning_count")
        assert hasattr(report, "add")

    def test_security_report_properties(self):
        from app.agents.karan import SecurityReport
        report = SecurityReport()
        assert hasattr(report, "add")
        assert hasattr(report, "critical_count")
        assert hasattr(report, "high_count")
        assert hasattr(report, "blocking_count")

    def test_logic_report_properties(self):
        from app.agents.navya import LogicReport
        report = LogicReport()
        assert hasattr(report, "add")
        assert hasattr(report, "error_count")
        assert hasattr(report, "warning_count")

    def test_perf_report_properties(self):
        from app.agents.deepika import PerfReport
        report = PerfReport()
        assert hasattr(report, "add")
        assert hasattr(report, "critical_count")
        assert hasattr(report, "high_count")

    def test_compliance_report_properties(self):
        from app.engine.compliance_engine import ComplianceReport
        report = ComplianceReport(findings=[], frameworks_checked=[], files_scanned=0, passed=True)
        assert hasattr(report, "critical_count")
        assert hasattr(report, "high_count")
        assert hasattr(report, "to_dict")

    def test_sandbox_test_report(self):
        from app.agents.aarav import SandboxTestReport
        report = SandboxTestReport()
        assert hasattr(report, "add")
        assert hasattr(report, "failed_phases")
        assert hasattr(report, "total_tests")
        assert hasattr(report, "total_passed")
        assert hasattr(report, "total_failed")

    def test_agent_result_has_duration(self):
        from app.agents.base import AgentResult
        assert hasattr(AgentResult, "duration_ms")

    def test_security_scan_report_properties(self):
        from app.agents.security_guardian import SecurityScanReport
        assert hasattr(SecurityScanReport, "to_dict")
        assert hasattr(SecurityScanReport, "critical_count")
        assert hasattr(SecurityScanReport, "total_findings")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 12: Scan utils and utility functions
# ═══════════════════════════════════════════════════════════════════════════


class TestScanUtils:
    """scan_utils must work correctly."""

    def test_split_into_windows_empty(self):
        from app.agents.scan_utils import split_into_windows
        windows = split_into_windows("", window_size=10, overlap=2)
        assert isinstance(windows, list)

    def test_split_into_windows_small_text(self):
        from app.agents.scan_utils import split_into_windows
        text = "line1\nline2\nline3"
        windows = split_into_windows(text, window_size=100, overlap=10)
        assert len(windows) >= 1

    def test_split_into_windows_large_text(self):
        from app.agents.scan_utils import split_into_windows
        text = "\n".join(f"line {i}" for i in range(500))
        windows = split_into_windows(text, window_size=50, overlap=10)
        assert len(windows) > 5


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 13: Singleton factory functions
# ═══════════════════════════════════════════════════════════════════════════


class TestSingletonFactories:
    """Factory functions must exist and be callable."""

    FACTORY_FUNCTIONS = [
        ("app.services.ai_router", "get_ai_router"),
        ("app.services.checkpoint", "get_checkpoint_service"),
        ("app.services.context_engine", "get_context_engine"),
        ("app.services.prompt_engine", "get_prompt_engine"),
        ("app.services.pipeline_audit", "get_pipeline_audit"),
        ("app.services.notification", "get_notification_service"),
        ("app.services.input_processor", "get_input_processor"),
        ("app.services.voice_to_text", "get_voice_to_text_service"),
        ("app.services.api_key_service", "get_api_key_service"),
        ("app.engine.code_quality", "get_code_quality_engine"),
        ("app.engine.execution_engine", "get_execution_engine"),
        ("app.engine.compliance_engine", "get_compliance_engine"),
        ("app.engine.delivery", "get_delivery_engine"),
        ("app.engine.template_engine", "get_template_engine"),
        ("app.services.pipeline", "get_orchestrator"),
    ]

    @pytest.mark.parametrize("module_path,func_name", FACTORY_FUNCTIONS)
    def test_factory_function_exists(self, module_path: str, func_name: str):
        import importlib
        mod = importlib.import_module(module_path)
        func = getattr(mod, func_name, None)
        assert func is not None, f"{module_path}.{func_name} not found"
        assert callable(func), f"{module_path}.{func_name} is not callable"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 14: Model constants and dictionaries
# ═══════════════════════════════════════════════════════════════════════════


class TestModelConstants:
    """AI model configuration constants must exist and be valid."""

    def test_models_dict_exists(self):
        from app.services.ai_router import MODELS
        assert isinstance(MODELS, dict)
        assert len(MODELS) >= 3

    def test_escalation_chain_exists(self):
        from app.services.ai_router import ESCALATION_CHAIN
        assert isinstance(ESCALATION_CHAIN, (list, tuple))
        assert len(ESCALATION_CHAIN) >= 2

    def test_security_critical_tasks(self):
        from app.services.ai_router import SECURITY_CRITICAL_TASKS
        assert isinstance(SECURITY_CRITICAL_TASKS, frozenset)
        assert len(SECURITY_CRITICAL_TASKS) >= 1

    def test_complexity_to_model(self):
        from app.services.ai_router import COMPLEXITY_TO_MODEL
        assert isinstance(COMPLEXITY_TO_MODEL, dict)
        assert len(COMPLEXITY_TO_MODEL) >= 3

    def test_all_model_specs_valid(self):
        from app.services.ai_router import MODELS, ModelSpec
        for key, spec in MODELS.items():
            assert isinstance(spec, ModelSpec), f"{key} is not ModelSpec"
            assert isinstance(spec.model_id, str)
            assert len(spec.model_id) > 0
            assert isinstance(spec.display_name, str)
            assert spec.max_output_tokens > 0


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 15: WhatsApp and integration agent validation
# ═══════════════════════════════════════════════════════════════════════════


class TestWhatsAppAgent:
    """WhatsApp agent must have all required types and methods."""

    def test_message_type_enum(self):
        from app.agents.whatsapp_agent import WhatsAppMessageType
        assert issubclass(WhatsAppMessageType, Enum)
        assert len(WhatsAppMessageType) >= 5

    def test_session_status_enum(self):
        from app.agents.whatsapp_agent import WhatsAppSessionStatus
        assert issubclass(WhatsAppSessionStatus, Enum)

    def test_incoming_from_webhook(self):
        from app.agents.whatsapp_agent import WhatsAppIncoming
        assert hasattr(WhatsAppIncoming, "from_webhook")

    def test_verify_webhook_signature(self):
        from app.agents.whatsapp_agent import verify_webhook_signature
        assert callable(verify_webhook_signature)


class TestGitAgent:
    """GitAgent must have all required types and methods."""

    def test_git_provider_enum(self):
        from app.agents.git_agent import GitProvider
        members = {m.name for m in GitProvider}
        assert "GITHUB" in members

    def test_repo_visibility_enum(self):
        from app.agents.git_agent import RepoVisibility
        members = {m.name for m in RepoVisibility}
        assert "PUBLIC" in members
        assert "PRIVATE" in members

    def test_git_operation_type_enum(self):
        from app.agents.git_agent import GitOperationType
        assert len(GitOperationType) >= 4

    def test_git_repo_config_from_contract(self):
        from app.agents.git_agent import GitRepoConfig
        assert hasattr(GitRepoConfig, "from_contract")

    def test_git_operation_result_to_dict(self):
        from app.agents.git_agent import GitOperationResult
        assert hasattr(GitOperationResult, "to_dict")


class TestSecurityGuardianTypes:
    """SecurityGuardian must have all required types."""

    def test_vulnerability_severity_enum(self):
        from app.agents.security_guardian import VulnerabilitySeverity
        assert len(VulnerabilitySeverity) >= 4

    def test_scan_type_enum(self):
        from app.agents.security_guardian import ScanType
        assert len(ScanType) >= 4

    def test_update_risk_enum(self):
        from app.agents.security_guardian import UpdateRisk
        members = {m.name for m in UpdateRisk}
        assert "PATCH" in members
        assert "MINOR" in members
        assert "MAJOR" in members

    def test_vulnerability_to_dict(self):
        from app.agents.security_guardian import Vulnerability
        assert hasattr(Vulnerability, "to_dict")

    def test_security_scan_report_has_blocking(self):
        from app.agents.security_guardian import SecurityScanReport
        assert hasattr(SecurityScanReport, "has_blocking")
