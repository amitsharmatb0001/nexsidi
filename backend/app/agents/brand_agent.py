"""
BRAND AGENT - Visual Design Evaluation with Screenshot Analysis

Purpose: Evaluate actual rendered designs using visual AI (not HTML/JSX code)
Technology: Gemini Vision API with screenshot analysis
Evaluation: Based on real user experience, not code structure

Evaluation Criteria:
- Instant Visual Clarity (5-second test on actual design)
- Visual Uniqueness (template detection via visual similarity)
- Emotional Design (trust indicators, visual hierarchy, professionalism)
- Value Proposition Visibility (clear messaging, CTA prominence)

Minimum Pass Score: 35/40 (87.5%)

Model: Gemini 3 Pro with Vision capabilities
"""

from typing import Dict, Any, List, Optional
import json
import re
import logging
import base64
from pathlib import Path

# AI Router
from app.services.ai_router import ai_router, TaskComplexity
from app.agents.mixins import (
    SearchCapableMixin, 
    MistakeMemoryMixin, 
    PermanentMemoryMixin,
    ContextManagementMixin,
    DecisionLedgerMixin,
    ProgressMixin
)


class BrandAgent(MistakeMemoryMixin, PermanentMemoryMixin, ContextManagementMixin, DecisionLedgerMixin, SearchCapableMixin, ProgressMixin):
    """
    Visual design evaluator using screenshot analysis.
    
    Follows standalone V2 architecture - no BaseAgent inheritance.
    Uses Gemini Vision API for actual visual assessment.
    
    Usage:
        brand_agent = BrandAgent(project_id="proj-123")
        result = await brand_agent.evaluate_screenshots(
            screenshots={"desktop": "/path/to/desktop.png", ...},
            business_description="Online bakery"
        )
    """
    
    def __init__(self, project_id: str):
        """
        Initialize BRAND AGENT.
        
        Args:
            project_id: Unique identifier for the project being evaluated
        """
        super().__init__()
        # Standalone - no inheritance
        self.project_id = project_id
        
        # AI Router
        self.ai_router = ai_router
        
        # Logging
        self.logger = logging.getLogger("agent.brand_agent")
        self.logger.setLevel(logging.INFO)
        
        # Statistics tracking
        self.total_evaluations = 0
        self.total_passed = 0
        self.total_failed = 0
        self.average_score = 0.0
    
    async def evaluate(
        self,
        design_html: str,
        business_description: str,
        target_audience: str = "general public"
    ) -> Dict[str, Any]:
        """
        Evaluate design from HTML/code (for testing only).
        
        NOTE: This is a simplified version for testing. In production,
        use evaluate_screenshots() for actual visual analysis.
        
        Args:
            design_html: HTML code to evaluate
            business_description: Description of the business
            target_audience: Target audience for the business
        
        Returns:
            Dict containing evaluation results
        """
        try:
            self.total_evaluations += 1
            self.logger.info(f"🎨 Starting brand evaluation #{self.total_evaluations} (HTML mode)")
            
            # Build text-based evaluation prompt
            prompt = f"""You are BRAND AGENT evaluating HTML/code design quality.

BUSINESS CONTEXT:
Business: {business_description}
Target Audience: {target_audience}

HTML CODE TO EVALUATE:
{design_html}

Evaluate based on code analysis (not visual):

1. **INSTANT CLARITY** [0-10]: Does the HTML show clear headlines, value props, CTAs?
2. **UNIQUENESS** [0-10]: Custom design or generic template? Check for unique colors, layouts, content.
3. **EMOTIONAL CONNECTION** [0-10]: Testimonials, personal stories, trust indicators in content?
4. **VALUE PROPOSITION** [0-10]: Clear benefits, guarantees, differentiation in text?

MINIMUM PASSING SCORE: 35/40 (87.5%)

RESPOND IN VALID JSON (no markdown, no backticks):
{{
    "agent": "BRAND_AGENT",
    "overall_score": 37,
    "instant_clarity": 9,
    "uniqueness": 8,
    "emotional_connection": 10,
    "value_proposition": 10,
    "passed": true,
    "feedback": "Analysis summary",
    "improvements": ["Suggestion 1", "Suggestion 2"],
    "breakdown": {{
        "instant_clarity": {{"score": 9, "reason": "Clear headline and CTA"}},
        "uniqueness": {{"score": 8, "reason": "Custom color palette"}},
        "emotional_connection": {{"score": 10, "reason": "Personal founder story"}},
        "value_proposition": {{"score": 10, "reason": "Clear differentiators"}}
    }}
}}

IMPORTANT: Return ONLY valid JSON."""

            response = await self.ai_router.generate(
                messages=[{"role": "user", "content": prompt}],
                task_type="brand_evaluation",
                complexity=TaskComplexity.COMPLEX
            )
            
            self.logger.info(
                f"✅ {response.output_tokens} tokens, "
                f"₹{response.cost_estimate:.4f}"
            )
            
            # Parse response
            result = self._parse_response(response.content)
            
            # Update statistics
            score = result.get("overall_score", 0)
            passed = result.get("passed", False)
            
            if passed:
                self.total_passed += 1
            else:
                self.total_failed += 1
            
            self.average_score = (
                (self.average_score * (self.total_evaluations - 1) + score) 
                / self.total_evaluations
            )
            
            self.logger.info(
                f"🎯 BRAND AGENT evaluation: {score}/40 "
                f"({'PASSED ✓' if passed else 'FAILED ✗'}) "
                f"(avg: {self.average_score:.1f}/40)"
            )
            
            return result
            
        except json.JSONDecodeError as e:
            self.logger.error(f"❌ Invalid JSON response: {e}")
            return self._error_response("Failed to parse AI response")
            
        except Exception as e:
            self.logger.error(f"❌ Brand evaluation failed: {e}")
            raise
    
    async def evaluate_screenshots(
        self,
        screenshots: Dict[str, str],
        business_description: str,
        target_audience: str = "general public"
    ) -> Dict[str, Any]:
        """
        Evaluate design using actual screenshots (primary method).
        
        Args:
            screenshots: Dict mapping screen sizes to screenshot paths
                        {"desktop": "/path/to/desktop.png", "mobile": "/path/to/mobile.png"}
            business_description: Description of the business
            target_audience: Target audience for the business
        
        Returns:
            Dict containing comprehensive visual evaluation
        """
        try:
            self.total_evaluations += 1
            self.logger.info(f"🎨 Starting visual evaluation #{self.total_evaluations}")
            
            # Load and encode screenshots
            await self._send_progress("design_validation", 20, "Loading screenshots and preparing visual analysis pipeline...")
            encoded_screenshots = {}
            for size, path in screenshots.items():
                if path and Path(path).exists():
                    encoded_screenshots[size] = self._encode_image(path)
                    self.logger.info(f"📸 Loaded {size} screenshot")
            
            if not encoded_screenshots:
                raise ValueError("No valid screenshots provided")
            
            # Build visual evaluation prompt
            await self._send_progress("design_validation", 50, "Evaluating visual clarity, uniqueness, and brand trust indicators...")
            prompt = self._build_visual_prompt(
                business_description,
                target_audience,
                list(encoded_screenshots.keys())
            )
            
            # Call Gemini Vision API
            # Note: Gemini Vision uses different message format
            messages = self._build_vision_messages(prompt, encoded_screenshots)
            
            response = await self.ai_router.generate(
                messages=messages,
                task_type="brand_evaluation",
                complexity=TaskComplexity.COMPLEX
            )
            
            # Log cost
            self.logger.info(
                f"✅ {response.output_tokens} tokens, "
                f"₹{response.cost_estimate:.4f}"
            )
            
            # Parse and validate response
            await self._send_progress("design_validation", 90, f"Finalizing design quality score (Pass threshold: 35/40)...")
            result = self._parse_response(response.content)
            
            await self._send_progress("design_validation", 100, f"Design evaluation complete. Score: {result.get('overall_score', 0)}/40. Result: {'PASS' if result.get('passed') else 'FAIL'}")
            
            # Add screenshot paths to result
            result["screenshots_evaluated"] = list(screenshots.keys())
            
            # Update statistics
            score = result.get("overall_score", 0)
            passed = result.get("passed", False)
            
            if passed:
                self.total_passed += 1
            else:
                self.total_failed += 1
            
            # Update average score
            self.average_score = (
                (self.average_score * (self.total_evaluations - 1) + score) 
                / self.total_evaluations
            )
            
            self.logger.info(
                f"🎯 BRAND AGENT evaluation: {score}/40 "
                f"({'PASSED ✓' if passed else 'FAILED ✗'}) "
                f"(avg: {self.average_score:.1f}/40)"
            )
            
            return result
            
        except json.JSONDecodeError as e:
            self.logger.error(f"❌ Invalid JSON response: {e}")
            return self._error_response("Failed to parse AI response")
            
        except Exception as e:
            self.logger.error(f"❌ Brand evaluation failed: {e}")
            raise
    
    def _encode_image(self, image_path: str) -> str:
        """
        Encode image to base64 for Gemini Vision API.
        
        Args:
            image_path: Path to image file
        
        Returns:
            Base64 encoded image string
        """
        with open(image_path, 'rb') as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    
    def _build_vision_messages(
        self,
        prompt: str,
        encoded_screenshots: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        """
        Build message format for Gemini Vision API.
        
        Gemini Vision expects specific format with inline images.
        """
        # For Gemini, we send images inline with the prompt
        # The AI Router will handle the Gemini-specific formatting
        
        # Create content parts: text + images
        content_parts = [prompt]
        
        # Add each screenshot
        for size, base64_data in encoded_screenshots.items():
            content_parts.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64_data
                }
            })
        
        return [{"role": "user", "content": content_parts}]
    
    def _build_visual_prompt(
        self,
        business_description: str,
        target_audience: str,
        screen_sizes: List[str]
    ) -> str:
        """
        Build visual evaluation prompt for Gemini Vision.
        
        This prompt is optimized for visual analysis of screenshots.
        """
        return f"""You are BRAND AGENT, a visual design evaluation expert analyzing ACTUAL screenshots.

BUSINESS CONTEXT:
Business: {business_description}
Target Audience: {target_audience}
Screen Sizes Provided: {', '.join(screen_sizes)}

YOUR MISSION: Evaluate the VISUAL design quality by analyzing the provided screenshots.

EVALUATION CRITERIA (Score each 0-10):

1. **INSTANT VISUAL CLARITY (5-Second Test)** [0-10]
   Look at the screenshots and ask:
   - Within 5 seconds of viewing, is the business purpose crystal clear?
   - Is the main headline/value proposition visible and readable?
   - Is the primary call-to-action obvious and prominent?
   - Can you tell what this business does WITHOUT reading everything?
   - Does visual hierarchy guide the eye to key information?
   
   Visual indicators:
   ✓ Large, clear headline above the fold
   ✓ Obvious CTA button (contrasting color, prominent placement)
   ✓ Clean layout without clutter
   ✓ Clear visual hierarchy (size, contrast, spacing)
   
   Scoring:
   - 9-10: Instant understanding, perfect clarity
   - 7-8: Clear with minor improvements possible
   - 5-6: Requires effort to understand
   - 3-4: Confusing, unclear purpose
   - 0-2: Completely unclear

2. **VISUAL UNIQUENESS (Template Detection)** [0-10]
   Analyze the visual design:
   - Does this look like a generic Bootstrap/WordPress template?
   - Are colors distinctive or generic (blue/grey/white only)?
   - Is the layout unique or cookie-cutter?
   - Do visual elements show custom design work?
   - Would you recognize this brand if you saw it again?
   
   Red Flags (reduce score):
   ❌ Generic stock photos (overused business imagery)
   ❌ Default template colors (Bootstrap blue, grey, white only)
   ❌ Standard navbar + hero + 3-column features layout
   ❌ Generic icons without customization
   ❌ Lorem ipsum or placeholder content visible
   
   Green Flags (increase score):
   ✓ Custom color palette (distinctive brand colors)
   ✓ Unique layout or asymmetric design
   ✓ Custom illustrations or real photography
   ✓ Consistent brand identity across screens
   ✓ Distinctive typography choices
   
   Scoring:
   - 9-10: Highly unique, memorable visual identity
   - 7-8: Some unique elements, professional execution
   - 5-6: Mix of unique and generic elements
   - 3-4: Mostly template with minor changes
   - 0-2: Pure template, zero visual uniqueness

3. **EMOTIONAL VISUAL DESIGN** [0-10]
   Evaluate the emotional impact:
   - Does the visual design create trust and professionalism?
   - Are there humanizing elements (testimonials, real photos, personal story)?
   - Does color psychology align with business type?
   - Is whitespace used effectively (not cramped or overwhelming)?
   - Do visual elements evoke appropriate emotions?
   
   Trust indicators:
   ✓ Professional photography (not obviously stock)
   ✓ Visible testimonials or social proof
   ✓ Clean, organized layout
   ✓ Consistent design quality
   ✓ Appropriate color mood (warm for family business, bold for tech, etc.)
   
   Scoring:
   - 9-10: Strong emotional connection, builds immediate trust
   - 7-8: Good professional feel with personality
   - 5-6: Neutral, neither connecting nor disconnecting
   - 3-4: Cold, impersonal, or low-trust design
   - 0-2: Actively unprofessional or suspicious

4. **VALUE PROPOSITION VISIBILITY** [0-10]
   Assess how clearly value is communicated visually:
   - Is the unique value proposition prominently displayed?
   - Are key benefits visible without scrolling? (above the fold)
   - Is there visual emphasis on differentiation?
   - Are there visual proof points (numbers, results, guarantees)?
   - Is the call-to-action compelling and visible?
   
   Visual assessment:
   ✓ Value prop in large text near top
   ✓ Visual hierarchy emphasizes benefits
   ✓ Numbers/statistics displayed prominently
   ✓ CTA button stands out with size and color
   ✓ Benefit icons or graphics reinforce messaging
   
   Scoring:
   - 9-10: Value proposition impossible to miss
   - 7-8: Clear value with good visual emphasis
   - 5-6: Value present but not visually emphasized
   - 3-4: Unclear or buried value proposition
   - 0-2: No visible value proposition

RESPONSIVE DESIGN ASSESSMENT:
If multiple screen sizes provided, also evaluate:
- Does the design work well on all screen sizes?
- Is text readable on mobile?
- Are touch targets appropriately sized?
- Does layout adapt gracefully?
(This affects all scores if responsive design fails)

MINIMUM PASSING SCORE: 35/40 (87.5%)

BE HONEST AND CRITICAL based on what you SEE in the screenshots.
Generic template designs should score 20-28/40.
Professional unique designs should score 36-40/40.

RESPOND IN VALID JSON (no markdown, no backticks):
{{
    "agent": "BRAND_AGENT",
    "overall_score": 37,
    "instant_clarity": 9,
    "uniqueness": 8,
    "emotional_connection": 10,
    "value_proposition": 10,
    "passed": true,
    "feedback": "Strong visual design with clear value proposition. The bakery's specialty is immediately obvious from the hero section. Custom color palette and real photography create trust.",
    "improvements": [
        "Mobile CTA button could be more prominent",
        "Add customer photo testimonials for stronger trust",
        "Increase contrast on secondary text for better readability"
    ],
    "responsive_notes": "Design adapts well across all screen sizes. Mobile layout maintains clarity.",
    "breakdown": {{
        "instant_clarity": {{
            "score": 9,
            "reason": "Large headline 'Custom Cakes for Every Occasion' is immediately visible. Clear CTA button stands out."
        }},
        "uniqueness": {{
            "score": 8,
            "reason": "Custom purple/gold color palette is distinctive. Layout shows custom design work, though footer is somewhat generic."
        }},
        "emotional_connection": {{
            "score": 10,
            "reason": "Warm colors and 'Family-owned since 1995' text create immediate trust. Real bakery photos visible."
        }},
        "value_proposition": {{
            "score": 10,
            "reason": "Three clear differentiators prominently displayed: 'Custom designs, Same-day delivery, 100% guarantee'."
        }}
    }}
}}

IMPORTANT: Return ONLY valid JSON. No markdown. No backticks."""

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
            "agent": "BRAND_AGENT",
            "overall_score": 0,
            "instant_clarity": 0,
            "uniqueness": 0,
            "emotional_connection": 0,
            "value_proposition": 0,
            "passed": False,
            "feedback": "",
            "improvements": [],
            "error": error_message
        }
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get agent performance statistics."""
        return {
            "total_evaluations": self.total_evaluations,
            "total_passed": self.total_passed,
            "total_failed": self.total_failed,
            "pass_rate": (
                (self.total_passed / self.total_evaluations * 100) 
                if self.total_evaluations > 0 else 0
            ),
            "average_score": self.average_score
        }


if __name__ == "__main__":
    import asyncio
    
    async def test():
        brand_agent = BrandAgent(project_id="test-brand-001")
        
        # This would use actual screenshots from AARAV
        screenshots = {
            "desktop": "/path/to/desktop.png",
            "mobile": "/path/to/mobile.png"
        }
        
        result = await brand_agent.evaluate_screenshots(
            screenshots=screenshots,
            business_description="Online bakery specializing in custom cakes",
            target_audience="People planning weddings and celebrations"
        )
        
        print(json.dumps(result, indent=2))
        print(f"\nStatistics: {brand_agent.get_statistics()}")
    
    # asyncio.run(test())  # Commented - needs actual screenshots
    print("BRAND AGENT ready. Use with AARAV for screenshot capture.")
