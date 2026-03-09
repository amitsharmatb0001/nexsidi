"""NexSidi AI Agents — the 18-stage pipeline workforce.

Each agent is a module in this package. Agents are registered with the
pipeline orchestrator at import time via register_agent() in base.py.

Agent categories:
- Requirements: Tilotma (gathering), Saanvi (analysis)
- Design: Vikram (architecture), Dhruv (database), Vanya (UI/UX)
- Build: Shubham (backend), Aanya (frontend)
- Quality: Karan (security), Navya (logic), Deepika (performance),
           Challenger (architecture review), AttackTester (attack surface)
- Test: Aarav (sandbox execution), Fixer (auto-fix loop)
- Deploy: Pranav (deployment), DocsAgent (documentation), GitAgent (git ops)
- Support: WhatsAppAgent, SupportAgent, SecurityGuardian, SystemMonitor
"""

import logging

logger = logging.getLogger(__name__)


def register_all_agents() -> int:
    """Import all agent modules to trigger register_agent() calls.

    Returns the number of agents successfully registered.
    Called during FastAPI lifespan startup.
    """
    # Agent modules that call register_agent() at module level.
    # Each import triggers the singleton creation + registration.
    _agent_modules = [
        "app.agents.tilotma",
        "app.agents.saanvi",
        "app.agents.vikram",
        "app.agents.dhruv",
        "app.agents.vanya",
        "app.agents.shubham",
        "app.agents.aanya",
        "app.agents.karan",
        "app.agents.navya",
        "app.agents.deepika",
        "app.agents.challenger",
        "app.agents.attack_tester",
        "app.agents.aarav",
        "app.agents.fixer",
        "app.agents.pranav",
        "app.agents.docs_agent",
        "app.agents.git_agent",
        # MVP-FIX: WhatsApp agent disabled — not needed for internal use.
        # Behind ENABLE_WHATSAPP feature flag anyway.  Re-enable for B2C.
        # "app.agents.whatsapp_agent",
        # V5-FIX (DISCONNECT-2): support_agent is registered but never wired
        # into any pipeline stage.  No stage produces "support_requests" context.
        # It wastes import time + LLM context.  Re-enable when support ticket
        # pipeline is implemented.
        # "app.agents.support_agent",
        "app.agents.security_guardian",
        "app.agents.system_monitor",
    ]

    # AGENT-IMPORT-FIX: Mandatory agents MUST import successfully.
    # If a pipeline-critical agent fails to import (e.g., syntax error in
    # shubham.py), the pipeline will start with a missing agent and fail
    # confusingly downstream. Utility agents (whatsapp, support, etc.) are
    # non-fatal — they're not part of the core pipeline.
    _MANDATORY_AGENTS = frozenset({
        "app.agents.tilotma",
        "app.agents.saanvi",
        "app.agents.vikram",
        "app.agents.dhruv",
        "app.agents.vanya",
        "app.agents.shubham",
        "app.agents.aanya",
        "app.agents.karan",
        "app.agents.navya",
        "app.agents.deepika",
        "app.agents.aarav",
        "app.agents.fixer",
        "app.agents.pranav",
        "app.agents.planner",  # P1-1: Agentic pipeline mode requires planner
    })

    imported = 0
    for module_name in _agent_modules:
        try:
            __import__(module_name)
            imported += 1
        except Exception as exc:
            if module_name in _MANDATORY_AGENTS:
                # FATAL: Pipeline-critical agent — re-raise so startup fails loudly
                logger.error("FATAL: mandatory agent import failed: %s (%s)", module_name, exc)
                raise ImportError(
                    f"Mandatory agent {module_name} failed to import: {exc}"
                ) from exc
            else:
                # Non-fatal: utility agent won't be available but pipeline continues
                logger.warning("agent_import_failed: %s (%s)", module_name, exc)

    from app.agents.base import list_agents
    registered = list_agents()
    logger.info("agents_registered: %d imported, %d registered: %s", imported, len(registered), registered)
    return len(registered)
