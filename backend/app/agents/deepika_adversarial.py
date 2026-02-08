"""
DEEPIKA - Adversarial Performance Issue Detection Agent

Purpose: Hunt for performance bottlenecks aggressively (adversarial training)
Training Objective: MAXIMIZE(performance_issues_found)
Reward Function: +1 per performance issue, +2 for HIGH impact

Specializations:
- O(n²) or worse algorithmic complexity
- N+1 query problems
- Memory leaks
- Synchronous blocking in async code
- Missing database indexes
- Large file operations without streaming
- Excessive API calls
- Missing caching opportunities
- Inefficient loops and iterations
- Resource exhaustion vulnerabilities

Model: Claude Sonnet 4.5 (always)
"""

from typing import Dict, Any, List
import json
import re
import logging

# Adjust imports based on your project structure
from app.services.ai_router import ai_router, TaskComplexity
from app.services.prompt_engine import prompt_engine
from app.services.mistake_memory import mistake_memory
from app.agents.mixins import (
    MistakeMemoryMixin, 
    SearchCapableMixin, 
    PermanentMemoryMixin,
    ContextManagementMixin,
    DecisionLedgerMixin
)
import time


class DeepikaAdversarial(MistakeMemoryMixin, PermanentMemoryMixin, ContextManagementMixin, DecisionLedgerMixin, SearchCapableMixin):
    """
    Adversarial performance issue agent with GAN-style learning.
    
    Follows standalone V2 architecture - no BaseAgent inheritance.
    Uses AI Router directly for all AI operations.
    
    Usage:
        deepika = DeepikaAdversarial(project_id="proj-123")
        result = await deepika.review(code, file_type="python")
        await deepika.learn_from_feedback(reward=6.0, strategy="maximization")
    """
    
    def __init__(self, project_id: str, workspace: Dict = None):
        """
        Initialize DEEPIKA agent.
        
        Args:
            project_id: Unique identifier for the project being reviewed
            workspace: Optional workspace info
        """
        # Initialize MistakeMemoryMixin
        super().__init__()
        
        # Standalone - no inheritance
        self.project_id = project_id
        self.agent_name = "deepika"  # For mistake memory
        
        # Direct AI Router access
        self.ai_router = ai_router
        
        # Logging
        self.logger = logging.getLogger("agent.deepika_adversarial")
        self.logger.setLevel(logging.INFO)
        self.workspace = workspace
        
        # Statistics tracking
        self.total_reviews = 0
        self.total_issues_found = 0
        self.high_impact_count = 0
        self.critical_impact_count = 0
        
        # Learning state (for GAN training)
        self.learning_history = []
        self.current_strategy_score = 0.0
    
    async def review(self, code: str, file_type: str = "python") -> Dict[str, Any]:
        """
        Hunt for performance issues aggressively.
        
        Args:
            code: Source code to review
            file_type: Type of code (python, javascript, typescript, etc.)
        
        Returns:
            Dict containing performance issues found
        """
        try:
            self.total_reviews += 1
            self.logger.info(f"⚡ Starting performance review #{self.total_reviews} for {file_type} code")
            
            # Step 1: Check past mistakes (async)
            past_mistakes = await self.check_past_mistakes(
                task_type="adversarial_performance",
                context={"file_type": file_type}
            )

            # Build adversarial prompt
            prompt = self._build_adversarial_prompt(code, file_type, past_mistakes)
            
            # Call AI Router
            response = await self.ai_router.generate(
                 messages=[{"role": "user", "content": prompt}],
                 task_type="adversarial_performance",
                 complexity=TaskComplexity.COMPLEX
            )
            
            # Log cost
            self.logger.info(
                f"✅ {response.output_tokens} tokens, "
                f"₹{response.cost_estimate:.4f}"
            )
            
            # Parse and validate response
            result = self._parse_response(response.content)
            
            # Update statistics
            issues_found = result.get("issues_found", 0)
            self.total_issues_found += issues_found
            
            # Count high and critical impact
            for detail in result.get("details", []):
                severity = detail.get("severity", "")
                if severity == "CRITICAL":
                    self.critical_impact_count += 1
                elif severity == "HIGH":
                    self.high_impact_count += 1
            
            self.logger.info(
                f"🎯 DEEPIKA found {issues_found} performance issues "
                f"(total: {self.total_issues_found}, "
                f"high: {self.high_impact_count}, critical: {self.critical_impact_count})"
            )
            
            return result
            
        except json.JSONDecodeError as e:
            self.logger.error(f"❌ Invalid JSON response: {e}")
            return self._error_response("Failed to parse AI response")
            
        except Exception as e:
            self.logger.error(f"❌ Performance review failed: {e}")
            await self.record_failure(
                task_type="adversarial_performance",
                error=str(e),
                context={"file_type": file_type}
            )
            raise
    
    async def learn_from_feedback(self, reward: float, strategy: str = "maximization"):
        """
        Learn from feedback rewards (GAN training component).
        
        This implements the adversarial learning loop for performance optimization:
        - High rewards = effective performance issue detection
        - Low rewards = need to think more like a load tester
        
        Args:
            reward: Reward score (issue count + impact weighting)
            strategy: "maximization" (find more performance issues)
        """
        self.logger.info(f"📚 Learning from feedback: reward={reward}, strategy={strategy}")
        
        # Track learning history
        self.learning_history.append({
            "timestamp": time.time(),
            "reward": reward,
            "strategy": strategy,
            "total_issues_so_far": self.total_issues_found,
            "critical_count": self.critical_impact_count
        })
        
        # Update strategy score
        if strategy == "maximization":
            if reward >= 6:
                # Excellent performance analysis
                self.current_strategy_score += 2.0
                self.logger.info("✅ Excellent - identified major bottlenecks!")
                
            elif reward >= 3:
                # Good analysis
                self.current_strategy_score += 1.0
                self.logger.info("✓ Good - found important issues")
                
            else:
                # Need deeper analysis
                self.current_strategy_score -= 0.5
                self.logger.info("⚠️ Low reward - think about scalability more!")
        
        # Store learning in mistake memory
        mistake_memory.record_failure(
            task_type=f"adversarial_performance_{self.agent_name}",
            input_data=f"reward_{reward}",
            error=f"Achieved reward: {reward}",
            fix=f"Strategy: {strategy}, Score: {self.current_strategy_score:.2f}, Critical: {self.critical_impact_count}"
        )
        
        # Log current learning state
        self.logger.info(
            f"📊 Learning state: strategy_score={self.current_strategy_score:.2f}, "
            f"critical_issues={self.critical_impact_count}, history_size={len(self.learning_history)}"
        )
    
    def _build_adversarial_prompt(self, code: str, file_type: str, past_mistakes: List[Dict] = None) -> str:
        """
        Build aggressive adversarial prompt using PromptEngine.
        Incorporates learning from past reviews.
        """
        base_prompt = prompt_engine.get_prompt(
            "deepika",
            "performance_review",
            context={
                "code_content": code,
                "file_type": file_type
            }
        )
        
        # Add learning context if we have history
        if self.learning_history:
            recent_rewards = [h["reward"] for h in self.learning_history[-5:]]
            avg_reward = sum(recent_rewards) / len(recent_rewards)
            
            learning_context = f"\n\n**PERFORMANCE ANALYSIS CONTEXT:**\n"
            learning_context += f"Your recent performance: {avg_reward:.1f} issues/review\n"
            learning_context += f"Critical bottlenecks found: {self.critical_impact_count}\n"
            learning_context += f"Strategy score: {self.current_strategy_score:.2f}\n"
            
            if self.current_strategy_score < 0:
                learning_context += "**THINK LIKE A LOAD TESTER** - what breaks at scale?\n"
            elif self.current_strategy_score > 4:
                learning_context += "**OUTSTANDING WORK** - keep finding bottlenecks!\n"
            else:
                learning_context += "**STAY ANALYTICAL** - every loop could be O(n²)!\n"
            
            base_prompt += learning_context
        
        # Step 2: Incorporate past mistakes (from MistakeMemoryMixin)
        if past_mistakes:
            base_prompt = self.incorporate_past_learnings(past_mistakes, base_prompt)
            self.logger.info(f"📚 Incorporated {len(past_mistakes)} past mistakes into performance review prompt")
        
        return base_prompt

    def _parse_response(self, content: str) -> Dict[str, Any]:
        """Parse AI response into structured format."""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try extracting JSON from markdown
            json_match = re.search(r'```json\n(.*?)\n```', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))
            json_match = re.search(r'```\n(.*?)\n```', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(0))
            raise ValueError(f"Could not parse response: {content[:200]}")
    
    def _error_response(self, error_message: str) -> Dict[str, Any]:
        """Return standardized error response."""
        return {
            "agent": "DEEPIKA",
            "issues_found": 0,
            "severity": [],
            "details": [],
            "error": error_message
        }
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get agent performance statistics including learning metrics."""
        return {
            "total_reviews": self.total_reviews,
            "total_issues_found": self.total_issues_found,
            "critical_issues": self.critical_impact_count,
            "high_issues": self.high_impact_count,
            "average_issues_per_review": (
                self.total_issues_found / self.total_reviews 
                if self.total_reviews > 0 else 0
            ),
            "strategy_score": self.current_strategy_score,
            "learning_history_size": len(self.learning_history)
        }


if __name__ == "__main__":
    import asyncio
    
    async def test():
        deepika = DeepikaAdversarial(project_id="test-perf-001")
        
        slow_code = """
def get_all_user_posts():
    users = User.query.all()  # No pagination
    result = []
    for user in users:
        posts = Post.query.filter_by(user_id=user.id).all()  # N+1 query
        for post in posts:
            # Nested loop - O(n²)
            comments = Comment.query.filter_by(post_id=post.id).all()
            result.append({
                'user': user,
                'post': post,
                'comments': comments
            })
    return result
"""
        
        result = await deepika.review(slow_code, "python")
        print(json.dumps(result, indent=2))
        
        # Simulate GAN training
        reward = result["issues_found"] + (result.get("critical_issues", 0) * 2)
        await deepika.learn_from_feedback(reward, "maximization")
        
        print(f"\nStatistics: {deepika.get_statistics()}")
    
    asyncio.run(test())