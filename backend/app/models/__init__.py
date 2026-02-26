"""SQLAlchemy models — all models imported here for Alembic discovery."""

from app.models.base import Base  # noqa: F401

# Import all models so Alembic's autogenerate can discover them
from app.models.auth import FeatureFlag, User, WhatsAppAccount  # noqa: F401
from app.models.audit import AuditLog, ConsentRecord, SecurityEvent  # noqa: F401
from app.models.billing import ApiKey, BillingRecord, TokenUsage  # noqa: F401
from app.models.chat import ChatMessage, ChatSession  # noqa: F401
from app.models.core import (  # noqa: F401
    FileUpload,
    Organization,
    Project,
    ProjectFile,
    ProjectMember,
    Team,
    TeamMember,
)
from app.models.deploy import Deployment  # noqa: F401
from app.models.notify import Notification  # noqa: F401
from app.models.pipeline import Checkpoint, FixerIteration, PipelineRun, PipelineStep  # noqa: F401
