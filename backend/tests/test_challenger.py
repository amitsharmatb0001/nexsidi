"""Tests for the Challenger agent — adversarial architecture reviewer."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.base import AgentResult, AgentStatus
from app.agents.challenger import (
    MAX_CHALLENGE_RETRIES,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    Challenge,
    Challenger,
)


class TestChallenge:
    """Test the Challenge data model."""

    def test_challenge_creation(self):
        c = Challenge(
            category="security_gap",
            severity=SEVERITY_CRITICAL,
            description="No auth defined",
            recommendation="Add JWT auth",
        )
        assert c.category == "security_gap"
        assert c.severity == "critical"
        assert c.description == "No auth defined"
        assert c.recommendation == "Add JWT auth"

    def test_challenge_to_dict(self):
        c = Challenge(
            category="over_engineering",
            severity=SEVERITY_MEDIUM,
            description="Too many services",
            recommendation="Use a monolith",
        )
        d = c.to_dict()
        assert d == {
            "category": "over_engineering",
            "severity": "medium",
            "description": "Too many services",
            "recommendation": "Use a monolith",
        }


class TestChallengerAgent:
    """Test the Challenger agent initialization and registration."""

    def test_agent_name(self):
        agent = Challenger()
        assert agent.name == "challenger"

    def test_agent_display_name(self):
        agent = Challenger()
        assert "Challenger" in agent.display_name

    def test_has_raise_challenge_tool(self):
        agent = Challenger()
        tool_names = [t.name for t in agent.tools]
        assert "raise_challenge" in tool_names

    def test_tool_has_required_parameters(self):
        agent = Challenger()
        tool = agent._tools["raise_challenge"]
        required = tool.parameters["required"]
        assert "category" in required
        assert "severity" in required
        assert "description" in required
        assert "recommendation" in required


class TestOverEngineeringChecks:
    """Test rule-based over-engineering detection."""

    def test_too_many_services_for_few_endpoints(self):
        agent = Challenger()
        contract = {
            "services": ["svc1", "svc2", "svc3", "svc4", "svc5", "svc6"],
            "api": {"endpoints": ["/a", "/b", "/c"]},
        }
        challenges = agent._check_over_engineering(contract)
        assert len(challenges) >= 1
        assert any(c.category == "over_engineering" for c in challenges)
        assert any(c.severity == SEVERITY_HIGH for c in challenges)

    def test_no_over_engineering_few_services(self):
        agent = Challenger()
        contract = {
            "services": ["svc1", "svc2"],
            "api": {"endpoints": ["/a", "/b", "/c"]},
        }
        challenges = agent._check_over_engineering(contract)
        # Should not flag over-engineering for 2 services
        svc_challenges = [c for c in challenges if "services" in c.description.lower()]
        assert len(svc_challenges) == 0

    def test_graphql_plus_rest(self):
        agent = Challenger()
        contract = {
            "tech_stack": {"api": "GraphQL"},
            "api": {"endpoints": ["/rest/v1/users"]},
        }
        challenges = agent._check_over_engineering(contract)
        assert any("GraphQL" in c.description for c in challenges)

    def test_kafka_for_simple_app(self):
        agent = Challenger()
        contract = {
            "tech_stack": {"messaging": "Kafka"},
            "api": {"endpoints": ["/a"]},
        }
        challenges = agent._check_over_engineering(contract)
        assert any("Kafka" in c.description for c in challenges)


class TestSecurityGapChecks:
    """Test rule-based security gap detection."""

    def test_no_auth_with_endpoints(self):
        agent = Challenger()
        contract = {
            "auth": {},
            "api": {"endpoints": ["/users", "/orders"]},
            "security": {},
        }
        challenges = agent._check_security_gaps(contract)
        critical = [c for c in challenges if c.severity == SEVERITY_CRITICAL]
        assert len(critical) >= 1
        assert any("authentication" in c.description.lower() for c in critical)

    def test_auth_present_no_critical(self):
        agent = Challenger()
        contract = {
            "auth": {"type": "jwt", "provider": "auth0"},
            "api": {"endpoints": ["/users"]},
            "security": {"rate_limiting": True},
        }
        challenges = agent._check_security_gaps(contract)
        critical = [c for c in challenges if c.severity == SEVERITY_CRITICAL]
        assert len(critical) == 0

    def test_no_rate_limiting(self):
        agent = Challenger()
        contract = {
            "auth": {"type": "jwt"},
            "api": {"endpoints": [f"/ep{i}" for i in range(10)]},
            "security": {},
        }
        challenges = agent._check_security_gaps(contract)
        assert any("rate limiting" in c.description.lower() for c in challenges)

    def test_file_upload_without_validation(self):
        agent = Challenger()
        contract = {
            "auth": {"type": "jwt"},
            "api": {"endpoints": ["/upload/file", "/users"]},
            "security": {},
        }
        challenges = agent._check_security_gaps(contract)
        assert any("upload" in c.description.lower() for c in challenges)


class TestMissingEdgeCaseChecks:
    """Test rule-based missing edge case detection."""

    def test_no_error_pages(self):
        agent = Challenger()
        contract = {
            "frontend": {
                "pages": ["home", "dashboard", "settings"],
            },
        }
        challenges = agent._check_missing_edge_cases(contract)
        assert any("error" in c.description.lower() for c in challenges)

    def test_error_pages_present(self):
        agent = Challenger()
        contract = {
            "frontend": {
                "pages": ["home", "dashboard", "404_error", "settings"],
            },
        }
        challenges = agent._check_missing_edge_cases(contract)
        error_challenges = [c for c in challenges if "error pages" in c.description.lower()]
        assert len(error_challenges) == 0

    def test_no_loading_states(self):
        agent = Challenger()
        contract = {
            "frontend": {
                "pages": ["home", "dashboard"],
            },
        }
        challenges = agent._check_missing_edge_cases(contract)
        assert any("loading" in c.description.lower() for c in challenges)


class TestContradictionChecks:
    """Test rule-based contradiction detection."""

    def test_realtime_without_websocket(self):
        agent = Challenger()
        contract = {
            "requirements": "Build a real-time chat application",
            "tech_stack": {"backend": "FastAPI"},
        }
        challenges = agent._check_contradictions(contract)
        assert any("real-time" in c.description.lower() for c in challenges)

    def test_realtime_with_websocket(self):
        agent = Challenger()
        contract = {
            "requirements": "Build a real-time chat application",
            "tech_stack": {"backend": "FastAPI", "transport": "WebSocket"},
        }
        challenges = agent._check_contradictions(contract)
        # WebSocket is mentioned in contract, so no contradiction
        realtime = [c for c in challenges if "real-time" in c.description.lower()]
        assert len(realtime) == 0


class TestScalabilityChecks:
    """Test rule-based scalability concern detection."""

    def test_many_models_no_indexes(self):
        agent = Challenger()
        contract = {
            "database": {
                "models": [
                    {"name": "users"},
                    {"name": "orders"},
                    {"name": "products"},
                    {"name": "reviews"},
                    {"name": "categories"},
                    {"name": "tags"},
                ],
            },
        }
        challenges = agent._check_scalability(contract)
        assert any("index" in c.description.lower() for c in challenges)

    def test_models_with_indexes(self):
        agent = Challenger()
        contract = {
            "database": {
                "models": [
                    {"name": "users", "indexes": ["email"]},
                    {"name": "orders"},
                    {"name": "products"},
                    {"name": "reviews"},
                    {"name": "categories"},
                    {"name": "tags"},
                ],
            },
        }
        challenges = agent._check_scalability(contract)
        index_challenges = [c for c in challenges if "index" in c.description.lower()]
        assert len(index_challenges) == 0


class TestChallengerExecution:
    """Test the full execute() method."""

    @pytest.mark.asyncio
    async def test_execute_no_vikram_output(self):
        agent = Challenger()
        result = await agent.execute("run-1", {})
        assert result.status == AgentStatus.FAILED
        assert "No architecture contract" in result.error

    @pytest.mark.asyncio
    async def test_execute_with_clean_contract(self):
        """A well-designed contract should pass with few/no issues."""
        agent = Challenger()
        contract = {
            "contract": {
                "auth": {"type": "jwt"},
                "api": {"endpoints": ["/users"]},
                "security": {"rate_limiting": True, "file_validation": True},
                "frontend": {"pages": ["home", "404_error", "loading_skeleton"]},
                "database": {"models": [{"name": "users"}]},
                "tech_stack": {"backend": "FastAPI"},
                "requirements": "Simple CRUD app",
            },
        }
        # Patch call_ai to avoid actual AI calls
        with patch("app.agents.challenger.call_ai", side_effect=Exception("No AI in tests")):
            with patch("app.agents.challenger.store_output", new_callable=AsyncMock):
                result = await agent.execute("run-1", {"vikram": contract})

        assert result.status == AgentStatus.COMPLETED
        assert result.output["verdict"] in ("pass", "warn")

    @pytest.mark.asyncio
    async def test_execute_with_insecure_contract(self):
        """A contract with no auth should get critical challenges."""
        agent = Challenger()
        contract = {
            "contract": {
                "auth": {},
                "api": {"endpoints": ["/users", "/orders", "/upload/file"]},
                "security": {},
                "frontend": {"pages": ["home"]},
                "database": {"models": []},
                "tech_stack": {},
                "requirements": "",
            },
        }
        with patch("app.agents.challenger.call_ai", side_effect=Exception("No AI")):
            with patch("app.agents.challenger.store_output", new_callable=AsyncMock):
                result = await agent.execute("run-1", {"vikram": contract})

        assert result.status == AgentStatus.COMPLETED
        assert result.output["has_critical"] is True
        assert result.output["verdict"] == "reject"
        assert result.output["critical_count"] >= 1

    @pytest.mark.asyncio
    async def test_execute_deduplicates_challenges(self):
        """Duplicate challenges (same description) should be removed."""
        agent = Challenger()
        contract = {
            "contract": {
                "auth": {},
                "api": {"endpoints": ["/users"]},
                "security": {},
                "frontend": {"pages": []},
                "database": {"models": []},
                "tech_stack": {},
                "requirements": "",
            },
        }
        with patch("app.agents.challenger.call_ai", side_effect=Exception("No AI")):
            with patch("app.agents.challenger.store_output", new_callable=AsyncMock):
                result = await agent.execute("run-1", {"vikram": contract})

        # Check no duplicate descriptions
        descriptions = [c["description"] for c in result.output["challenges"]]
        assert len(descriptions) == len(set(descriptions))

    @pytest.mark.asyncio
    async def test_execute_counts_by_severity(self):
        """Output should have correct severity counts."""
        agent = Challenger()
        contract = {
            "contract": {
                "auth": {},
                "api": {"endpoints": [f"/ep{i}" for i in range(10)]},
                "security": {},
                "frontend": {"pages": ["home"]},
                "database": {"models": []},
                "tech_stack": {},
                "requirements": "",
            },
        }
        with patch("app.agents.challenger.call_ai", side_effect=Exception("No AI")):
            with patch("app.agents.challenger.store_output", new_callable=AsyncMock):
                result = await agent.execute("run-1", {"vikram": contract})

        output = result.output
        total = output["critical_count"] + output["high_count"] + output["medium_count"] + output["low_count"]
        assert total == output["total_challenges"]
