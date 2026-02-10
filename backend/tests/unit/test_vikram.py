
import unittest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import json

# Import Vikram and dependencies
from app.agents.vikram import Vikram, ai_router, context_engine
from app.services.ai_router import AIResponse

# Mock Blueprint
MOCK_BLUEPRINT = {
  "project_name": "food-delivery-app",
  "description": "A food delivery platform.",
  "architecture_style": "modular_monolith",
  "tech_stack": {
    "backend": "Python 3.12 + FastAPI",
    "frontend": "React",
    "database": "PostgreSQL 15"
  },
  "services": [
    {
      "name": "orders",
      "folder_structure": {
        "src/orders/domain/entities.py": "Entities"
      }
    }
  ],
  "api_endpoints": [
      {"path": "/orders", "method": "POST"}
  ],
  "database_schema": {
      "tables": [{"name": "orders"}]
  }
}

class TestVikramAgent(unittest.TestCase):
    
    def setUp(self):
        self.project_id = "test-project-123"
        self.user_id = "test-user-456"
        self.vikram = Vikram(self.project_id, self.user_id)

    @patch.object(ai_router, 'generate', new_callable=AsyncMock)
    @patch.object(context_engine, 'store_context')
    def test_design_architecture(self, mock_store_context, mock_generate):
        # Setup Mock AI Response
        mock_response = MagicMock(spec=AIResponse)
        mock_response.content = json.dumps(MOCK_BLUEPRINT)
        mock_generate.return_value = mock_response

        # Run method
        requirements = {"description": "Build a food delivery app"}
        result = asyncio.run(self.vikram.design_architecture(requirements))

        # Assertions
        self.assertEqual(result["project_name"], "food-delivery-app")
        self.assertEqual(result["tech_stack"]["backend"], "Python 3.12 + FastAPI")
        
        # Verify Context Storage
        mock_store_context.assert_called()

    @patch.object(ai_router, 'generate', new_callable=AsyncMock)
    def test_generate_actions(self, mock_generate):
        # Setup Mock
        mock_response = MagicMock(spec=AIResponse)
        mock_response.content = json.dumps(MOCK_BLUEPRINT)
        mock_generate.return_value = mock_response

        # Test individual methods
        requirements = {"description": "Test"}
        
        # Tech Stack: Calls generate_json_blueprint which calls ai_router.generate
        tech_stack = asyncio.run(self.vikram.decide_tech_stack(requirements))
        self.assertEqual(tech_stack["backend"], "Python 3.12 + FastAPI")

        # Folder Structure: Helper method, no AI call
        folder_structure = asyncio.run(self.vikram.generate_folder_structure(MOCK_BLUEPRINT))
        self.assertIn("orders", folder_structure)
        
        # API Contracts: Helper method, no AI call
        apis = asyncio.run(self.vikram.define_api_contracts(MOCK_BLUEPRINT))
        self.assertEqual(len(apis), 1)
        self.assertEqual(apis[0]["path"], "/orders")
        
        # DB Schema: Helper method, no AI call
        schema = asyncio.run(self.vikram.design_database_schema(MOCK_BLUEPRINT))
        self.assertEqual(schema["tables"][0]["name"], "orders")

if __name__ == "__main__":
    unittest.main()
