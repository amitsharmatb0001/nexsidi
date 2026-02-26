"""Core schema models: organizations, teams, projects, files.

Tables: core.organizations, core.teams, core.team_members,
        core.projects, core.project_members, core.project_files, core.file_uploads
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organizations"
    __table_args__ = {"schema": "core"}

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(20), default="free", nullable=False)
    settings: Mapped[dict | None] = mapped_column(JSONB, default=None)

    # Relationships
    teams: Mapped[list[Team]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    projects: Mapped[list[Project]] = relationship(back_populates="organization", cascade="all, delete-orphan")


class Team(Base, UUIDPrimaryKeyMixin, TenantMixin):
    __tablename__ = "teams"
    __table_args__ = (
        Index("ix_teams_organization_id", "organization_id"),
        {"schema": "core"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("core.organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)

    # Relationships
    organization: Mapped[Organization] = relationship(back_populates="teams")
    members: Mapped[list[TeamMember]] = relationship(back_populates="team", cascade="all, delete-orphan")


class TeamMember(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "team_members"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
        Index("ix_team_members_user_id", "user_id"),
        {"schema": "core"},
    )

    team_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("core.teams.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), default="member", nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)

    # Relationships
    team: Mapped[Team] = relationship(back_populates="members")


class Project(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_organization_id", "organization_id"),
        Index("ix_projects_owner_id", "owner_id"),
        Index("ix_projects_status", "status"),
        {"schema": "core"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("core.organizations.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("core.teams.id", ondelete="SET NULL"), default=None
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    input_source: Mapped[str] = mapped_column(String(20), default="web_chat", nullable=False)
    architecture_contract: Mapped[dict | None] = mapped_column(JSONB, default=None)
    complexity_score: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    ai_model_tier: Mapped[str | None] = mapped_column(String(20), default=None)
    execution_mode: Mapped[str] = mapped_column(String(20), default="checkpoint", nullable=False)
    settings: Mapped[dict | None] = mapped_column(JSONB, default=None)

    # Relationships
    organization: Mapped[Organization] = relationship(back_populates="projects")
    members: Mapped[list[ProjectMember]] = relationship(back_populates="project", cascade="all, delete-orphan")
    files: Mapped[list[ProjectFile]] = relationship(back_populates="project", cascade="all, delete-orphan")
    uploads: Mapped[list[FileUpload]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectMember(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
        Index("ix_project_members_project_id", "project_id"),
        Index("ix_project_members_user_id", "user_id"),
        Index("ix_project_members_role", "role"),
        {"schema": "core"},
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("core.projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), default="editor", nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("auth.users.id", ondelete="SET NULL"), default=None
    )
    invitation_status: Mapped[str] = mapped_column(String(20), default="accepted", nullable=False)
    invited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Relationships
    project: Mapped[Project] = relationship(back_populates="members")


class ProjectFile(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "project_files"
    __table_args__ = (
        UniqueConstraint("project_id", "file_path", name="uq_project_files_project_path"),
        Index("ix_project_files_project_id", "project_id"),
        Index("ix_project_files_organization_id", "organization_id"),
        {"schema": "core"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("core.projects.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, default=None)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    generated_by: Mapped[str | None] = mapped_column(String(30), default=None)
    version: Mapped[int] = mapped_column(default=1, nullable=False)

    # Relationships
    project: Mapped[Project] = relationship(back_populates="files")


class FileUpload(Base, UUIDPrimaryKeyMixin, TenantMixin):
    __tablename__ = "file_uploads"
    __table_args__ = (
        Index("ix_file_uploads_project_id", "project_id"),
        Index("ix_file_uploads_organization_id", "organization_id"),
        {"schema": "core"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("core.projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=False
    )
    original_name: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    scan_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)

    # Relationships
    project: Mapped[Project] = relationship(back_populates="uploads")
