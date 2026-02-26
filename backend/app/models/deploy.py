"""Deploy schema models: deployments.

Tables: deploy.deployments
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, UUIDPrimaryKeyMixin


class Deployment(Base, UUIDPrimaryKeyMixin, TenantMixin):
    __tablename__ = "deployments"
    __table_args__ = (
        Index("ix_deploy_deployments_project_id", "project_id"),
        Index("ix_deploy_deployments_organization_id", "organization_id"),
        Index("ix_deploy_deployments_status", "status"),
        {"schema": "deploy"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("core.projects.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    auth_method: Mapped[str] = mapped_column(String(20), default="oauth", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    deployment_url: Mapped[str | None] = mapped_column(Text, default=None)
    deploy_log: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
