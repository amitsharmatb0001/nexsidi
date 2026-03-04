"""Team management routes.

All routes are tenant-scoped by organization_id.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import AdminContext, CurrentContext, TenantSession
from app.models.auth import User
from app.models.core import Team, TeamMember
from app.schemas.teams import (
    TeamCreate,
    TeamDetailResponse,
    TeamListResponse,
    TeamMemberAdd,
    TeamMemberResponse,
    TeamResponse,
)
from app.services.audit import log_action

logger = structlog.get_logger(__name__)

router = APIRouter()


async def _get_team_or_404(
    team_id: uuid.UUID,
    org_id: uuid.UUID,
    session: AsyncSession,
) -> Team:
    """Fetch a Team by id + org, raising 404 if not found."""
    result = await session.execute(
        select(Team).where(
            Team.id == team_id,
            Team.organization_id == org_id,
        )
    )
    team = result.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return team

@router.post("/", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    body: TeamCreate,
    ctx: AdminContext,  # C-5-FIX: Team creation is an admin operation.
    # Previously used CurrentContext — any member/developer could create teams.
    session: TenantSession,
) -> TeamResponse:
    """Create a new team in the current organization. Requires admin role.

    C-5-FIX: Changed from CurrentContext to AdminContext.
    M-7-FIX: Duplicate team name check added.
    """
    org_id = uuid.UUID(ctx.organization_id)

    # M-7-FIX: Enforce unique team names per organization at application layer
    existing_name = await session.execute(
        select(Team).where(
            Team.organization_id == org_id,
            Team.name == body.name,
        )
    )
    if existing_name.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A team with this name already exists in your organization",
        )

    team = Team(
        organization_id=org_id,
        name=body.name,
    )
    session.add(team)
    await session.flush()

    try:
        await log_action(
            session=session,
            action="team.create",
            entity_type="team",
            entity_id=team.id,
            user_id=uuid.UUID(ctx.user_id),
            organization_id=org_id,
            after_state={"name": body.name},
        )
    except Exception as audit_exc:
        logger.error("audit_log_failed", action="team.create", error=str(audit_exc)[:200])

    logger.info("team_created", team_id=str(team.id), name=body.name)
    return TeamResponse(
        id=team.id,
        organization_id=team.organization_id,
        name=team.name,
        created_at=team.created_at,
        member_count=0,
    )


@router.get("/", response_model=TeamListResponse)
async def list_teams(
    ctx: CurrentContext,
    session: TenantSession,
    limit: int = 50,   # H-8-FIX: Pagination — previously unbounded
    offset: int = 0,
) -> TeamListResponse:
    """List teams for the current organization with member counts.

    H-8-FIX: Added limit/offset pagination to prevent full-table scans on
    organizations with thousands of teams.
    """
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    org_id = uuid.UUID(ctx.organization_id)

    # Total count for pagination metadata
    total_result = await session.execute(
        select(func.count(Team.id)).where(Team.organization_id == org_id)
    )
    total = total_result.scalar_one()

    # Subquery: count members per team
    member_count_sq = (
        select(TeamMember.team_id, func.count(TeamMember.id).label("cnt"))
        .group_by(TeamMember.team_id)
        .subquery()
    )

    rows = await session.execute(
        select(Team, func.coalesce(member_count_sq.c.cnt, 0).label("member_count"))
        .outerjoin(member_count_sq, Team.id == member_count_sq.c.team_id)
        .where(Team.organization_id == org_id)
        .order_by(Team.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    teams = []
    for team, member_count in rows:
        teams.append(
            TeamResponse(
                id=team.id,
                organization_id=team.organization_id,
                name=team.name,
                created_at=team.created_at,
                member_count=member_count,
            )
        )

    return TeamListResponse(teams=teams, total=total)

@router.get("/{team_id}", response_model=TeamDetailResponse)
async def get_team(
    team_id: uuid.UUID,
    ctx: CurrentContext,
    session: TenantSession,
) -> TeamDetailResponse:
    """Get a single team with its full member list. Verifies org ownership."""
    org_id = uuid.UUID(ctx.organization_id)
    team = await _get_team_or_404(team_id, org_id, session)

    members_result = await session.execute(
        select(TeamMember)
        .where(TeamMember.team_id == team.id)
        # M-2-FIX: NULLS LAST so NULL joined_at rows sort to the end rather
        # than unpredictably. joined_at has server_default but may be NULL in
        # older rows or if the column is not yet migrated.
        .order_by(TeamMember.joined_at.asc().nulls_last())
    )
    members = [
        TeamMemberResponse.model_validate(m)
        for m in members_result.scalars().all()
    ]

    return TeamDetailResponse(
        id=team.id,
        organization_id=team.organization_id,
        name=team.name,
        created_at=team.created_at,
        member_count=len(members),
        members=members,
    )


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: uuid.UUID,
    ctx: AdminContext,
    session: TenantSession,
) -> Response:
    """Delete a team and all its memberships. Requires org_admin or super_admin."""
    org_id = uuid.UUID(ctx.organization_id)
    team = await _get_team_or_404(team_id, org_id, session)

    await session.delete(team)

    try:
        await log_action(
            session=session,
            action="team.delete",
            entity_type="team",
            entity_id=team_id,
            user_id=uuid.UUID(ctx.user_id),
            organization_id=org_id,
            before_state={"name": team.name},
        )
    except Exception as audit_exc:
        logger.error("audit_log_failed", action="team.delete", error=str(audit_exc)[:200])

    logger.info("team_deleted", team_id=str(team_id), org_id=str(org_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.post(
    "/{team_id}/members",
    response_model=TeamMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_team_member(
    team_id: uuid.UUID,
    body: TeamMemberAdd,
    ctx: AdminContext,
    session: TenantSession,
) -> TeamMemberResponse:
    """Add a user to a team. Requires org_admin or super_admin."""
    org_id = uuid.UUID(ctx.organization_id)
    # Verify the team belongs to this org
    await _get_team_or_404(team_id, org_id, session)

    # C-6-FIX: Verify the target user actually belongs to this organization.
    # Previously any user UUID from ANY organization could be added to a team.
    # This could create cross-tenant team membership and leak user existence.
    target_user_result = await session.execute(
        select(User).where(
            User.id == body.user_id,
            User.organization_id == org_id,
        )
    )
    if target_user_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in this organization",
        )

    # Check for duplicate membership
    existing = await session.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == body.user_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this team",
        )

    member = TeamMember(
        team_id=team_id,
        user_id=body.user_id,
        role=body.role,
    )
    session.add(member)
    await session.flush()

    try:
        await log_action(
            session=session,
            action="team.member.add",
            entity_type="team_member",
            entity_id=member.id,
            user_id=uuid.UUID(ctx.user_id),
            organization_id=org_id,
            after_state={
                "team_id": str(team_id),
                "user_id": str(body.user_id),
                "role": body.role,
            },
        )
    except Exception as audit_exc:
        logger.error("audit_log_failed", action="team.member.add", error=str(audit_exc)[:200])

    logger.info("team_member_added", team_id=str(team_id), user_id=str(body.user_id))
    return TeamMemberResponse.model_validate(member)


@router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_team_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    ctx: AdminContext,
    session: TenantSession,
) -> Response:
    """Remove a user from a team. Requires org_admin or super_admin."""
    org_id = uuid.UUID(ctx.organization_id)
    # Verify the team belongs to this org
    await _get_team_or_404(team_id, org_id, session)

    result = await session.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team member not found",
        )

    await session.delete(member)

    try:
        await log_action(
            session=session,
            action="team.member.remove",
            entity_type="team_member",
            entity_id=member.id,
            user_id=uuid.UUID(ctx.user_id),
            organization_id=org_id,
            before_state={
                "team_id": str(team_id),
                "user_id": str(user_id),
                "role": member.role,
            },
        )
    except Exception as audit_exc:
        logger.error("audit_log_failed", action="team.member.remove", error=str(audit_exc)[:200])

    logger.info("team_member_removed", team_id=str(team_id), user_id=str(user_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
