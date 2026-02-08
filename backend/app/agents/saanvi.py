# =============================================================================
# SAANVI - REQUIREMENTS ANALYST AGENT
# Location: backend/app/agents/saanvi.py
# Purpose: Analyze conversations and create structured specifications
# =============================================================================
#
# SAANVI'S CORE RESPONSIBILITIES:
# 1. Analyze conversation from Tilotma
# 2. Extract requirements (functional & non-functional)
# 3. Detect project complexity (1-10 scale)
# 4. Calculate realistic pricing
# 5. Estimate timeline
# 6. Generate structured specification document
#
# KEY FEATURES:
# - Intelligent requirement extraction
# - Complexity-based pricing
# - Technology stack recommendation
# - Risk assessment
# - Missing information detection
# - OTP-based approval system
#
# =============================================================================

import os
import json
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
import uuid

# Import AI Router
try:
    from app.services.ai_router import ai_router, TaskComplexity
except ImportError:
    from app.services.ai_router import ai_router
    from enum import Enum
    class TaskComplexity(Enum):
        SIMPLE = "simple"
        MEDIUM = "medium"
        COMPLEX = "complex"

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import Engines
from app.services.prompt_engine import prompt_engine
from app.services.context_engine import context_engine
from app.agents.mixins import (
    MistakeMemoryMixin, 
    SearchCapableMixin, 
    PermanentMemoryMixin,
    ContextManagementMixin,
    DecisionLedgerMixin
)
from app.utils.json_utils import safe_json_parse


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class ProjectType(Enum):
    """Types of projects"""
    LANDING_PAGE = "landing_page"
    BUSINESS_WEBSITE = "business_website"
    ECOMMERCE = "ecommerce"
    SAAS = "saas"
    MOBILE_APP = "mobile_app"
    WEB_APP = "web_app"
    API_SERVICE = "api_service"
    CUSTOM = "custom"


class TechStack(Enum):
    """Recommended technology stacks"""
    STATIC_HTML = "static_html"  # Simple sites
    REACT_FASTAPI = "react_fastapi"  # Standard web apps
    NEXTJS_FASTAPI = "nextjs_fastapi"  # SEO-critical
    VUE_FASTAPI = "vue_fastapi"  # Enterprise
    FLUTTER = "flutter"  # Mobile apps


@dataclass
class Feature:
    """A single feature requirement"""
    name: str
    description: str
    is_critical: bool = True
    estimated_hours: int = 0


@dataclass
class TechStackSpec:
    """Technology stack specification"""
    frontend: str
    backend: str
    database: str
    deployment: str
    additional: List[str] = field(default_factory=list)


@dataclass
class PricingBreakdown:
    """Detailed pricing breakdown"""
    complexity_score: int  # 1-10
    base_price: float
    features_cost: float
    tech_complexity_cost: float
    total_price: float
    currency: str = "INR"
    
    # Iteration allowances
    mid_level_changes: int = 5
    small_changes: int = 11


@dataclass
class Timeline:
    """Project timeline estimate"""
    total_days: int
    phases: Dict[str, int]  # phase_name: days
    estimated_start: str
    estimated_completion: str


@dataclass
class RequirementsSpec:
    """Complete requirements specification"""
    project_id: str
    project_name: str
    project_type: ProjectType
    
    # Requirements
    functional_requirements: List[Feature]
    non_functional_requirements: List[str]
    
    # Technical
    tech_stack: TechStackSpec
    
    # Business
    pricing: PricingBreakdown
    timeline: Timeline
    
    # Meta
    created_at: datetime
    approved: bool = False
    approval_otp: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "project_type": self.project_type.value,
            "functional_requirements": [
                {
                    "name": f.name,
                    "description": f.description,
                    "is_critical": f.is_critical,
                    "estimated_hours": f.estimated_hours
                }
                for f in self.functional_requirements
            ],
            "non_functional_requirements": self.non_functional_requirements,
            "tech_stack": {
                "frontend": self.tech_stack.frontend,
                "backend": self.tech_stack.backend,
                "database": self.tech_stack.database,
                "deployment": self.tech_stack.deployment,
                "additional": self.tech_stack.additional
            },
            "pricing": {
                "complexity_score": self.pricing.complexity_score,
                "base_price": self.pricing.base_price,
                "total_price": self.pricing.total_price,
                "currency": self.pricing.currency,
                "mid_level_changes": self.pricing.mid_level_changes,
                "small_changes": self.pricing.small_changes
            },
            "timeline": {
                "total_days": self.timeline.total_days,
                "phases": self.timeline.phases,
                "estimated_start": self.timeline.estimated_start,
                "estimated_completion": self.timeline.estimated_completion
            },
            "created_at": self.created_at.isoformat(),
            "approved": self.approved
        }


# =============================================================================
# SAANVI AGENT CLASS
# =============================================================================

class Saanvi(MistakeMemoryMixin, PermanentMemoryMixin, ContextManagementMixin, DecisionLedgerMixin, SearchCapableMixin):
    """
    Requirements Analyst Agent
    
    Responsibilities:
    - Analyze conversations
    - Extract structured requirements
    - Recommend technology stack
    - Calculate pricing
    - Estimate timeline
    - Generate specification document
    
    Intelligence Features:
    - Complexity-based pricing
    - Smart tech stack selection
    - Realistic timeline estimation
    - Missing information detection
    """
    
    def __init__(self, project_id: str, user_id: str):
        """
        Initialize Saanvi for a project.
        
        Args:
            project_id: UUID of the project
            user_id: UUID of the user
        """
        # Initialize MistakeMemoryMixin
        super().__init__()
        
        self.project_id = project_id
        self.user_id = user_id
        self.agent_name = "saanvi"  # For mistake memory
        self.logger = logging.getLogger(f"saanvi.{project_id}")
        
        self.logger.info(f"📋 Saanvi initialized for project {project_id}")
    
    # =========================================================================
    # MAIN ANALYSIS METHOD
    # =========================================================================
    
    async def analyze_requirements_from_text(self, description: str) -> Dict[str, Any]:
        """
        Analyze requirements from a single project description string.
        (Wrapper for analyze_requirements)
        """
        # Create a synthetic conversation
        synthentic_conversation = [
            {"role": "user", "content": f"I want to build this: {description}"}
        ]
        
        spec = await self.analyze_requirements(synthentic_conversation)
        return spec.to_dict()

    async def analyze_requirements(
        self,
        conversation: List[Dict[str, str]],
        project_name: Optional[str] = None
    ) -> RequirementsSpec:
        """
        Analyze conversation and generate complete requirements specification.
        
        Workflow:
        1. Summarize conversation
        2. Extract requirements
        3. Detect project type
        4. Calculate complexity
        5. Recommend tech stack
        6. Calculate pricing
        7. Estimate timeline
        8. Generate specification
        
        Args:
            conversation: List of messages [{"role": "user/assistant", "content": "..."}]
            project_name: Optional project name
        
        Returns:
            Complete RequirementsSpec
        """
        
        self.logger.info("🔍 Starting requirements analysis...")
        
        # Step 1: Summarize conversation (keep it short!)
        summary = self._summarize_conversation(conversation)
        
        # Step 2: Extract requirements
        self.logger.info("📝 Extracting requirements...")
        requirements = await self._extract_requirements(summary)
        
        # Step 3: Detect project type
        self.logger.info("🎯 Detecting project type...")
        project_type = self._detect_project_type(requirements)
        
        # Step 4: Calculate complexity
        self.logger.info("📊 Calculating complexity...")
        complexity = await self._calculate_complexity(requirements, project_type)
        
        # Step 5: Recommend tech stack
        self.logger.info("⚙️ Recommending technology stack...")
        tech_stack = await self._recommend_tech_stack(project_type, complexity, requirements)
        
        # Step 6: Calculate pricing
        self.logger.info("💰 Calculating pricing...")
        pricing = self._calculate_pricing(complexity, requirements)
        
        # Step 7: Estimate timeline
        self.logger.info("📅 Estimating timeline...")
        timeline = self._estimate_timeline(complexity, project_type)
        
        # Step 8: Generate specification
        spec = RequirementsSpec(
            project_id=self.project_id,
            project_name=project_name or f"Project_{self.project_id[:8]}",
            project_type=project_type,
            functional_requirements=requirements["functional"],
            non_functional_requirements=requirements["non_functional"],
            tech_stack=tech_stack,
            pricing=pricing,
            timeline=timeline,
            created_at=datetime.now()
        )
        
        # Store context
        context_engine.store_context(
            self.project_id, 
            "requirements", 
            {"spec": spec.to_dict(), "timestamp": datetime.now().timestamp()}
        )
        
        self.logger.info(f"✅ Requirements analysis complete!")
        self.logger.info(f"   Complexity: {complexity}/10")
        self.logger.info(f"   Pricing: ₹{pricing.total_price:,.0f}")
        self.logger.info(f"   Timeline: {timeline.total_days} days")
        
        return spec
    
    # =========================================================================
    # CONVERSATION PROCESSING
    # =========================================================================
    
    def _summarize_conversation(
        self,
        conversation: List[Dict[str, str]]
    ) -> str:
        """
        Summarize conversation into concise format.
        
        Keep only key information, max 500 chars total.
        
        Args:
            conversation: Full conversation
        
        Returns:
            Concise summary
        """
        
        # Extract key points (max 100 chars per message)
        key_points = []
        for msg in conversation:
            if msg["role"] == "user":
                content = msg["content"][:100]
                key_points.append(content)
        
        summary = " | ".join(key_points)
        return summary[:500]  # Max 500 chars
    
    async def _extract_requirements(
        self,
        conversation_summary: str
    ) -> Dict[str, Any]:
        """
        Extract structured requirements from conversation.
        
        Uses AI to identify functional and non-functional requirements.
        
        Args:
            conversation_summary: Concise conversation summary
        
        Returns:
            Dict with functional and non_functional requirements
        """
        
        # Prepare prompt using PromptEngine
        prompt = prompt_engine.get_prompt(
            "saanvi",
            "requirements_analysis", 
            context={
                "user_requirements": conversation_summary,
                "detected_features": "To be extracted",
                "target_audience": "General"
            }
        )
        
        try:
            # Call AI Router with higher complexity
            response = await ai_router.generate(
                messages=[{"role": "user", "content": prompt}],
                task_type="analysis",
                complexity=TaskComplexity.NORMAL, # Increased from SIMPLE
                max_tokens=2000 # Enough for detailed specs
            )
            
            # Parse JSON (H5)
            result = safe_json_parse(response.content)
            
            # Convert to Feature objects
            functional = []
            for item in result.get("functional", [])[:10]:  # Max 10
                functional.append(Feature(
                    name=item.get("name", "Feature"),
                    description=item.get("description", ""),
                    is_critical=item.get("is_critical", True)
                ))
            
            return {
                "functional": functional,
                "non_functional": result.get("non_functional", [])[:5]  # Max 5
            }
        
        except Exception as e:
            self.logger.error(f"Failed to extract requirements: {e}")
            # Return minimal requirements
            return {
                "functional": [
                    Feature(
                        name="Core Functionality",
                        description="Main application features",
                        is_critical=True
                    )
                ],
                "non_functional": ["Responsive design", "Fast loading"]
            }
    
    # =========================================================================
    # PROJECT ANALYSIS
    # =========================================================================
    
    def _detect_project_type(
        self,
        requirements: Dict[str, Any]
    ) -> ProjectType:
        """
        Detect project type from requirements.
        
        Args:
            requirements: Extracted requirements
        
        Returns:
            ProjectType enum
        """
        
        # Check functional requirements
        feature_names = " ".join([f.name.lower() for f in requirements["functional"]])
        
        # Simple pattern matching
        if "ecommerce" in feature_names or "shop" in feature_names or "cart" in feature_names:
            return ProjectType.ECOMMERCE
        elif "mobile" in feature_names or "app" in feature_names:
            return ProjectType.MOBILE_APP
        elif "saas" in feature_names or "multi-tenant" in feature_names:
            return ProjectType.SAAS
        elif "api" in feature_names:
            return ProjectType.API_SERVICE
        elif "landing" in feature_names or len(requirements["functional"]) <= 3:
            return ProjectType.LANDING_PAGE
        elif "business" in feature_names or "website" in feature_names:
            return ProjectType.BUSINESS_WEBSITE
        else:
            return ProjectType.WEB_APP
    
    async def _calculate_complexity(
        self,
        requirements: Dict[str, Any],
        project_type: ProjectType
    ) -> int:
        """
        Calculate project complexity using AI.
        """
        try:
            # Format requirements for prompt
            req_text = json.dumps(requirements, indent=2)
            
            prompt = prompt_engine.get_prompt(
                "saanvi",
                "complexity_estimation",
                context={
                    "requirements": req_text
                }
            )
            
            response = await ai_router.generate(
                messages=[{"role": "user", "content": prompt}],
                task_type="analysis",
                complexity=TaskComplexity.SIMPLE,
                max_tokens=200
            )
            
            content = response.content.replace("```json", "").replace("```", "").strip()
            result = json.loads(content)
            
            score = result.get("score", 5)
            # Ensure 1-10 range
            return min(10, max(1, int(score)))
            
        except Exception as e:
            self.logger.error(f"AI complexity estimation failed: {e}")
            # Fallback to simple calculation
            feature_count = len(requirements.get("functional", []))
            return min(10, max(1, feature_count // 2))
    
    async def _recommend_tech_stack(
        self,
        project_type: ProjectType,
        complexity: int,
        requirements: Dict[str, Any] = None # Added optional arg
    ) -> TechStackSpec:
        """
        Recommend appropriate technology stack using AI.
        """
        try:
            req_text = json.dumps(requirements, indent=2) if requirements else "Standard project"
            
            prompt = prompt_engine.get_prompt(
                "saanvi",
                "tech_stack_recommendation",
                context={
                    "requirements": req_text,
                    "project_type": project_type.value
                }
            )
            
            response = await ai_router.generate(
                messages=[{"role": "user", "content": prompt}],
                task_type="architecture",
                complexity=TaskComplexity.NORMAL,
                max_tokens=300
            )
            
            content = response.content.replace("```json", "").replace("```", "").strip()
            result = json.loads(content)
            
            return TechStackSpec(
                frontend=result.get("frontend", "React"),
                backend=result.get("backend", "FastAPI"),
                database=result.get("database", "PostgreSQL"),
                deployment=result.get("deployment", "Vercel + Railway"),
                additional=result.get("libraries", [])
            )
            
        except Exception as e:
            self.logger.error(f"AI tech stack recommendation failed: {e}")
            # Fallback
            return TechStackSpec(
                frontend="React",
                backend="FastAPI",
                database="PostgreSQL",
                deployment="Vercel + Railway",
                additional=["Standard Libraries"]
            )
    
    # =========================================================================
    # PRICING & TIMELINE
    # =========================================================================
    
    def _calculate_pricing(
        self,
        complexity: int,
        requirements: Dict[str, Any]
    ) -> PricingBreakdown:
        """
        Calculate realistic pricing based on complexity.
        
        Pricing model:
        - Complexity 1-2: ₹10,000 - ₹20,000
        - Complexity 3-4: ₹30,000 - ₹50,000
        - Complexity 5-6: ₹60,000 - ₹100,000
        - Complexity 7-8: ₹120,000 - ₹200,000
        - Complexity 9-10: ₹250,000 - ₹300,000
        
        Args:
            complexity: Complexity score
            requirements: Requirements dict
        
        Returns:
            PricingBreakdown
        """
        
        # Base pricing table
        base_prices = {
            1: 10000, 2: 15000, 3: 30000, 4: 40000, 5: 60000,
            6: 80000, 7: 120000, 8: 160000, 9: 250000, 10: 300000
        }
        
        base_price = base_prices.get(complexity, 50000)
        
        # Feature cost (₹5k per feature above 5)
        feature_count = len(requirements["functional"])
        features_cost = max(0, (feature_count - 5) * 5000)
        
        # Tech complexity (if auth/payment)
        non_func = " ".join(requirements["non_functional"]).lower()
        tech_cost = 0
        if "auth" in non_func:
            tech_cost += 10000
        if "payment" in non_func:
            tech_cost += 15000
        
        total = base_price + features_cost + tech_cost
        
        return PricingBreakdown(
            complexity_score=complexity,
            base_price=base_price,
            features_cost=features_cost,
            tech_complexity_cost=tech_cost,
            total_price=total,
            currency="INR"
        )
    
    def _estimate_timeline(
        self,
        complexity: int,
        project_type: ProjectType
    ) -> Timeline:
        """
        Estimate realistic project timeline.
        
        Args:
            complexity: Complexity score
            project_type: Type of project
        
        Returns:
            Timeline
        """
        
        # Base days by complexity
        base_days = {
            1: 2, 2: 3, 3: 5, 4: 7, 5: 10,
            6: 14, 7: 21, 8: 28, 9: 35, 10: 42
        }
        
        total_days = base_days.get(complexity, 14)
        
        # Phase breakdown
        phases = {
            "Requirements & Design": max(1, int(total_days * 0.15)),
            "Development": max(2, int(total_days * 0.60)),
            "Testing & QA": max(1, int(total_days * 0.15)),
            "Deployment": max(1, int(total_days * 0.10))
        }
        
        # Dates
        start_date = datetime.now()
        completion_date = start_date + timedelta(days=total_days)
        
        return Timeline(
            total_days=total_days,
            phases=phases,
            estimated_start=start_date.strftime("%Y-%m-%d"),
            estimated_completion=completion_date.strftime("%Y-%m-%d")
        )
    
    # =========================================================================
    # APPROVAL SYSTEM
    # =========================================================================
    
    def generate_approval_otp(self, spec: RequirementsSpec) -> str:
        """
        Generate OTP for specification approval.
        
        Args:
            spec: Requirements specification
        
        Returns:
            6-digit OTP
        """
        
        import random
        otp = str(random.randint(100000, 999999))
        spec.approval_otp = otp
        
        self.logger.info(f"🔐 Generated approval OTP: {otp}")
        
        return otp
    
    def verify_approval_otp(
        self,
        spec: RequirementsSpec,
        otp: str
    ) -> bool:
        """
        Verify OTP and approve specification.
        
        Args:
            spec: Requirements specification
            otp: OTP to verify
        
        Returns:
            True if approved
        """
        
        if spec.approval_otp == otp:
            spec.approved = True
            self.logger.info("✅ Specification approved!")
            return True
        else:
            self.logger.warning("❌ Invalid OTP")
            return False
    
    # =========================================================================
    # UTILITY METHODS
    # =========================================================================
    
    def export_spec_to_json(self, spec: RequirementsSpec) -> str:
        """
        Export specification as JSON string.
        
        Args:
            spec: Requirements specification
        
        Returns:
            JSON string
        """
        return json.dumps(spec.to_dict(), indent=2)
    
    def export_spec_to_markdown(self, spec: RequirementsSpec) -> str:
        """
        Export specification as Markdown document.
        
        Args:
            spec: Requirements specification
        
        Returns:
            Markdown string
        """
        
        md = f"""# {spec.project_name}
**Project ID:** {spec.project_id}  
**Type:** {spec.project_type.value}  
**Created:** {spec.created_at.strftime("%Y-%m-%d %H:%M")}  
**Status:** {'✅ Approved' if spec.approved else '⏳ Pending Approval'}

---

## Functional Requirements

"""
        
        for i, feature in enumerate(spec.functional_requirements, 1):
            critical = "🔴 Critical" if feature.is_critical else "🟡 Optional"
            md += f"{i}. **{feature.name}** ({critical})  \n"
            md += f"   {feature.description}\n\n"
        
        md += f"""---

## Non-Functional Requirements

"""
        for req in spec.non_functional_requirements:
            md += f"- {req}\n"
        
        md += f"""
---

## Technology Stack

- **Frontend:** {spec.tech_stack.frontend}
- **Backend:** {spec.tech_stack.backend}
- **Database:** {spec.tech_stack.database}
- **Deployment:** {spec.tech_stack.deployment}

---

## Pricing

- **Complexity Score:** {spec.pricing.complexity_score}/10
- **Base Price:** ₹{spec.pricing.base_price:,.0f}
- **Total Price:** ₹{spec.pricing.total_price:,.0f}
- **Included Changes:**
  - {spec.pricing.mid_level_changes} mid-level changes
  - {spec.pricing.small_changes} small changes

---

## Timeline

**Total Duration:** {spec.timeline.total_days} days  
**Start Date:** {spec.timeline.estimated_start}  
**Completion Date:** {spec.timeline.estimated_completion}

### Phase Breakdown:
"""
        
        for phase, days in spec.timeline.phases.items():
            md += f"- {phase}: {days} days\n"
        
        return md


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

if __name__ == "__main__":
    """
    Example of using Saanvi agent.
    
    Run this file directly to test:
    python saanvi.py
    """
    
    import asyncio
    
    async def test_saanvi():
        """Test basic requirements analysis"""
        
        print("\n" + "="*60)
        print("TESTING SAANVI - REQUIREMENTS ANALYST")
        print("="*60 + "\n")
        
        # Initialize Saanvi
        project_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        
        saanvi = Saanvi(project_id, user_id)
        
        # Simulate conversation
        conversation = [
            {"role": "user", "content": "I want to build a website"},
            {"role": "assistant", "content": "What kind of website?"},
            {"role": "user", "content": "For my restaurant"},
            {"role": "assistant", "content": "What features?"},
            {"role": "user", "content": "Online ordering, menu display, table reservations"},
        ]
        
        # Analyze requirements
        spec = await saanvi.analyze_requirements(
            conversation=conversation,
            project_name="Restaurant Website"
        )
        
        # Display results
        print("\n📋 REQUIREMENTS SPECIFICATION:")
        print("="*60)
        print(saanvi.export_spec_to_markdown(spec))
        
        # Generate OTP
        otp = saanvi.generate_approval_otp(spec)
        print(f"\n🔐 Approval OTP: {otp}")
        
        # Verify OTP
        approved = saanvi.verify_approval_otp(spec, otp)
        print(f"✅ Approved: {approved}")
        
        print("\n" + "="*60)
        print("TEST COMPLETE")
        print("="*60 + "\n")
    
    # Run test
    asyncio.run(test_saanvi())
