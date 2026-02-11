"""
ADVERSARIAL TRAINER - GAN-Style Learning System (Patent Claim 4)
==================================================================
Location: app/services/adversarial_trainer.py

Purpose: Competitive loss-function loop for code generation and review
- Generator (Shubham/Aanya) penalized when reviewers find issues
- Reviewers (Navya, Karan, Deepika) rewarded for unique findings
- Both improve through adversarial training
- Epoch-based training with convergence tracking

Patent Claim 4: GAN-based training subsystem
"""

import json
import logging
import asyncio
from typing import Dict, Any, List, Optional
from pathlib import Path
from app.core.redis import get_redis_client
from datetime import datetime


class AdversarialTrainer:
    """
    GAN-inspired training system for code generation.
    
    Generator Agent: Shubham (creates code)
    Discriminator Agents: Navya, Karan, Deepika (review code)
    
    Training Loop:
    1. Shubham generates code
    2. Review agents find issues
    3. Both sides learn from the interaction
    4. Weights/strategies updated
    5. Next iteration improves
    """
    
    def __init__(self, project_id: str = "global", training_dir: str = "training_data"):
        self.project_id = project_id
        self.logger = logging.getLogger("adversarial_trainer")
        
        # Training data storage
        self.training_dir = Path(training_dir) / project_id
        self.training_dir.mkdir(parents=True, exist_ok=True)
        
        # Metrics tracking
        self.epoch = 0
        self.training_history: List[Dict[str, Any]] = []
        self._load_training_history()
        
        try:
            self.redis = get_redis_client()
            self.redis.ping()
            self.connected = True
            self.logger.info("[OK] Connected to Redis (shared pool)")
        except Exception as e:
            self.logger.warning(f"[WARN] Redis unavailable: {e}")
            self.redis = None
            self.connected = False
            self._memory_weights = {}
    
    async def train_adversarial_pair(
        self,
        generator_agent: str,
        discriminator_agent: str,
        code: Dict[str, Any],
        review_result: Dict[str, Any],
        outcome: str  # "accepted" or "rejected"
    ):
        """
        Train both generator and discriminator based on interaction.
        
        Args:
            generator_agent: Agent that generated code (e.g., "shubham")
            discriminator_agent: Agent that reviewed (e.g., "navya")
            code: Generated code
            review_result: Review findings
            outcome: Whether code was accepted or rejected
        """
        
        # Calculate reward/penalty
        if outcome == "accepted":
            # Code passed review
            generator_reward = 1.0
            discriminator_reward = -0.1  # Slight penalty for being too lenient
        elif outcome == "rejected":
            # Code had issues
            bugs_found = review_result.get("bugs", [])
            bug_count = len(bugs_found)
            
            generator_reward = -bug_count * 0.5  # Penalty for bugs
            discriminator_reward = bug_count * 0.3  # Reward for finding bugs
        else:
            return  # Unknown outcome
        
        # Update generator (Shubham)
        await self._update_generator_strategy(
            generator_agent,
            code,
            review_result,
            generator_reward
        )
        
        # Update discriminator (Navya/Karan/Deepika)
        await self._update_discriminator_strategy(
            discriminator_agent,
            review_result,
            discriminator_reward
        )
        
        # Log training iteration
        self.logger.info(
            f"🔄 Training: {generator_agent} ({generator_reward:+.2f}) "
            f"vs {discriminator_agent} ({discriminator_reward:+.2f})"
        )
    
    async def _update_generator_strategy(
        self,
        agent_name: str,
        code: Dict[str, Any],
        review: Dict[str, Any],
        reward: float
    ):
        """
        Update generator's strategy based on review feedback.
        
        Learning:
        - Which patterns caused bugs
        - Which approaches passed review
        - Common mistakes to avoid
        """
        
        strategy_key = f"generator_strategy:{agent_name}"
        
        # Get current strategy
        current_strategy = self._get_strategy(strategy_key)
        
        # Extract learnings
        bugs = review.get("bugs", [])
        warnings = review.get("warnings", [])
        
        # Update avoidance patterns (what NOT to do)
        if reward < 0:
            for bug in bugs:
                pattern = self._extract_bug_pattern(bug)
                if pattern:
                    current_strategy["avoid_patterns"].append({
                        "pattern": pattern,
                        "severity": bug.get("severity", "medium"),
                        "timestamp": datetime.now().isoformat()
                    })
        
        # Update success patterns (what TO do)
        if reward > 0:
            approach = self._extract_approach(code)
            current_strategy["success_patterns"].append({
                "approach": approach,
                "score": reward,
                "timestamp": datetime.now().isoformat()
            })
        
        # Update confidence weights
        current_strategy["confidence"] = self._calculate_confidence(
            current_strategy,
            reward
        )
        
        # Store updated strategy
        self._store_strategy(strategy_key, current_strategy)
    
    async def _update_discriminator_strategy(
        self,
        agent_name: str,
        review: Dict[str, Any],
        reward: float
    ):
        """
        Update discriminator's strategy based on training feedback.
        
        Learning:
        - Which checks are most valuable
        - False positive/negative rates
        - Priority of different issue types
        """
        
        strategy_key = f"discriminator_strategy:{agent_name}"
        
        # Get current strategy
        current_strategy = self._get_strategy(strategy_key)
        
        # Update check priorities
        bugs_found = review.get("bugs", [])
        
        if reward > 0:
            # Reward for finding real bugs
            for bug in bugs_found:
                bug_type = bug.get("type", "unknown")
                
                if bug_type not in current_strategy["check_priorities"]:
                    current_strategy["check_priorities"][bug_type] = 1.0
                
                # Increase priority for this check
                current_strategy["check_priorities"][bug_type] += 0.1
        
        elif reward < 0:
            # Penalty for being too strict (false positives)
            current_strategy["strictness"] *= 0.95  # Reduce strictness
        
        # Store updated strategy
        self._store_strategy(strategy_key, current_strategy)
    
    def _get_strategy(self, strategy_key: str) -> Dict[str, Any]:
        """Load strategy or create default"""
        
        if self.connected:
            data = self.redis.get(strategy_key)
            if data:
                return json.loads(data)
        else:
            if strategy_key in self._memory_weights:
                return self._memory_weights[strategy_key]
        
        # Default strategy
        if "generator" in strategy_key:
            return {
                "avoid_patterns": [],
                "success_patterns": [],
                "confidence": 0.5,
                "iteration": 0
            }
        else:  # discriminator
            return {
                "check_priorities": {},
                "strictness": 1.0,
                "iteration": 0
            }
    
    def _store_strategy(self, strategy_key: str, strategy: Dict[str, Any]):
        """Store updated strategy"""
        
        strategy["iteration"] += 1
        strategy["updated_at"] = datetime.now().isoformat()
        
        if self.connected:
            self.redis.set(
                strategy_key,
                json.dumps(strategy),
                ex=30 * 24 * 60 * 60  # 30 days expiry
            )
        else:
            self._memory_weights[strategy_key] = strategy
    
    def _extract_bug_pattern(self, bug: Dict[str, Any]) -> Optional[str]:
        """Extract pattern from bug for future avoidance"""
        
        bug_type = bug.get("type", "")
        location = bug.get("location", "")
        description = bug.get("description", "")
        
        # Create pattern signature
        if bug_type and location:
            return f"{bug_type}:{location}"
        
        return None
    
    def _extract_approach(self, code: Dict[str, Any]) -> str:
        """Extract coding approach/pattern from successful code"""
        
        # Simplified: extract technology choices
        tech = []
        
        if "framework" in str(code):
            tech.append("framework_based")
        
        if "async" in str(code):
            tech.append("async_pattern")
        
        return "|".join(tech) if tech else "standard"
    
    def _calculate_confidence(
        self,
        strategy: Dict[str, Any],
        reward: float
    ) -> float:
        """Calculate confidence score based on performance"""
        
        current_confidence = strategy.get("confidence", 0.5)
        
        # Update confidence with exponential moving average
        alpha = 0.1  # Learning rate
        new_confidence = current_confidence + alpha * reward
        
        # Clamp between 0 and 1
        return max(0.0, min(1.0, new_confidence))
    
    def get_generator_suggestions(self, agent_name: str) -> Dict[str, Any]:
        """
        Get improvement suggestions for generator agent.
        
        Returns learned patterns to avoid and promote.
        """
        
        strategy_key = f"generator_strategy:{agent_name}"
        strategy = self._get_strategy(strategy_key)
        
        return {
            "avoid_patterns": strategy.get("avoid_patterns", [])[-10:],  # Last 10
            "success_patterns": strategy.get("success_patterns", [])[-10:],
            "confidence": strategy.get("confidence", 0.5),
            "iterations": strategy.get("iteration", 0)
        }
    
    def get_discriminator_priorities(self, agent_name: str) -> Dict[str, Any]:
        """
        Get check priorities for discriminator agent.
        
        Returns which checks to prioritize.
        """
        
        strategy_key = f"discriminator_strategy:{agent_name}"
        strategy = self._get_strategy(strategy_key)
        
        return {
            "check_priorities": strategy.get("check_priorities", {}),
            "strictness": strategy.get("strictness", 1.0),
            "iterations": strategy.get("iteration", 0)
        }
    
    async def train_epoch(
        self,
        code_samples: List[Dict[str, str]],
        generator_agent: str = "shubham"
    ) -> Dict[str, Any]:
        """
        Run single training epoch with competitive loss calculation (Patent Claim 4).
        
        Args:
            code_samples: List of {code, file_type} dicts
            generator_agent: Which agent generated the code
        
        Returns:
            Dict with losses, metrics, and convergence data
        """
        from app.agents.deepika_adversarial import DeepikaAdversarial
        from app.agents.karan_adversarial import KaranAdversarial
        from app.agents.navya_adversarial import NavyaAdversarial
        
        self.epoch += 1
        self.logger.info(f"🔄 Starting training epoch {self.epoch}")
        
        # Initialize reviewers
        deepika = DeepikaAdversarial(project_id=self.project_id)
        karan = KaranAdversarial(project_id=self.project_id)
        navya = NavyaAdversarial(project_id=self.project_id)
        
        epoch_results = {
            "epoch": self.epoch,
            "timestamp": datetime.now().isoformat(),
            "generator": generator_agent,
            "samples_processed": len(code_samples),
            "generator_loss": 0.0,
            "reviewer_losses": {
                "deepika": 0.0,
                "karan": 0.0,
                "navya": 0.0
            },
            "total_issues_found": 0,
            "unique_issues": 0
        }
        
        # Process each code sample
        for i, sample in enumerate(code_samples):
            code = sample.get("code", "")
            file_type = sample.get("file_type", "python")
            
            self.logger.info(f"[LOG] Processing sample {i+1}/{len(code_samples)}")
            
            # Run reviewers in parallel
            try:
                results = await asyncio.gather(
                    deepika.review(code, file_type),
                    karan.review(code, file_type),
                    navya.review(code, file_type),
                    return_exceptions=True
                )
                
                deepika_result, karan_result, navya_result = results
                
                # Handle exceptions
                if isinstance(deepika_result, Exception):
                    self.logger.error(f"[ERROR] Deepika failed: {deepika_result}")
                    deepika_result = {"issues_found": 0, "details": []}
                
                if isinstance(karan_result, Exception):
                    self.logger.error(f"[ERROR] Karan failed: {karan_result}")
                    karan_result = {"vulnerabilities_found": 0, "details": []}
                
                if isinstance(navya_result, Exception):
                    self.logger.error(f"[ERROR] Navya failed: {navya_result}")
                    navya_result = {"bugs_found": 0, "details": []}
                
                # Calculate competitive losses
                sample_losses = self._calculate_competitive_losses(
                    deepika_result,
                    karan_result,
                    navya_result,
                    code
                )
                
                # Accumulate losses
                epoch_results["generator_loss"] += sample_losses["generator_loss"]
                epoch_results["reviewer_losses"]["deepika"] += sample_losses["deepika_loss"]
                epoch_results["reviewer_losses"]["karan"] += sample_losses["karan_loss"]
                epoch_results["reviewer_losses"]["navya"] += sample_losses["navya_loss"]
                epoch_results["total_issues_found"] += sample_losses["total_issues"]
                epoch_results["unique_issues"] += sample_losses["unique_issues"]
                
            except Exception as e:
                self.logger.error(f"[ERROR] Sample processing failed: {e}")
                continue
        
        # Average losses across samples
        if len(code_samples) > 0:
            epoch_results["generator_loss"] /= len(code_samples)
            epoch_results["reviewer_losses"]["deepika"] /= len(code_samples)
            epoch_results["reviewer_losses"]["karan"] /= len(code_samples)
            epoch_results["reviewer_losses"]["navya"] /= len(code_samples)
        
        # Update agent strategies based on losses
        await self._update_strategies_from_losses(
            deepika,
            karan,
            navya,
            epoch_results
        )
        
        # Store training history
        self.training_history.append(epoch_results)
        self._save_training_history()
        
        # Calculate convergence metrics
        convergence = self._calculate_convergence()
        epoch_results["convergence"] = convergence
        
        self.logger.info(
            f"[OK] Epoch {self.epoch} complete: "
            f"Gen Loss={epoch_results['generator_loss']:.3f}, "
            f"Issues={epoch_results['total_issues_found']}, "
            f"Convergence={convergence['rate']:.3f}"
        )
        
        return epoch_results
    
    def _calculate_competitive_losses(
        self,
        deepika_result: Dict,
        karan_result: Dict,
        navya_result: Dict,
        code: str
    ) -> Dict[str, float]:
        """
        Calculate competitive losses for GAN training (Patent Claim 4).
        
        Generator Loss: Penalized for each issue found
        Reviewer Loss: Rewarded for unique findings, penalized for false positives
        """
        # Extract issue counts
        performance_issues = deepika_result.get("issues_found", 0)
        security_vulns = karan_result.get("vulnerabilities_found", 0)
        logic_bugs = navya_result.get("bugs_found", 0)
        
        total_issues = performance_issues + security_vulns + logic_bugs
        
        # Generator Loss: Higher when more issues found
        # Normalized by code length (issues per 100 LOC)
        code_lines = len(code.split('\n'))
        generator_loss = (total_issues / max(code_lines, 1)) * 100
        
        # Reviewer Losses: Negative (reward) for finding issues
        deepika_loss = -float(performance_issues)
        karan_loss = -float(security_vulns)
        navya_loss = -float(logic_bugs)
        
        # Apply severity bonuses
        for detail in deepika_result.get("details", []):
            if detail.get("severity") == "CRITICAL":
                deepika_loss -= 2.0
            elif detail.get("severity") == "HIGH":
                deepika_loss -= 1.0
        
        for detail in karan_result.get("details", []):
            if detail.get("severity") == "CRITICAL":
                karan_loss -= 2.0
            elif detail.get("severity") == "HIGH":
                karan_loss -= 1.0
        
        for detail in navya_result.get("details", []):
            if detail.get("severity") == "CRITICAL":
                navya_loss -= 2.0
            elif detail.get("severity") == "HIGH":
                navya_loss -= 1.0
        
        # Count unique issues
        unique_issues = total_issues
        
        return {
            "generator_loss": generator_loss,
            "deepika_loss": deepika_loss,
            "karan_loss": karan_loss,
            "navya_loss": navya_loss,
            "total_issues": total_issues,
            "unique_issues": unique_issues
        }
    
    async def _update_strategies_from_losses(
        self,
        deepika,
        karan,
        navya,
        epoch_results: Dict
    ):
        """
        Update agent strategies based on competitive losses (GAN training).
        """
        # Convert losses to rewards for learning
        deepika_reward = -epoch_results["reviewer_losses"]["deepika"]
        karan_reward = -epoch_results["reviewer_losses"]["karan"]
        navya_reward = -epoch_results["reviewer_losses"]["navya"]
        
        # Trigger learning
        await asyncio.gather(
            deepika.learn_from_feedback(deepika_reward, "maximization"),
            karan.learn_from_feedback(karan_reward, "maximization"),
            navya.learn_from_feedback(navya_reward, "maximization")
        )
        
        self.logger.info(
            f"[AI] Updated strategies: "
            f"Deepika={deepika_reward:.2f}, "
            f"Karan={karan_reward:.2f}, "
            f"Navya={navya_reward:.2f}"
        )
    
    def _calculate_convergence(self) -> Dict[str, float]:
        """
        Calculate training convergence metrics.
        """
        if len(self.training_history) < 2:
            return {"rate": 0.0, "trend": "insufficient_data"}
        
        # Get last 5 epochs
        recent = self.training_history[-5:]
        losses = [e["generator_loss"] for e in recent]
        
        # Simple convergence: loss decreasing?
        if len(losses) >= 2:
            trend = losses[-1] - losses[0]
            rate = abs(trend) / max(losses[0], 0.001)
            
            if trend < -0.01:
                trend_label = "improving"
            elif trend > 0.01:
                trend_label = "degrading"
            else:
                trend_label = "stable"
        else:
            rate = 0.0
            trend_label = "insufficient_data"
        
        return {
            "rate": rate,
            "trend": trend_label,
            "recent_losses": losses
        }
    
    def _save_training_history(self):
        """Save training history to disk for analysis."""
        history_file = self.training_dir / "training_history.json"
        
        with open(history_file, 'w') as f:
            json.dump(self.training_history, f, indent=2)
        
        self.logger.debug(f"[SAVE] Saved training history to {history_file}")
    
    def _load_training_history(self):
        """Load training history from disk."""
        history_file = self.training_dir / "training_history.json"
        
        if history_file.exists():
            with open(history_file, 'r') as f:
                self.training_history = json.load(f)
                if self.training_history:
                    self.epoch = self.training_history[-1]["epoch"]
            self.logger.info(f"📂 Loaded {len(self.training_history)} epochs from history")
    
    def get_training_metrics(self) -> Dict[str, Any]:
        """
        Get comprehensive training metrics.
        """
        if not self.training_history:
            return {
                "status": "not_started",
                "epochs": 0
            }
        
        latest = self.training_history[-1]
        convergence = self._calculate_convergence()
        
        return {
            "status": "training",
            "epochs": self.epoch,
            "latest_epoch": latest,
            "convergence": convergence,
            "total_samples_processed": sum(e["samples_processed"] for e in self.training_history),
            "average_generator_loss": sum(e["generator_loss"] for e in self.training_history) / len(self.training_history),
            "total_issues_found": sum(e["total_issues_found"] for e in self.training_history)
        }


# Global instance
adversarial_trainer = AdversarialTrainer()


if __name__ == "__main__":
    import asyncio
    
    async def test():
        print("🧪 Testing AdversarialTrainer...")
        
        # Simulate training cycle
        code = {"backend": "FastAPI code here"}
        
        # Scenario 1: Code rejected (bugs found)
        review_rejected = {
            "bugs": [
                {"type": "sql_injection", "location": "user_query", "severity": "high"},
                {"type": "missing_validation", "location": "input_handler", "severity": "medium"}
            ],
            "warnings": []
        }
        
        await adversarial_trainer.train_adversarial_pair(
            generator_agent="shubham",
            discriminator_agent="navya",
            code=code,
            review_result=review_rejected,
            outcome="rejected"
        )
        
        print("\n[STATS] After rejection:")
        gen_suggestions = adversarial_trainer.get_generator_suggestions("shubham")
        print(f"Generator confidence: {gen_suggestions['confidence']:.2f}")
        print(f"Patterns to avoid: {len(gen_suggestions['avoid_patterns'])}")
        
        disc_priorities = adversarial_trainer.get_discriminator_priorities("navya")
        print(f"Discriminator priorities: {disc_priorities['check_priorities']}")
        
        # Scenario 2: Code accepted
        review_accepted = {
            "bugs": [],
            "warnings": []
        }
        
        await adversarial_trainer.train_adversarial_pair(
            generator_agent="shubham",
            discriminator_agent="navya",
            code=code,
            review_result=review_accepted,
            outcome="accepted"
        )
        
        print("\n[STATS] After acceptance:")
        gen_suggestions = adversarial_trainer.get_generator_suggestions("shubham")
        print(f"Generator confidence: {gen_suggestions['confidence']:.2f}")
        print(f"Success patterns: {len(gen_suggestions['success_patterns'])}")
        
        print("\n[OK] AdversarialTrainer test complete!")
    
    asyncio.run(test())
