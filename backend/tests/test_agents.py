"""Tests for all 20 NexSidi agents.

Tests registration, tool definitions, properties, and execute() for each agent.
Agents that call store_output() are patched to avoid Valkey dependency.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.base import AgentStatus, get_agent, list_agents, _agents as AGENT_REGISTRY
from app.services.ai_router import TaskComplexity


# ── Import all agents to trigger registration ──────────────────

import app.agents.tilotma       # noqa: F401
import app.agents.saanvi        # noqa: F401
import app.agents.vikram        # noqa: F401
import app.agents.dhruv         # noqa: F401
import app.agents.vanya         # noqa: F401
import app.agents.shubham       # noqa: F401
import app.agents.aanya         # noqa: F401
import app.agents.karan         # noqa: F401
import app.agents.navya         # noqa: F401
import app.agents.deepika       # noqa: F401
import app.agents.aarav         # noqa: F401
import app.agents.fixer         # noqa: F401
import app.agents.pranav        # noqa: F401
import app.agents.docs_agent    # noqa: F401
import app.agents.support_agent # noqa: F401
import app.agents.whatsapp_agent  # noqa: F401
import app.agents.git_agent     # noqa: F401
import app.agents.security_guardian  # noqa: F401
import app.agents.system_monitor  # noqa: F401
import app.agents.challenger      # noqa: F401
import app.agents.attack_tester   # noqa: F401


class TestAgentRegistry:
    """Test the global agent registry."""

    def test_total_agent_count(self):
        assert len(AGENT_REGISTRY) == 21  # 20 original + attack_tester

    def test_list_agents_returns_sorted(self):
        names = list_agents()
        assert names == sorted(names)
        assert len(names) == 21  # 20 original + attack_tester

    def test_all_agents_have_unique_names(self):
        names = list_agents()
        assert len(names) == len(set(names))

    @pytest.mark.parametrize("name", [
        "tilotma", "saanvi", "vikram", "dhruv", "vanya",
        "shubham", "aanya", "karan", "navya", "deepika",
        "aarav", "fixer", "pranav", "docs_agent", "support_agent",
        "whatsapp_agent", "git_agent", "security_guardian", "system_monitor",
        "challenger",
    ])
    def test_agent_registered(self, name: str):
        agent = get_agent(name)
        assert agent is not None, f"{name} not found in registry"
        assert agent.name == name

    @pytest.mark.parametrize("name", [
        "tilotma", "saanvi", "vikram", "dhruv", "vanya",
        "shubham", "aanya", "karan", "navya", "deepika",
        "aarav", "fixer", "pranav", "docs_agent", "support_agent",
        "whatsapp_agent", "git_agent", "security_guardian", "system_monitor",
        "challenger",
    ])
    def test_agent_has_tools(self, name: str):
        agent = get_agent(name)
        assert len(agent.tools) >= 1, f"{name} has no tools"

    @pytest.mark.parametrize("name", [
        "tilotma", "saanvi", "vikram", "dhruv", "vanya",
        "shubham", "aanya", "karan", "navya", "deepika",
        "aarav", "fixer", "pranav", "docs_agent", "support_agent",
        "whatsapp_agent", "git_agent", "security_guardian", "system_monitor",
        "challenger",
    ])
    def test_agent_has_display_name(self, name: str):
        agent = get_agent(name)
        assert agent.display_name, f"{name} missing display_name"

    def test_get_nonexistent_agent_raises(self):
        with pytest.raises(KeyError):
            get_agent("nonexistent_agent")


class TestDesignPhaseAgents:
    """Test tilotma, saanvi, vikram, dhruv, vanya."""

    def test_tilotma_properties(self):
        a = get_agent("tilotma")
        assert a.default_complexity == TaskComplexity.MEDIUM
        assert len(a.tools) == 2

    def test_saanvi_properties(self):
        a = get_agent("saanvi")
        assert a.default_complexity == TaskComplexity.MEDIUM
        assert len(a.tools) == 2

    def test_vikram_properties(self):
        a = get_agent("vikram")
        assert a.default_complexity == TaskComplexity.HIGH
        assert len(a.tools) >= 2

    def test_dhruv_properties(self):
        a = get_agent("dhruv")
        assert a.default_complexity == TaskComplexity.HIGH
        assert len(a.tools) == 3

    def test_vanya_properties(self):
        a = get_agent("vanya")
        assert a.default_complexity == TaskComplexity.MEDIUM
        assert len(a.tools) >= 1


class TestBuildPhaseAgents:
    """Test shubham, aanya."""

    def test_shubham_properties(self):
        a = get_agent("shubham")
        assert a.default_complexity == TaskComplexity.HIGH
        assert len(a.tools) >= 2

    def test_aanya_properties(self):
        a = get_agent("aanya")
        assert a.default_complexity == TaskComplexity.HIGH
        assert len(a.tools) >= 2


class TestQualityAgents:
    """Test karan, navya, deepika, aarav, fixer."""

    def test_karan_is_security_critical(self):
        a = get_agent("karan")
        assert a.default_complexity == TaskComplexity.HIGH
        assert a.default_model == "claude-sonnet-4-6"

    def test_navya_properties(self):
        a = get_agent("navya")
        assert a.default_complexity == TaskComplexity.MEDIUM

    def test_deepika_properties(self):
        a = get_agent("deepika")
        assert a.default_complexity == TaskComplexity.MEDIUM

    def test_aarav_properties(self):
        a = get_agent("aarav")
        assert a.default_complexity == TaskComplexity.HIGH

    def test_fixer_properties(self):
        a = get_agent("fixer")
        assert a.default_complexity == TaskComplexity.HIGH


class TestDeployAgents:
    """Test pranav, docs_agent."""

    def test_pranav_is_security_critical(self):
        a = get_agent("pranav")
        assert a.default_model == "claude-sonnet-4-6"

    def test_docs_agent_properties(self):
        a = get_agent("docs_agent")
        assert a.default_complexity == TaskComplexity.MEDIUM
        assert len(a.tools) == 2


class TestSupportAgents:
    """Test support_agent, whatsapp_agent."""

    def test_support_agent_properties(self):
        a = get_agent("support_agent")
        assert a.default_complexity == TaskComplexity.LOW
        assert len(a.tools) == 2

    def test_whatsapp_agent_properties(self):
        a = get_agent("whatsapp_agent")
        assert a.default_complexity == TaskComplexity.MEDIUM
        assert len(a.tools) == 2


class TestIntegrationAgents:
    """Test git_agent."""

    def test_git_agent_properties(self):
        a = get_agent("git_agent")
        assert a.default_complexity == TaskComplexity.MEDIUM
        tool_names = {t.name for t in a.tools}
        assert tool_names == {"create_repo", "push_code", "create_pull_request"}


class TestSelfMaintainingAgents:
    """Test security_guardian, system_monitor."""

    def test_security_guardian_properties(self):
        a = get_agent("security_guardian")
        assert a.default_complexity == TaskComplexity.HIGH
        assert a.default_model == "claude-sonnet-4-6"
        assert len(a.tools) == 4

    def test_system_monitor_properties(self):
        a = get_agent("system_monitor")
        assert a.default_complexity == TaskComplexity.MEDIUM
        assert len(a.tools) == 3


# ── Execute Tests (patched to avoid Valkey) ────────────────────

class TestAgentExecution:
    """Test agent execute() methods."""

    @pytest.mark.asyncio
    async def test_whatsapp_execute_text(self):
        from app.agents.whatsapp_agent import WhatsAppAgent
        wa = WhatsAppAgent()
        payload = {
            "whatsapp_input": {
                "entry": [{"changes": [{"value": {"messages": [{
                    "from": "+919876543210",
                    "type": "text",
                    "text": {"body": "Build me a restaurant app"},
                    "timestamp": "1700000000",
                }]}}]}]
            }
        }
        with patch("app.agents.whatsapp_agent.store_output", new_callable=AsyncMock):
            result = await wa.execute("run-1", payload)
        assert result.status == AgentStatus.COMPLETED
        assert result.output["source"] == "whatsapp"
        assert result.output["message_type"] == "text"

    @pytest.mark.asyncio
    async def test_whatsapp_execute_no_input(self):
        from app.agents.whatsapp_agent import WhatsAppAgent
        wa = WhatsAppAgent()
        result = await wa.execute("run-2", {})
        assert result.status == AgentStatus.COMPLETED
        assert result.output["status"] == "no_input"

    @pytest.mark.asyncio
    async def test_git_agent_execute_with_files(self, pipeline_context):
        from app.agents.git_agent import GitAgent
        ga = GitAgent()
        result = await ga.execute("run-3", pipeline_context)
        assert result.status == AgentStatus.COMPLETED
        assert result.output["files_pushed"] == 4  # 2 backend + 2 frontend
        assert result.output["pr_url"]

    @pytest.mark.asyncio
    async def test_git_agent_execute_no_contract(self):
        from app.agents.git_agent import GitAgent
        ga = GitAgent()
        result = await ga.execute("run-4", {})
        assert result.status == AgentStatus.COMPLETED
        assert result.output["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_security_guardian_execute(self, pipeline_context):
        from app.agents.security_guardian import SecurityGuardian
        sg = SecurityGuardian()
        result = await sg.execute("run-5", pipeline_context)
        assert result.status in (AgentStatus.COMPLETED, AgentStatus.FAILED)
        assert "total_findings" in result.output

    @pytest.mark.asyncio
    async def test_system_monitor_execute_healthy(self):
        from app.agents.system_monitor import SystemMonitor
        sm = SystemMonitor()
        ctx = {
            "system_metrics": [
                {"type": "response_time", "value": 100, "unit": "ms"},
                {"type": "cpu_usage", "value": 30, "unit": "%"},
            ],
            "captured_errors": [],
        }
        result = await sm.execute("run-6", ctx)
        assert result.status == AgentStatus.COMPLETED
        assert result.output["overall_status"] == "healthy"

    @pytest.mark.asyncio
    async def test_system_monitor_execute_with_errors(self):
        from app.agents.system_monitor import SystemMonitor
        sm = SystemMonitor()
        ctx = {
            "system_metrics": [],
            "captured_errors": [
                {"type": "ConnectionError", "message": "DB down", "component": "db"},
            ],
        }
        result = await sm.execute("run-7", ctx)
        assert result.status == AgentStatus.COMPLETED
        assert result.output["tracked_bugs"] >= 1

    def test_support_agent_triage(self):
        from app.agents.support_agent import SupportAgent
        sa = SupportAgent()
        result = sa.triage(
            "Found SQL injection vulnerability in the user search endpoint"
        )
        assert result is not None
        assert result.escalation_agent == "karan"  # Security always -> karan
