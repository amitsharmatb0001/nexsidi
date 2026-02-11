"""
TILOTMA MEMORY - Dedicated Memory System for Chief AI Officer
==============================================================

Purpose: Tilotma's private memory space for deep user understanding
Separate from shared context - this is HER understanding of conversations

Features:
- Conversation history with user
- User preferences and patterns
- Project understanding evolution
- Decision rationale storage
- Intervention history
- Quality concerns tracking
"""

import json
import time
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from app.services.context_engine import context_engine


@dataclass
class TilotmaConversationEntry:
    """Single conversation entry in Tilotma's memory"""
    timestamp: datetime
    user_message: str
    tilotma_response: str
    understanding: str  # What Tilotma understood from this exchange
    concerns: List[str]  # Any concerns raised
    decisions_made: List[str]  # Decisions made in this exchange
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "user_message": self.user_message,
            "tilotma_response": self.tilotma_response,
            "understanding": self.understanding,
            "concerns": self.concerns,
            "decisions_made": self.decisions_made
        }


@dataclass
class UserProfile:
    """Tilotma's understanding of the user"""
    user_id: str
    communication_style: str  # "technical", "business", "casual"
    typical_project_size: str  # "small", "medium", "large"
    risk_tolerance: str  # "conservative", "moderate", "aggressive"
    preferences: Dict[str, Any] = field(default_factory=dict)
    past_projects: List[str] = field(default_factory=list)


@dataclass
class ProjectUnderstanding:
    """Tilotma's evolving understanding of the project"""
    project_id: str
    initial_request: str
    current_understanding: str
    confidence_level: float  # 0.0 to 1.0
    project_type: Optional[str] = None  # NEW
    key_features: List[str] = field(default_factory=list)  # NEW
    user_confirmed: bool = False  # NEW
    missing_information: List[str] = field(default_factory=list)
    assumptions_made: List[str] = field(default_factory=list)
    risks_identified: List[str] = field(default_factory=list)
    quality_concerns: List[str] = field(default_factory=list)


class TilotmaMemory:
    """
    Tilotma's Dedicated Memory System
    
    This is SEPARATE from the shared context.
    It's Tilotma's private space for understanding users and projects.
    
    Storage:
    - Private: Tilotma's internal understanding
    - Shared: Can be accessed by Arjun when needed for coordination
    """
    
    def __init__(self, project_id: str, user_id: str):
        self.project_id = project_id
        self.user_id = user_id
        self.logger = logging.getLogger(f"tilotma_memory.{project_id}")
        
        # Load existing memory
        self.conversation_history: List[TilotmaConversationEntry] = []
        self.user_profile: Optional[UserProfile] = None
        self.project_understanding: Optional[ProjectUnderstanding] = None
        self.intervention_history: List[Dict] = []
        self.monitoring_log: List[Dict] = []
        
        self._load_memory()
    
    def _load_memory(self):
        """Load Tilotma's memory from storage"""
        # FIX: Ensure private memory for pre-project chat (no project_id)
        # Patent-compliant isolation: Use user_id as partition key if project_id missing
        storage_key = self.project_id if self.project_id else f"user_context_{self.user_id}"
        
        memory_data = context_engine.get_context(
            storage_key,
            "tilotma_private_memory"
        )
        
        if memory_data:
            self.logger.info("[LOAD] Loading Tilotma's existing memory...")
            
            # Load conversation history
            if "conversation_history" in memory_data:
                self.conversation_history = []
                for entry in memory_data["conversation_history"]:
                    # Create copy to avoid modifying original context
                    entry_copy = entry.copy()
                    
                    # Fix timestamp deserialization (str -> datetime)
                    if isinstance(entry_copy.get("timestamp"), str):
                        try:
                            # Handle ISO format
                            entry_copy["timestamp"] = datetime.fromisoformat(entry_copy["timestamp"])
                        except ValueError:
                            # Fallback if invalid
                            self.logger.warning("Invalid timestamp in memory, using now()")
                            entry_copy["timestamp"] = datetime.now()
                            
                    self.conversation_history.append(TilotmaConversationEntry(**entry_copy))
            
            # Load user profile
            if "user_profile" in memory_data:
                self.user_profile = UserProfile(**memory_data["user_profile"])
            
            # Load project understanding
            if "project_understanding" in memory_data:
                self.project_understanding = ProjectUnderstanding(
                    **memory_data["project_understanding"]
                )
            
            # Load intervention history
            self.intervention_history = memory_data.get("intervention_history", [])
            
            # Load monitoring log
            self.monitoring_log = memory_data.get("monitoring_log", [])
    
    def save_memory(self):
        """Save Tilotma's memory to storage"""
        memory_data = {
            "conversation_history": [
                entry.to_dict() for entry in self.conversation_history
            ],
            "user_profile": self.user_profile.__dict__ if self.user_profile else None,
            "project_understanding": (
                self.project_understanding.__dict__ 
                if self.project_understanding else None
            ),
            "intervention_history": self.intervention_history,
            "monitoring_log": self.monitoring_log,
            "last_updated": time.time()
        }
        
        # FIX: Consistent storage key for pre-project chat persistence
        storage_key = self.project_id if self.project_id else f"user_context_{self.user_id}"
        
        context_engine.store_context(
            storage_key,
            "tilotma_private_memory",
            memory_data
        )
        
        self.logger.info("[SAVE] Tilotma's memory saved")
    
    def add_conversation(
        self,
        user_message: str,
        tilotma_response: str,
        understanding: str,
        concerns: List[str] = None,
        decisions: List[str] = None
    ):
        """Add conversation entry to memory"""
        entry = TilotmaConversationEntry(
            timestamp=datetime.now(),
            user_message=user_message,
            tilotma_response=tilotma_response,
            understanding=understanding,
            concerns=concerns or [],
            decisions_made=decisions or []
        )
        
        self.conversation_history.append(entry)
        self.save_memory()
    
    def update_project_understanding(
        self,
        understanding: str,
        confidence: float,
        project_type: str = None,
        key_features: List[str] = None,
        user_confirmed: bool = False,
        missing_info: List[str] = None,
        assumptions: List[str] = None,
        risks: List[str] = None
    ):
        """Update Tilotma's understanding of the project"""
        if not self.project_understanding:
            self.project_understanding = ProjectUnderstanding(
                project_id=self.project_id,
                initial_request=understanding,
                current_understanding=understanding,
                confidence_level=confidence,
                project_type=project_type,
                key_features=key_features or [],
                user_confirmed=user_confirmed
            )
        else:
            self.project_understanding.current_understanding = understanding
            self.project_understanding.confidence_level = confidence
            if project_type:
                self.project_understanding.project_type = project_type
            if key_features:
                self.project_understanding.key_features = key_features
            if user_confirmed:
                self.project_understanding.user_confirmed = user_confirmed
        
        if missing_info:
            self.project_understanding.missing_information = missing_info
        if assumptions:
            self.project_understanding.assumptions_made = assumptions
        if risks:
            self.project_understanding.risks_identified = risks
        
        self.save_memory()
    
    def add_quality_concern(self, concern: str):
        """Add a quality concern"""
        if self.project_understanding:
            self.project_understanding.quality_concerns.append(concern)
            self.save_memory()
    
    def record_intervention(
        self,
        agent_name: str,
        issue: str,
        action_taken: str
    ):
        """Record an intervention Tilotma made"""
        intervention = {
            "timestamp": time.time(),
            "agent": agent_name,
            "issue": issue,
            "action": action_taken
        }
        
        self.intervention_history.append(intervention)
        self.save_memory()
    
    def add_monitoring_entry(self, agent_name: str, report: Dict):
        """Add entry to monitoring log"""
        entry = {
            "timestamp": time.time(),
            "agent": agent_name,
            "report": report
        }
        
        self.monitoring_log.append(entry)
        
        # Keep only last 100 entries to avoid memory bloat
        if len(self.monitoring_log) > 100:
            self.monitoring_log = self.monitoring_log[-100:]
        
        self.save_memory()
    
    def get_recent_conversations(self, count: int = 10) -> List[TilotmaConversationEntry]:
        """Get recent conversation entries"""
        return self.conversation_history[-count:]
    
    def get_context_summary(self) -> str:
        """Get a summary of Tilotma's understanding for sharing"""
        if not self.project_understanding:
            return "No project understanding yet"
        
        return f"""
        Project Understanding (Confidence: {self.project_understanding.confidence_level:.0%}):
        {self.project_understanding.current_understanding}
        
        Missing Information: {', '.join(self.project_understanding.missing_information) if self.project_understanding.missing_information else 'None'}
        
        Quality Concerns: {', '.join(self.project_understanding.quality_concerns) if self.project_understanding.quality_concerns else 'None'}
        
        Interventions Made: {len(self.intervention_history)}
        """
