
import json
import logging
from typing import Dict, Any, List
from app.services.ai_router import ai_router, TaskComplexity


class CollaborationSession:
    """
    Structured collaboration between agents.
    
    Instead of: Navya → bug list → Shubham fixes blindly
    Now: Navya explains → Shubham asks questions → Research searches → Shubham fixes with understanding
    """
    
    def __init__(self, project_id: str, participants: List[str]):
        self.project_id = project_id
        self.participants = participants
        self.discussion = []
        self.logger = logging.getLogger("collaboration")
    
    async def start_review_discussion(
        self,
        reviewer_name: str,
        developer_name: str,
        code: str,
        bugs: List[Dict]
    ) -> Dict[str, Any]:
        """
        Facilitate a review discussion between reviewer and developer.
        
        Flow:
        1. Reviewer explains each bug with context
        2. Developer asks clarifying questions
        3. If stuck, ResearchAgent searches for solutions
        4. Developer proposes fix approach
        5. Reviewer validates approach
        6. Developer implements fix
        """
        
        resolved_bugs = []
        
        for bug in bugs:
            # Step 1: Reviewer explains the bug in detail
            explanation = await self._get_explanation(
                reviewer_name, bug, code
            )
            self.discussion.append({
                "speaker": reviewer_name,
                "type": "explanation",
                "content": explanation
            })
            
            # Step 2: Developer asks clarifying questions
            questions = await self._get_clarifying_questions(
                developer_name, bug, explanation, code
            )
            self.discussion.append({
                "speaker": developer_name,
                "type": "question",
                "content": questions
            })
            
            # Step 3: Search for solutions if complex
            if bug.get("severity") in ["high", "critical"]:
                max_retries = 3
                retry_count = 0
                search_result = None
                
                while retry_count < max_retries and search_result is None:
                    try:
                        from app.agents.research_agent import ResearchAgent
                        researcher = ResearchAgent(self.project_id)
                        search_result = await researcher.find_error_solution(
                            error=f"{bug.get('type')}: {bug.get('description')}",
                            context=code[:1000]
                        )
                        self.discussion.append({
                            "speaker": "research",
                            "type": "solution",
                            "content": search_result.get("findings", "")
                        })
                        break
                    except ImportError as e:
                        self.logger.error(f"[ERROR] Research agent not available: {e}")
                        break  # Don't retry import errors
                    except ConnectionError as e:
                        retry_count += 1
                        self.logger.warning(
                            f"[WARN] Research agent connection failed (attempt {retry_count}/{max_retries}): {e}"
                        )
                        if retry_count >= max_retries:
                            self.logger.error("[ERROR] Research agent failed after max retries")
                            # Add fallback message
                            self.discussion.append({
                                "speaker": "system",
                                "type": "error",
                                "content": "Research agent unavailable. Proceeding without external research."
                            })
                        else:
                            # Exponential backoff
                            await __import__('asyncio').sleep(2 ** retry_count)
                    except Exception as e:
                        self.logger.error(
                            f"[ERROR] Research agent failed with unexpected error: {type(e).__name__}: {e}",
                            exc_info=True
                        )
                        # For critical bugs, raise the error instead of suppressing
                        if bug.get("severity") == "critical":
                            raise
                        break
            
            # Step 4: Developer proposes fix approach
            fix_approach = await self._propose_fix(
                developer_name, bug, self.discussion[-3:]
            )
            self.discussion.append({
                "speaker": developer_name,
                "type": "proposal",
                "content": fix_approach
            })
            
            resolved_bugs.append({
                **bug,
                "discussion": self.discussion[-4:],
                "proposed_fix": fix_approach
            })
        
        return {
            "resolved_bugs": resolved_bugs,
            "discussion_log": self.discussion,
            "total_exchanges": len(self.discussion)
        }
    
    async def _get_explanation(
        self, reviewer: str, bug: Dict, code: str
    ) -> str:
        """Reviewer explains the bug with full context."""
        prompt = f"""You are {reviewer}, a code reviewer. Explain this bug clearly:

Bug: {bug.get('type')} - {bug.get('description')}
Location: {bug.get('location', 'unknown')}
Severity: {bug.get('severity', 'medium')}

Relevant code section:
{code[:3000]}

Explain:
1. What exactly is wrong
2. Why it's dangerous
3. What could happen if not fixed
4. General direction for fixing it"""

        response = await ai_router.generate(
            messages=[{"role": "user", "content": prompt}],
            task_type="review_explanation",
            complexity=TaskComplexity.MODERATE,
            max_tokens=1000
        )
        return response.content
    
    async def _get_clarifying_questions(
        self, developer: str, bug: Dict, explanation: str, code: str
    ) -> str:
        """Developer asks questions about the bug."""
        prompt = f"""You are {developer}, a developer. The reviewer found this bug:

{explanation}

Ask 1-2 specific clarifying questions if anything is unclear.
If the explanation is clear enough, say "Understood, I'll fix this." """

        response = await ai_router.generate(
            messages=[{"role": "user", "content": prompt}],
            task_type="developer_question",
            complexity=TaskComplexity.SIMPLE,
            max_tokens=500
        )
        return response.content
    
    async def _propose_fix(
        self, developer: str, bug: Dict, discussion: list
    ) -> str:
        """Developer proposes fix approach based on discussion."""
        context = "\n".join([
            f"[{d['speaker']}]: {d['content']}" for d in discussion
        ])
        
        prompt = f"""You are {developer}. Based on this discussion:

{context}

Propose your exact fix approach in 2-3 sentences. 
Be specific about what code changes you'll make."""

        response = await ai_router.generate(
            messages=[{"role": "user", "content": prompt}],
            task_type="fix_proposal",
            complexity=TaskComplexity.SIMPLE,
            max_tokens=500
        )
        return response.content
