"""API key management routes.

All routes are tenant-scoped by organization_id.
The plaintext key is returned ONCE on creation and never stored.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.dependencies import AdminContext, CurrentContext, TenantSession
from app.models.billing import ApiKey
from app.schemas.api_keys import (
    ApiKeyCreate,
    ApiKeyCreateResponse,
    ApiKeyResponse,
    ApiKeyUpdate,
)
from app.services.audit import log_action

logger = structlog.get_logger(__name__)

router = APIRouter()


def _key_response(key: ApiKey) -> ApiKeyResponse:
    """Build an ApiKeyResponse from an ApiKey ORM object (never exposes key_hash)."""
    return ApiKeyResponse(
        id=key.id,
        name=key.name,
        key_prefix=key.key_prefix,
        scope=key.scope,
        rate_limit=key.rate_limit,
        last_used_at=key.last_used_at,
        expires_at=key.expires_at,
        is_active=key.is_active,
        created_at=key.created_at,
    )


@router.post(
    "/",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    body: ApiKeyCreate,
    ctx: CurrentContext,
    session: TenantSession,
) -> ApiKeyCreateResponse:
    """Create a new API key for the current organization.

    The full key is returned ONCE in this response.
    Only the SHA-256 hash is stored; the plaintext key cannot be recovered.
    """
    org_id = uuid.UUID(ctx.organization_id)
    user_id = uuid.UUID(ctx.user_id)

    # Generate a cryptographically secure 32-byte key
    raw_key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:8]

    expires_at: datetime | None = None
    if body.expires_in_days is not None:
        expires_at = datetime.now(tz=timezone.utc) + timedelta(days=body.expires_in_days)

    api_key = ApiKey(
        organization_id=org_id,
        user_id=user_id,
        name=body.name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        scope=body.scope,
        rate_limit=body.rate_limit,
        expires_at=expires_at,
        is_active=True,
    )
    session.add(api_key)
    await session.flush()

    try:
        await log_action(
            session=session,
            action="api_key.create",
            entity_type="api_key",
            entity_id=api_key.id,
            user_id=user_id,
            organization_id=org_id,
            after_state={"name": body.name, "scope": body.scope, "prefix": key_prefix},
        )
    except Exception as audit_exc:
        logger.error("audit_log_failed", action="api_key.create", error=str(audit_exc)[:200])

    logger.info("api_key_created", key_prefix=key_prefix, org_id=str(org_id))
    return ApiKeyCreateResponse(
        **_key_response(api_key).model_dump(),
        key=raw_key,
    )


@router.get("/", response_model=list[ApiKeyResponse])
async def list_api_keys(
    ctx: CurrentContext,
    session: TenantSession,
) -> list[ApiKeyResponse]:
    """List all active API keys for the current organization.

    Never returns key_hash -- only safe metadata (prefix, name, scope, etc.).
    """
    org_id = uuid.UUID(ctx.organization_id)
    result = await session.execute(
        select(ApiKey)
        .where(
            ApiKey.organization_id == org_id,
            ApiKey.is_active.is_(True),
        )
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()
    return [_key_response(k) for k in keys]


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def revoke_api_key(
    key_id: uuid.UUID,
    ctx: AdminContext,
    session: TenantSession,
) -> None:
    """Revoke (soft-delete) an API key. Requires org_admin or super_admin."""
    org_id = uuid.UUID(ctx.organization_id)
    result = await session.execute(
        select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.organization_id == org_id,
            ApiKey.is_active.is_(True),
        )
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    api_key.is_active = False

    try:
        await log_action(
            session=session,
            action="api_key.revoke",
            entity_type="api_key",
            entity_id=api_key.id,
            user_id=uuid.UUID(ctx.user_id),
            organization_id=org_id,
            before_state={"name": api_key.name, "is_active": True},
        )
    except Exception as audit_exc:
        logger.error("audit_log_failed", action="api_key.revoke", error=str(audit_exc)[:200])

    logger.info("api_key_revoked", key_id=str(key_id), org_id=str(org_id))


@router.patch("/{key_id}", response_model=ApiKeyResponse)
async def update_api_key(
    key_id: uuid.UUID,
    body: ApiKeyUpdate,
    ctx: AdminContext,
    session: TenantSession,
) -> ApiKeyResponse:
    """Update API key metadata (name, scope, rate_limit, expiry). Requires admin."""
    org_id = uuid.UUID(ctx.organization_id)
    result = await session.execute(
        select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.organization_id == org_id,
            ApiKey.is_active.is_(True),
        )
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    before = {
        "name": api_key.name,
        "scope": api_key.scope,
        "rate_limit": api_key.rate_limit,
        "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
    }

    if body.name is not None:
        api_key.name = body.name
    if body.scope is not None:
        api_key.scope = body.scope
    if body.rate_limit is not None:
        api_key.rate_limit = body.rate_limit
    if body.expires_in_days is not None:
        api_key.expires_at = datetime.now(tz=timezone.utc) + timedelta(days=body.expires_in_days)

    try:
        await log_action(
            session=session,
            action="api_key.update",
            entity_type="api_key",
            entity_id=api_key.id,
            user_id=uuid.UUID(ctx.user_id),
            organization_id=org_id,
            before_state=before,
            after_state={
                "name": api_key.name,
                "scope": api_key.scope,
                "rate_limit": api_key.rate_limit,
                "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
            },
        )
    except Exception as audit_exc:
        logger.error("audit_log_failed", action="api_key.update", error=str(audit_exc)[:200])

    logger.info("api_key_updated", key_id=str(key_id), org_id=str(org_id))
    return _key_response(api_key)
