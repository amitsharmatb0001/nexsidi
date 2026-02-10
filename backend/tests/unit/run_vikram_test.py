
import sys
import os
# Add project root to sys.path
sys.path.append(r"e:\nexsidi\backend")
import asyncio
import unittest
import json
from unittest.mock import MagicMock, AsyncMock

# --- PRE-IMPORT MOCKING ---
# Mock app.services.ai_router
mock_ai_router_module = MagicMock()
mock_ai_router = MagicMock()
mock_ai_router.generate = AsyncMock()
mock_ai_router_module.ai_router = mock_ai_router

# Mock TaskComplexity
class MockTaskComplexity:
    MOST_COMPLEX = "most_complex"
mock_ai_router_module.TaskComplexity = MockTaskComplexity

sys.modules["app.services.ai_router"] = mock_ai_router_module

# Mock app.services.context_engine
mock_context_engine_module = MagicMock()
mock_context_engine = MagicMock()
mock_context_engine_module.context_engine = mock_context_engine
sys.modules["app.services.context_engine"] = mock_context_engine_module

# Mock app.agents.mixins
mock_mixins_module = MagicMock()
class MistakeMemoryMixin: pass
class PermanentMemoryMixin: pass
class ContextManagementMixin: pass
class DecisionLedgerMixin: pass
class SearchCapableMixin: pass

mock_mixins_module.MistakeMemoryMixin = MistakeMemoryMixin
mock_mixins_module.PermanentMemoryMixin = PermanentMemoryMixin
mock_mixins_module.ContextManagementMixin = ContextManagementMixin
mock_mixins_module.DecisionLedgerMixin = DecisionLedgerMixin
mock_mixins_module.SearchCapableMixin = SearchCapableMixin
sys.modules["app.agents.mixins"] = mock_mixins_module


# Mock app.utils.json_utils
mock_json_utils = MagicMock()
def safe_json_parse(x): return json.loads(x)
mock_json_utils.safe_json_parse = safe_json_parse
sys.modules["app.utils.json_utils"] = mock_json_utils

# --- IMPORT VIKRAM ---
# (Now it should be safe to import)
from app.agents.vikram import Vikram

# --- TEST DATA ---
MOCK_BLUEPRINT = {
  "project_name": "test-project",
  "tech_stack": {"backend": "FastAPI"},
  "services": [{"name": "auth", "folder_structure": {"src/auth": "..."}}],
  "api_endpoints": [{"path": "/login"}],
  "database_schema": {"tables": []}
}

class TestVikram(unittest.TestCase):
    def setUp(self):
        self.vikram = Vikram("p1", "u1")
        
        # Reset mocks
        mock_ai_router.generate.reset_mock()
        mock_context_engine.store_context.reset_mock()

    def test_design_architecture(self):
        # Setup mock response
        mock_response = MagicMock()
        mock_response.content = json.dumps(MOCK_BLUEPRINT)
        mock_ai_router.generate.return_value = mock_response
        
        # Call
        result = asyncio.run(self.vikram.design_architecture({"desc": "test"}))
        
        # Verify
        self.assertEqual(result["project_name"], "test-project")
        mock_ai_router.generate.assert_called_once()
        mock_context_engine.store_context.assert_called()

    def test_helper_methods(self):
        # Setup mock response for decide_tech_stack
        mock_response = MagicMock()
        mock_response.content = json.dumps(MOCK_BLUEPRINT)
        mock_ai_router.generate.return_value = mock_response

        # Tech Stack
        stack = asyncio.run(self.vikram.decide_tech_stack({"desc": "test"}))
        self.assertEqual(stack["backend"], "FastAPI")

        # Helpers (no AI call needed if passed blueprint)
        struct = asyncio.run(self.vikram.generate_folder_structure(MOCK_BLUEPRINT))
        self.assertIn("auth", struct)

if __name__ == "__main__":
    unittest.main()
