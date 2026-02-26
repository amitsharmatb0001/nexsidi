"""Project API routes: CRUD for projects.

All routes under /api/v1/projects/. Tenant-scoped via RLS.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select

from app.dependencies import CurrentContext, TenantSession
from app.models.core import Project
from app.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)
from app.services.audit import log_action

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.post(
    "/",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    body: ProjectCreate,
    ctx: CurrentContext,
    session: TenantSession,
) -> ProjectResponse:
    """Create a new project in the current organization."""
    project = Project(
        organization_id=uuid.UUID(ctx.organization_id),
        owner_id=uuid.UUID(ctx.user_id),
        name=body.name,
        description=body.description,
        status="active",
        settings={"tech_stack": body.tech_stack} if body.tech_stack else None,
    )
    session.add(project)
    await session.flush()

    await log_action(
        session=session,
        action="project.create",
        entity_type="project",
        entity_id=project.id,
        user_id=uuid.UUID(ctx.user_id),
        organization_id=uuid.UUID(ctx.organization_id),
        after_state={"name": body.name},
    )

    logger.info("project_created", project_id=str(project.id), name=body.name)
    return ProjectResponse.model_validate(project)


@router.get("/", response_model=ProjectListResponse)
async def list_projects(
    ctx: CurrentContext,
    session: TenantSession,
    offset: int = 0,
    limit: int = 50,
) -> ProjectListResponse:
    """List projects for the current organization (paginated)."""
    total_q = await session.execute(
        select(func.count()).select_from(Project)
    )
    total = total_q.scalar_one()

    result = await session.execute(
        select(Project)
        .order_by(Project.created_at.desc())
        .offset(offset)
        .limit(min(limit, 100))
    )
    projects = [ProjectResponse.model_validate(p) for p in result.scalars().all()]

    return ProjectListResponse(projects=projects, total=total)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: uuid.UUID,
    ctx: CurrentContext,
    session: TenantSession,
) -> ProjectResponse:
    """Get a single project by ID."""
    result = await session.execute(
        select(Project).where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return ProjectResponse.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    ctx: CurrentContext,
    session: TenantSession,
) -> ProjectResponse:
    """Update a project's name or description."""
    result = await session.execute(
        select(Project).where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    before = {"name": project.name, "description": project.description}

    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description

    await log_action(
        session=session,
        action="project.update",
        entity_type="project",
        entity_id=project.id,
        user_id=uuid.UUID(ctx.user_id),
        organization_id=uuid.UUID(ctx.organization_id),
        before_state=before,
        after_state={"name": project.name, "description": project.description},
    )

    return ProjectResponse.model_validate(project)


@router.delete("/{project_id}")
async def delete_project(
    project_id: uuid.UUID,
    ctx: CurrentContext,
    session: TenantSession,
) -> Response:
    """Soft-delete a project (set status to 'archived')."""
    result = await session.execute(
        select(Project).where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    project.status = "archived"

    await log_action(
        session=session,
        action="project.archive",
        entity_type="project",
        entity_id=project.id,
        user_id=uuid.UUID(ctx.user_id),
        organization_id=uuid.UUID(ctx.organization_id),
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
