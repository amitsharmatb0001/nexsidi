"""SQLAlchemy base class and reusable mixins.

Every table uses UUID primary keys, created_at/updated_at timestamps.
Tenant-scoped tables include organization_id for 1-hop RLS.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


# V5-FIX (MEDIUM-3): Module-level functions instead of lambdas for
# SQLAlchemy default/onupdate.  Lambdas can't be pickled (breaks
# multiprocessing-based test runners and alembic autogenerate) and
# cause opaque ``<lambda>`` in tracebacks.  Named functions are
# equivalent but debuggable and serializable.
def _utcnow() -> datetime:
    """Return timezone-aware UTC now (used by column defaults)."""
    return datetime.now(timezone.utc)


class UUIDPrimaryKeyMixin:
    """UUID primary key with server-side default."""

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )


class TimestampMixin:
    """created_at and updated_at timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=func.now(),
        onupdate=_utcnow,
        nullable=False,
    )


class TenantMixin:
    """organization_id for direct 1-hop RLS. Every tenant-scoped table must use this.

    NOTE: No index=True here. Each model adds its own explicit Index in __table_args__
    to avoid duplicate indexes when the column is overridden with ForeignKey.
    """

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
    )
