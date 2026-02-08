"""
KARAN - Adversarial Security Vulnerability Detection Agent

Purpose: Hunt for security vulnerabilities aggressively (adversarial training)
Training Objective: MAXIMIZE(security_holes_found)
Reward Function: +1 per vulnerability detected, +2 for CRITICAL

Specializations:
- SQL Injection vulnerabilities
- Cross-Site Scripting (XSS)
- CSRF attacks
- Insecure deserialization
- Hardcoded credentials
- Weak encryption
- Missing authentication/authorization
- Path traversal vulnerabilities
- Insecure file uploads
- Missing CORS configuration

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


class KaranAdversarial(MistakeMemoryMixin, PermanentMemoryMixin, ContextManagementMixin, DecisionLedgerMixin, SearchCapableMixin):
    """
    Adversarial security vulnerability agent with GAN-style learning.
    
    Follows standalone V2 architecture - no BaseAgent inheritance.
    Uses AI Router directly for all AI operations.
    
    Usage:
        karan = KaranAdversarial(project_id="proj-123")
        result = await karan.review(code, file_type="python")
        await karan.learn_from_feedback(reward=4.0, strategy="maximization")
    """
    
    def __init__(self, project_id: str, workspace: Dict = None):
        """
        Initialize KARAN agent.
        
        Args:
            project_id: Unique identifier for the project being reviewed
            workspace: Optional workspace info
        """
        # Initialize MistakeMemoryMixin
        super().__init__()
        
        # Standalone - no inheritance
        self.project_id = project_id
        self.agent_name = "karan"  # For mistake memory
        
        # Direct AI Router access
        self.ai_router = ai_router
        
        # Logging
        self.logger = logging.getLogger("agent.karan_adversarial")
        self.logger.setLevel(logging.INFO)
        self.workspace = workspace
        
        # Statistics tracking
        self.total_reviews = 0
        self.total_vulnerabilities_found = 0
        self.critical_count = 0
        self.high_count = 0
        
        # Learning state (for GAN training)
        self.learning_history = []
        self.current_strategy_score = 0.0
    
    async def review(self, code: str, file_type: str = "python") -> Dict[str, Any]:
        """
        Hunt for security vulnerabilities aggressively.
        
        This is the main execution method. It sends code to Claude Sonnet 4.5
        with an adversarial prompt designed to maximize vulnerability detection.
        
        Args:
            code: Source code to review
            file_type: Type of code (python, javascript, typescript, etc.)
        
        Returns:
            Dict containing:
                - agent: "KARAN"
                - vulnerabilities_found: int count of vulnerabilities
                - severity: List of severity levels
                - details: List of vulnerability details with CVE references
        """
        try:
            self.total_reviews += 1
            self.logger.info(f"🔒 Starting security review #{self.total_reviews} for {file_type} code")
            
            # Step 1: Check past mistakes (async)
            past_mistakes = await self.check_past_mistakes(
                task_type="adversarial_security",
                context={"file_type": file_type}
            )

            # Build adversarial prompt
            prompt = self._build_adversarial_prompt(code, file_type, past_mistakes)
            
            # Call AI Router
            response = await self.ai_router.generate(
                messages=[{"role": "user", "content": prompt}],
                task_type="adversarial_security",
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
            vulns_found = result.get("vulnerabilities_found", 0)
            self.total_vulnerabilities_found += vulns_found
            
            # Count critical and high severity
            for detail in result.get("details", []):
                if detail.get("severity") == "CRITICAL":
                    self.critical_count += 1
                elif detail.get("severity") == "HIGH":
                    self.high_count += 1
            
            self.logger.info(
                f"🎯 KARAN found {vulns_found} vulnerabilities "
                f"(total: {self.total_vulnerabilities_found}, "
                f"critical: {self.critical_count}, high: {self.high_count})"
            )
            
            return result
            
        except json.JSONDecodeError as e:
            self.logger.error(f"❌ Invalid JSON response: {e}")
            return self._error_response("Failed to parse AI response")
            
        except Exception as e:
            self.logger.error(f"❌ Security review failed: {e}")
            await self.record_failure(
                task_type="adversarial_security",
                error=str(e),
                context={"file_type": file_type}
            )
            raise
    
    async def learn_from_feedback(self, reward: float, strategy: str = "maximization"):
        """
        Learn from feedback rewards (GAN training component).
        
        This implements the adversarial learning loop:
        - Agents evolve their detection strategies based on success
        - High rewards reinforce current tactics
        - Low rewards trigger strategy changes
        
        Args:
            reward: Reward score (vulnerability count + severity weighting)
            strategy: "maximization" (find more vulnerabilities)
        """
        self.logger.info(f"📚 Learning from feedback: reward={reward}, strategy={strategy}")
        
        # Track learning history
        self.learning_history.append({
            "timestamp": time.time(),
            "reward": reward,
            "strategy": strategy,
            "total_vulns_so_far": self.total_vulnerabilities_found,
            "critical_count": self.critical_count
        })
        
        # Update strategy score
        if strategy == "maximization":
            if reward >= 5:
                # High reward - excellent vulnerability detection
                self.current_strategy_score += 1.5
                self.logger.info("✅ High reward - reinforcing security analysis approach")
                
            elif reward >= 2:
                # Moderate reward - good but can improve
                self.current_strategy_score += 0.5
                self.logger.info("✓ Moderate reward - maintaining vigilance")
                
            else:
                # Low reward - need more aggressive analysis
                self.current_strategy_score -= 0.5
                self.logger.info("⚠️ Low reward - intensifying security analysis")
        
        # Store learning in mistake memory
        mistake_memory.record_failure(
            task_type=f"adversarial_security_{self.agent_name}",
            input_data=f"reward_{reward}",
            error=f"Achieved reward: {reward}",
            fix=f"Strategy: {strategy}, Score: {self.current_strategy_score:.2f}, Critical: {self.critical_count}"
        )
        
        # Log current learning state
        self.logger.info(
            f"📊 Learning state: strategy_score={self.current_strategy_score:.2f}, "
            f"critical_vulns={self.critical_count}, history_size={len(self.learning_history)}"
        )
    
    def _build_adversarial_prompt(self, code: str, file_type: str, past_mistakes: List[Dict] = None) -> str:
        """
        Build aggressive adversarial prompt using PromptEngine.
        Incorporates learning from past reviews.
        """
        base_prompt = prompt_engine.get_prompt(
            "karan",
            "security_review",
            context={
                "code_content": code,
                "file_type": file_type
            }
        )
        
        # Add learning context if we have history
        if self.learning_history:
            recent_rewards = [h["reward"] for h in self.learning_history[-5:]]
            avg_reward = sum(recent_rewards) / len(recent_rewards)
            
            learning_context = f"\n\n**SECURITY HUNTING CONTEXT:**\n"
            learning_context += f"Your recent performance: {avg_reward:.1f} vulnerabilities/review\n"
            learning_context += f"Critical findings: {self.critical_count}\n"
            learning_context += f"Strategy score: {self.current_strategy_score:.2f}\n"
            
            if self.current_strategy_score < 0:
                learning_context += "**THINK LIKE A HACKER** - find creative attack vectors!\n"
            elif self.current_strategy_score > 3:
                learning_context += "**EXCELLENT WORK** - maintain this level of scrutiny!\n"
            else:
                learning_context += "**STAY VIGILANT** - every line could hide a vulnerability!\n"
            
            base_prompt += learning_context
        
        # Step 2: Incorporate past mistakes (from MistakeMemoryMixin)
        if past_mistakes:
            base_prompt = self.incorporate_past_learnings(past_mistakes, base_prompt)
            self.logger.info(f"📚 Incorporated {len(past_mistakes)} past mistakes into security review prompt")
        
        return base_prompt

    def _parse_response(self, content: str) -> Dict[str, Any]:
        """
        Parse AI response into structured format.
        
        Handles multiple response formats:
        1. Direct JSON
        2. JSON wrapped in markdown code blocks
        3. Malformed responses (returns error format)
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
        """Return standardized error response."""
        return {
            "agent": "KARAN",
            "vulnerabilities_found": 0,
            "severity": [],
            "details": [],
            "error": error_message
        }
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get agent performance statistics including learning metrics."""
        return {
            "total_reviews": self.total_reviews,
            "total_vulnerabilities_found": self.total_vulnerabilities_found,
            "critical_vulnerabilities": self.critical_count,
            "high_vulnerabilities": self.high_count,
            "average_vulns_per_review": (
                self.total_vulnerabilities_found / self.total_reviews 
                if self.total_reviews > 0 else 0
            ),
            "strategy_score": self.current_strategy_score,
            "learning_history_size": len(self.learning_history)
        }


# Standalone execution for testing
if __name__ == "__main__":
    import asyncio
    
    async def test():
        karan = KaranAdversarial(project_id="test-sec-001")
        
        vulnerable_code = """
# SQL Injection
def get_user(email):
    query = f"SELECT * FROM users WHERE email = '{email}'"
    return db.execute(query)

# XSS
def display_message(message):
    return f"<div>{message}</div>"

# Hardcoded credentials
API_KEY = "sk_live_12345abcde"
PASSWORD = "admin123"

# Path traversal
def read_file(filename):
    return open(f"/uploads/{filename}").read()
"""
        
        result = await karan.review(vulnerable_code, "python")
        print(json.dumps(result, indent=2))
        
        # Simulate GAN training
        reward = result["vulnerabilities_found"] + (result["details"].count("CRITICAL") * 2)
        await karan.learn_from_feedback(reward, "maximization")
        
        print(f"\nStatistics: {karan.get_statistics()}")
    
    asyncio.run(test())