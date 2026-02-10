"""
NAVYA - Adversarial Logic Error Detection Agent

Purpose: Hunt for logic errors aggressively (adversarial training)
Training Objective: MAXIMIZE(logical_errors_found)
Reward Function: +1 per logic bug detected

Specializations:
- Division by zero vulnerabilities
- Null/None reference errors
- Off-by-one errors in loops and indexes
- Race conditions in async code
- Type mismatches and incorrect calculations
- Missing edge case handling
- Unreachable code detection

Model: Claude Sonnet 4.5 (always)
"""

from typing import Dict, Any, List
import json
import re
import logging

# AI Router integration
from app.services.ai_router import ai_router, TaskComplexity
from app.services.prompt_engine import prompt_engine
from app.services.mistake_memory import mistake_memory
from app.agents.mixins import (
    MistakeMemoryMixin, 
    SearchCapableMixin, 
    PermanentMemoryMixin,
    ContextManagementMixin,
    DecisionLedgerMixin,
    ProgressMixin
)
import time


class NavyaAdversarial(MistakeMemoryMixin, PermanentMemoryMixin, ContextManagementMixin, DecisionLedgerMixin, SearchCapableMixin, ProgressMixin):
    """
    Adversarial logic error agent with GAN-style learning.
    
    Follows standalone V2 architecture - no BaseAgent inheritance.
    Uses AI Router directly for all AI operations.
    
    Usage:
        navya = NavyaAdversarial(project_id="proj-123")
        result = await navya.review(code, file_type="python")
        
        # After reward calculation
        await navya.learn_from_feedback(reward=5.0, strategy="maximization")
    """
    
    def __init__(self, project_id: str, workspace: Dict = None):
        """
        Initialize NAVYA agent.
        
        Args:
            project_id: Unique identifier for the project being reviewed
            workspace: Optional workspace info
        """
        # Initialize MistakeMemoryMixin
        super().__init__()
        
        # Standalone - no inheritance
        self.project_id = project_id
        self.agent_name = "navya"  # For mistake memory
        
        # Direct AI Router access
        self.ai_router = ai_router
        
        # Logging
        self.logger = logging.getLogger("agent.navya_adversarial")
        self.logger.setLevel(logging.INFO)
        self.workspace = workspace
        
        # Statistics tracking
        self.total_reviews = 0
        self.total_bugs_found = 0
        
        # Learning state (for GAN training)
        self.learning_history = []
        self.current_strategy_score = 0.0
    
    async def review(self, code: str, file_type: str = "python") -> Dict[str, Any]:
        """
        Hunt for logic errors aggressively.
        
        This is the main execution method. It sends code to Claude Sonnet 4.5
        with an adversarial prompt designed to maximize bug detection.
        
        Args:
            code: Source code to review
            file_type: Type of code (python, javascript, typescript, etc.)
        
        Returns:
            Dict containing:
                - agent: "NAVYA"
                - bugs_found: int count of bugs
                - severity: List of severity levels
                - details: List of bug details with line numbers and fixes
        
        Example:
            {
                "agent": "NAVYA",
                "bugs_found": 3,
                "severity": ["CRITICAL", "HIGH", "MEDIUM"],
                "details": [...]
            }
        """
        try:
            self.total_reviews += 1
            self.logger.info(f"🔍 Starting review #{self.total_reviews} for {file_type} code")
            
            # Step 1: Check past mistakes (async)
            await self._send_progress("adversarial_review", 20, "Analyzing logic flows for edge cases and potential null references...")
            past_mistakes = await self.check_past_mistakes(
                task_type="adversarial_review",
                context={"file_type": file_type}
            )

            # Build adversarial prompt
            prompt = self._build_adversarial_prompt(code, file_type, past_mistakes)
            
            # Call AI Router with adversarial_logic task type
            await self._send_progress("adversarial_review", 60, "Hunting for off-by-one errors and race conditions...")
            response = await self.ai_router.generate(
                messages=[{"role": "user", "content": prompt}],
                task_type="adversarial_logic",
                complexity=TaskComplexity.COMPLEX
            )
            
            # Log cost
            self.logger.info(
                f"✅ {response.output_tokens} tokens, "
                f"₹{response.cost_estimate:.4f}"
            )
            
            # Parse and validate response
            await self._send_progress("adversarial_review", 90, "Finalizing logical correctness audit...")
            result = self._parse_response(response.content)
            
            await self._send_progress("adversarial_review", 100, f"Logic review complete. Identified {result.get('bugs_found', 0)} potential errors.")
            
            # Update statistics
            bugs_found = result.get("bugs_found", 0)
            self.total_bugs_found += bugs_found
            
            self.logger.info(
                f"🎯 NAVYA found {bugs_found} logic errors "
                f"(total: {self.total_bugs_found} bugs across {self.total_reviews} reviews)"
            )
            
            return result
            
        except json.JSONDecodeError as e:
            self.logger.error(f"❌ Invalid JSON response: {e}")
            return self._error_response("Failed to parse AI response")
            
        except Exception as e:
            self.logger.error(f"❌ Review failed: {e}")
            await self.record_failure(
                task_type="adversarial_review",
                error=str(e),
                context={"file_type": file_type}
            )
            raise
    
    async def learn_from_feedback(self, reward: float, strategy: str = "maximization"):
        """
        Learn from feedback rewards (GAN training component).
        
        This implements the "learning loop" from the patent - agents improve
        their bug-finding strategies based on reward signals.
        
        Args:
            reward: Reward score from current review (typically bug count)
            strategy: "maximization" (find more bugs) or "minimization" (fewer bugs)
        
        How it works:
            - High reward + maximization = reinforce current approach
            - Low reward + maximization = try different approach
            - Stores learning in mistake_memory for future reference
        """
        self.logger.info(f"📚 Learning from feedback: reward={reward}, strategy={strategy}")
        
        # Track learning history
        self.learning_history.append({
            "timestamp": time.time(),
            "reward": reward,
            "strategy": strategy,
            "total_bugs_so_far": self.total_bugs_found
        })
        
        # Update strategy score
        if strategy == "maximization":
            if reward > 5:
                # High reward - keep this approach
                self.current_strategy_score += 1.0
                self.logger.info("✅ High reward - reinforcing current strategy")
                
            elif reward < 2:
                # Low reward - need to be more aggressive
                self.current_strategy_score -= 0.5
                self.logger.info("⚠️ Low reward - becoming more aggressive")
        
        # Store learning in mistake memory
        mistake_memory.record_failure(
            task_type=f"adversarial_review_{self.agent_name}",
            input_data=f"reward_{reward}",
            error=f"Achieved reward: {reward}",
            fix=f"Strategy: {strategy}, Score: {self.current_strategy_score:.2f}"
        )
        
        # Log current learning state
        self.logger.info(
            f"📊 Learning state: strategy_score={self.current_strategy_score:.2f}, "
            f"history_size={len(self.learning_history)}"
        )
    
    def _build_adversarial_prompt(self, code: str, file_type: str, past_mistakes: List[Dict] = None) -> str:
        """
        Build aggressive adversarial prompt using PromptEngine.
        
        Incorporates learning from past reviews to improve detection.
        """
        # Step 1: Get base prompt
        base_prompt = prompt_engine.get_prompt(
            "navya", 
            "code_review",
            context={
                "code_content": code,
                "file_type": file_type
            }
        )
        
        # Add learning context if we have history
        if self.learning_history:
            recent_rewards = [h["reward"] for h in self.learning_history[-5:]]
            avg_reward = sum(recent_rewards) / len(recent_rewards)
            
            learning_context = f"\n\n**LEARNING CONTEXT:**\n"
            learning_context += f"Your recent performance: {avg_reward:.1f} bugs/review\n"
            learning_context += f"Strategy score: {self.current_strategy_score:.2f}\n"
            
            if self.current_strategy_score < 0:
                learning_context += "**BE MORE AGGRESSIVE** - find more subtle bugs!\n"
            else:
                learning_context += "**MAINTAIN AGGRESSION** - keep finding bugs!\n"
            
            base_prompt += learning_context
        
        # Step 2: Incorporate past mistakes (from MistakeMemoryMixin)
        if past_mistakes:
            base_prompt = self.incorporate_past_learnings(past_mistakes, base_prompt)
            self.logger.info(f"📚 Incorporated {len(past_mistakes)} past mistakes into review prompt")
        
        return base_prompt

    def _parse_response(self, content: str) -> Dict[str, Any]:
        """
        Parse AI response into structured format.
        
        Handles multiple response formats:
        1. Direct JSON
        2. JSON wrapped in markdown code blocks
        3. Malformed responses (returns error format)
        
        Returns:
            Structured dict with bugs_found and details
        """
        try:
            # Try direct JSON parse first
            return json.loads(content)
            
        except json.JSONDecodeError:
            # Try extracting JSON from markdown code blocks
            json_match = re.search(r'```json\n(.*?)\n```', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))
            
            # Try without json marker
            json_match = re.search(r'```\n(.*?)\n```', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))
            
            # Last resort: try to find JSON object in text
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(0))
            
            raise ValueError(f"Could not parse response as JSON: {content[:200]}")
    
    def _error_response(self, error_message: str) -> Dict[str, Any]:
        """
        Return standardized error response.
        
        Args:
            error_message: Description of the error
            
        Returns:
            Dict in standard format with error indication
        """
        return {
            "agent": "NAVYA",
            "bugs_found": 0,
            "severity": [],
            "details": [],
            "error": error_message
        }
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get agent performance statistics including learning metrics.
        
        Returns:
            Dict with review stats and learning state
        """
        return {
            "total_reviews": self.total_reviews,
            "total_bugs_found": self.total_bugs_found,
            "average_bugs_per_review": (
                self.total_bugs_found / self.total_reviews 
                if self.total_reviews > 0 else 0
            ),
            "strategy_score": self.current_strategy_score,
            "learning_history_size": len(self.learning_history)
        }


# Standalone execution for testing
if __name__ == "__main__":
    import asyncio
    
    async def test():
        navya = NavyaAdversarial(project_id="test-123")
        
        buggy_code = """
def calculate_total(quantity, price):
    return quantity / price  # Bug: division by zero if price=0
    
def get_user(user_id):
    users = []
    return users[user_id]  # Bug: index out of range

def process_data(data):
    result = data + 5  # Bug: type mismatch if data is string
    return result
"""
        
        result = await navya.review(buggy_code, "python")
        print(json.dumps(result, indent=2))
        
        # Simulate GAN training
        reward = result["bugs_found"]
        await navya.learn_from_feedback(reward, "maximization")
        
        print(f"\nStatistics: {navya.get_statistics()}")
    
    asyncio.run(test())