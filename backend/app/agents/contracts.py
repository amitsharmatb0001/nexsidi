"""
Data contracts between agents
Ensures type safety and clear expectations
"""
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional
from enum import Enum


class ProjectType(Enum):
    LANDING_PAGE = "landing_page"
    BLOG = "blog"
    ECOMMERCE = "ecommerce"
    SAAS = "saas"
    SOCIAL = "social"


@dataclass
class TilotmaOutput:
    """What Tilotma gives to Arjun"""
    project_id: str
    user_id: str
    project_type: ProjectType
    description: str
    key_features: List[str]
    tech_preferences: Optional[Dict] = None
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class SaanviOutput:
    """What Saanvi gives to Vikram/Shubham"""
    project_id: str
    requirements: Dict  # Structured requirements
    complexity_score: int  # 1-10
    estimated_cost: int  # In INR
    estimated_hours: int
    recommended_tech_stack: Dict
    database_requirements: List[str]
    api_endpoints_needed: List[str]
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class VikramOutput:
    """What Vikram gives to Vanya/Shubham (Architectural Blueprint)"""
    project_id: str
    blueprint: Dict
    tech_stack: Dict
    database_schema: Dict
    api_contracts: List[Dict]
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ShubhamInput:
    """What Shubham needs to generate backend"""
    project_id: str
    architecture: Dict  # From Saanvi/Vikram
    tech_stack: str  # "fastapi", "express-ts", etc.
    database_schema: Dict
    api_endpoints: List[Dict]
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ShubhamOutput:
    """What Shubham produces"""
    project_id: str
    files_written: bool  # Were they actually saved?
    workspace_path: str
    backend_url: Optional[str] = None
    api_architecture: Optional[Dict] = None
    files_generated: List[str] = None  # Optional list of paths
    backend_framework: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class AanyaInput:
    """What Aanya needs to generate frontend"""
    project_id: str
    api_base_url: str  # Backend URL
    api_endpoints: List[Dict]
    design_system: Dict
    tech_stack: str  # "react-vite-ts", "nextjs-ts", etc.
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class AanyaOutput:
    """What Aanya produces"""
    project_id: str
    files_written: bool
    workspace_path: str
    frontend_url: Optional[str] = None
    build_status: Optional[str] = None
    files_generated: List[str] = None
    frontend_framework: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return asdict(self)
