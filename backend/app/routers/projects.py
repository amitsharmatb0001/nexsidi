"""Project API routes: CRUD for projects.

All routes under /api/v1/projects/. Tenant-scoped via RLS.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select

from app.dependencies import AdminContext, CurrentContext, TenantSession
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

    # R33-FIX: Wrap log_action() in try/except so audit failures don't crash
    # the business operation. create_project shares a transaction with the
    # TenantSession — if log_action() raises (DB error, RLS policy violation),
    # the entire transaction rolls back and the project is NOT created.
    # Auth.py already uses this pattern for /logout and /logout-all.
    try:
        await log_action(
            session=session,
            action="project.create",
            entity_type="project",
            entity_id=project.id,
            user_id=uuid.UUID(ctx.user_id),
            organization_id=uuid.UUID(ctx.organization_id),
            after_state={"name": body.name},
        )
    except Exception as audit_exc:
        logger.error("audit_log_failed", action="project.create", error=str(audit_exc)[:200])

    logger.info("project_created", project_id=str(project.id), name=body.name)
    return ProjectResponse.model_validate(project)


@router.get("/", response_model=ProjectListResponse)
async def list_projects(
    ctx: CurrentContext,
    session: TenantSession,
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    limit: int = Query(default=50, ge=1, le=100, description="Items per page"),
) -> ProjectListResponse:
    """List projects for the current organization (paginated)."""
    # M11-FIX: Filter out archived (soft-deleted) projects
    # R31-FIX-IDOR: Defense-in-depth — filter by organization_id at application
    # level, not just RLS. Without this, a superuser DB connection returns ALL
    # orgs' projects in the list.
    org_id = uuid.UUID(ctx.organization_id)
    active_filter = (Project.status != "archived",)
    org_filter = (Project.organization_id == org_id,)

    total_q = await session.execute(
        select(func.count()).select_from(Project).where(*active_filter, *org_filter)
    )
    total = total_q.scalar_one()

    result = await session.execute(
        select(Project)
        .where(*active_filter, *org_filter)
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
    # R27-FIX-7: Exclude archived projects — soft-deleted projects should not
    # be accessible via direct ID access (only list_projects filtered them).
    # R31-FIX-IDOR: Defense-in-depth — filter by organization_id at application
    # level, not just RLS. If the DB connection is a superuser (RLS bypassed),
    # the WHERE clause still enforces tenant isolation.
    result = await session.execute(
        select(Project).where(
            Project.id == project_id,
            Project.organization_id == uuid.UUID(ctx.organization_id),
            Project.status != "archived",
        )
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
    """Update a project's name or description.

    R-07-FIX: Only project owner or org admin can update projects.
    Viewers and regular members get 403.
    """
    # R27-FIX-7: Exclude archived projects from update
    # R31-FIX-IDOR: Defense-in-depth — filter by organization_id at application level.
    result = await session.execute(
        select(Project).where(
            Project.id == project_id,
            Project.organization_id == uuid.UUID(ctx.organization_id),
            Project.status != "archived",
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # R-07-FIX: RBAC — only project owner or org admin/super_admin can update
    is_owner = str(project.owner_id) == ctx.user_id
    is_admin = ctx.role in ("org_admin", "super_admin")
    if not (is_owner or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the project owner or an admin can update this project",
        )

    before = {"name": project.name, "description": project.description}

    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description

    # R33-FIX: Same pattern as create_project — don't let audit failure roll back
    # the project update. The update already happened in memory; if the transaction
    # commits, the update persists even if the audit log entry is lost.
    try:
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
    except Exception as audit_exc:
        logger.error("audit_log_failed", action="project.update", error=str(audit_exc)[:200])

    return ProjectResponse.model_validate(project)


@router.delete("/{project_id}")
async def delete_project(
    project_id: uuid.UUID,
    ctx: AdminContext,
    session: TenantSession,
) -> Response:
    """Soft-delete a project (set status to 'archived').

    VULN-1-FIX: Requires org_admin or super_admin role.
    """
    # R29-FIX-12: Exclude already-archived projects — prevents duplicate audit
    # entries and information leakage (confirming archived project existence).
    # R31-FIX-IDOR: Defense-in-depth — filter by organization_id at application level.
    result = await session.execute(
        select(Project).where(
            Project.id == project_id,
            Project.organization_id == uuid.UUID(ctx.organization_id),
            Project.status != "archived",
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    project.status = "archived"

    # R33-FIX: Same pattern — don't let audit failure prevent the archive.
    try:
        await log_action(
            session=session,
            action="project.archive",
            entity_type="project",
            entity_id=project.id,
            user_id=uuid.UUID(ctx.user_id),
            organization_id=uuid.UUID(ctx.organization_id),
        )
    except Exception as audit_exc:
        logger.error("audit_log_failed", action="project.archive", error=str(audit_exc)[:200])

    return Response(status_code=status.HTTP_204_NO_CONTENT)
