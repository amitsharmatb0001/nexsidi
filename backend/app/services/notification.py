"""Notification Service: sends notifications via web + email channels.

Phase 1 channels:
- Web (in-app): stored in notify.notifications table, delivered via WebSocket
- Email: queued via SMTP (async, non-blocking)

Phase 2 (behind feature flag):
- WhatsApp Business API

Design:
- All notifications flow through this service
- Channel selection is per-user preference (stored in auth.users)
- Templates are predefined per notification type
- Rate limiting: max 100 notifications per user per hour
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class NotificationChannel(str, Enum):
    WEB = "web"
    EMAIL = "email"


class NotificationType(str, Enum):
    """Predefined notification types with templates."""

    PIPELINE_STARTED = "pipeline.started"
    PIPELINE_COMPLETED = "pipeline.completed"
    PIPELINE_FAILED = "pipeline.failed"

    CHECKPOINT_READY = "checkpoint.ready"
    CHECKPOINT_APPROVED = "checkpoint.approved"
    CHECKPOINT_REJECTED = "checkpoint.rejected"

    DEPLOY_STARTED = "deploy.started"
    DEPLOY_COMPLETED = "deploy.completed"
    DEPLOY_FAILED = "deploy.failed"

    DELIVERY_READY = "delivery.ready"

    SECURITY_ALERT = "security.alert"

    TEAM_INVITE = "team.invite"
    TEAM_MEMBER_JOINED = "team.member_joined"

    SYSTEM_MAINTENANCE = "system.maintenance"


# Notification templates: type -> (title_template, body_template)
NOTIFICATION_TEMPLATES: dict[NotificationType, tuple[str, str]] = {
    NotificationType.PIPELINE_STARTED: (
        "Pipeline started: {project_name}",
        "Your project '{project_name}' has entered the build pipeline.",
    ),
    NotificationType.PIPELINE_COMPLETED: (
        "Pipeline complete: {project_name}",
        "Your project '{project_name}' has been built and is ready for review.",
    ),
    NotificationType.PIPELINE_FAILED: (
        "Pipeline failed: {project_name}",
        "Your project '{project_name}' build encountered errors. Check the pipeline dashboard for details.",
    ),
    NotificationType.CHECKPOINT_READY: (
        "Checkpoint ready: {project_name}",
        "Your project '{project_name}' is waiting for your approval at {checkpoint_name}.",
    ),
    NotificationType.CHECKPOINT_APPROVED: (
        "Checkpoint approved: {project_name}",
        "Checkpoint '{checkpoint_name}' has been approved. Pipeline is continuing.",
    ),
    NotificationType.CHECKPOINT_REJECTED: (
        "Checkpoint rejected: {project_name}",
        "Checkpoint '{checkpoint_name}' was rejected. Pipeline has been paused.",
    ),
    NotificationType.DEPLOY_STARTED: (
        "Deploying: {project_name}",
        "Your project '{project_name}' is being deployed to {provider}.",
    ),
    NotificationType.DEPLOY_COMPLETED: (
        "Deployed: {project_name}",
        "Your project '{project_name}' is live at {deployment_url}.",
    ),
    NotificationType.DEPLOY_FAILED: (
        "Deploy failed: {project_name}",
        "Deployment of '{project_name}' failed. Check deployment logs for details.",
    ),
    NotificationType.DELIVERY_READY: (
        "Project ready: {project_name}",
        "Your project '{project_name}' delivery package is ready for download.",
    ),
    NotificationType.SECURITY_ALERT: (
        "Security alert: {project_name}",
        "Security issues found in '{project_name}': {alert_summary}.",
    ),
    NotificationType.TEAM_INVITE: (
        "Team invitation",
        "You've been invited to join the team '{team_name}' at {organization_name}.",
    ),
    NotificationType.TEAM_MEMBER_JOINED: (
        "New team member",
        "{member_name} has joined the team '{team_name}'.",
    ),
    NotificationType.SYSTEM_MAINTENANCE: (
        "System maintenance",
        "NexSidi will undergo maintenance: {maintenance_details}.",
    ),
}


@dataclass(slots=True)
class NotificationPayload:
    """A notification to be sent."""

    notification_type: NotificationType
    user_id: str
    organization_id: str
    channel: NotificationChannel = NotificationChannel.WEB
    title: str = ""
    body: str = ""
    link: str = ""
    data: dict[str, Any] | None = None
    created_at: str = ""
    # R36-FIX: Track read/unread status. Previously missing — notification_count()
    # returned total notifications and there was no way to count unread.
    # The DB model (notify.notifications) has a `read` column with a partial
    # index on unread notifications, but the in-memory payload had no field.
    is_read: bool = False
    notification_id: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.notification_id:
            self.notification_id = str(_uuid.uuid4())


class NotificationService:
    """Sends and manages notifications across channels.

    In production, web notifications are stored in DB + pushed via WebSocket.
    Email notifications are queued via SMTP.
    """

    # MEM-FIX: Bound in-memory notification storage to prevent OOM.
    _MAX_SENT: int = 10_000
    _MAX_RATE_KEYS: int = 5_000

    def __init__(self) -> None:
        from collections import deque

        self._sent: deque[NotificationPayload] = deque(maxlen=self._MAX_SENT)
        # M7-FIX: Use sliding window with timestamps instead of simple counter
        self._rate_windows: dict[str, list[float]] = {}  # user_id -> [timestamps]
        self._max_per_hour: int = 100
        self._rate_check_count: int = 0

    async def send(
        self,
        notification_type: NotificationType,
        user_id: str,
        organization_id: str,
        channel: NotificationChannel = NotificationChannel.WEB,
        link: str = "",
        data: dict[str, Any] | None = None,
        **template_vars: str,
    ) -> NotificationPayload:
        """Send a notification.

        Args:
            notification_type: Which template to use.
            user_id: Target user UUID.
            organization_id: User's org UUID.
            channel: Delivery channel (web or email).
            link: Optional link to include.
            data: Optional extra data.
            **template_vars: Variables to fill in the template.

        Returns:
            The sent NotificationPayload.

        Raises:
            ValueError: If rate limit exceeded.
        """
        # RATE-TOCTOU-FIX: Atomically check and increment rate limit.
        # Previously, _check_rate_limit() and _increment_rate() were separate,
        # allowing concurrent coroutines to both pass the check.
        if not self._check_and_increment_rate(user_id):
            raise ValueError(f"Rate limit exceeded for user {user_id}")

        # Render template
        title_tpl, body_tpl = NOTIFICATION_TEMPLATES.get(
            notification_type,
            ("{notification_type}", "Notification: {notification_type}"),
        )
        template_vars["notification_type"] = notification_type.value
        title = _safe_format(title_tpl, template_vars)
        body = _safe_format(body_tpl, template_vars)

        payload = NotificationPayload(
            notification_type=notification_type,
            user_id=user_id,
            organization_id=organization_id,
            channel=channel,
            title=title,
            body=body,
            link=link,
            data=data,
        )

        # Route to channel
        if channel == NotificationChannel.WEB:
            await self._send_web(payload)
        elif channel == NotificationChannel.EMAIL:
            await self._send_email(payload)

        self._sent.append(payload)
        # Rate already incremented atomically in _check_and_increment_rate()

        logger.info(
            "notification_sent",
            notification_type=notification_type.value,
            channel=channel.value,
            user_id=user_id,
        )

        return payload

    async def send_to_project_users(
        self,
        notification_type: NotificationType,
        user_ids: list[str],
        organization_id: str,
        **template_vars: str,
    ) -> list[NotificationPayload]:
        """Send a notification to all users associated with a project."""
        results = []
        for uid in user_ids:
            try:
                payload = await self.send(
                    notification_type=notification_type,
                    user_id=uid,
                    organization_id=organization_id,
                    **template_vars,
                )
                results.append(payload)
            except ValueError:
                logger.warning("notification_rate_limited", user_id=uid)
        return results

    def get_user_notifications(
        self,
        user_id: str,
        limit: int = 50,
        unread_only: bool = False,
        organization_id: str | None = None,
    ) -> list[NotificationPayload]:
        """Get notifications for a user (most recent first).

        R8-FIX: Added organization_id filter for defense-in-depth tenant
        isolation. While user_ids are globally unique UUIDs, filtering by
        org_id prevents cross-tenant data leakage if a user_id collision
        or misconfiguration occurs.
        """
        user_notifs = [
            n for n in self._sent
            if n.user_id == user_id
            and (organization_id is None or n.organization_id == organization_id)
            # R36-FIX: Actually filter by unread status. Previously, the
            # unread_only parameter was accepted but never used in the filter.
            and (not unread_only or not n.is_read)
        ]
        user_notifs.sort(key=lambda n: n.created_at, reverse=True)
        return user_notifs[:limit]

    def mark_as_read(
        self,
        user_id: str,
        notification_ids: list[str] | None = None,
        organization_id: str | None = None,
    ) -> int:
        """R36-FIX: Mark notifications as read.

        If notification_ids is None, marks ALL notifications for the user as read.
        Returns the count of notifications marked as read.
        """
        count = 0
        for n in self._sent:
            if n.user_id != user_id:
                continue
            if organization_id is not None and n.organization_id != organization_id:
                continue
            if n.is_read:
                continue
            # If specific IDs provided, only mark those
            if notification_ids is not None:
                # Use created_at as a proxy ID since NotificationPayload has no id field
                if n.notification_id not in notification_ids:
                    continue
            n.is_read = True
            count += 1
        return count

    def notification_count(
        self, user_id: str, organization_id: str | None = None,
    ) -> int:
        """Total notifications for a user."""
        return sum(
            1 for n in self._sent
            if n.user_id == user_id
            and (organization_id is None or n.organization_id == organization_id)
        )

    def unread_count(
        self, user_id: str, organization_id: str | None = None,
    ) -> int:
        """R36-FIX: Count of unread notifications for a user.

        Previously missing — notification_count() returned total but there
        was no way to get unread count. The API returned unread=total always.
        """
        return sum(
            1 for n in self._sent
            if n.user_id == user_id
            and not n.is_read
            and (organization_id is None or n.organization_id == organization_id)
        )

    # ── Channel Handlers ──────────────────────────────────────────

    async def _send_web(self, payload: NotificationPayload) -> None:
        """Store web notification (in production: DB + WebSocket push)."""
        logger.debug(
            "web_notification",
            title=payload.title,
            user_id=payload.user_id,
        )

    async def _send_email(self, payload: NotificationPayload) -> None:
        """Queue email notification (in production: SMTP via async worker)."""
        logger.debug(
            "email_notification_queued",
            title=payload.title,
            user_id=payload.user_id,
        )

    # ── Rate Limiting ─────────────────────────────────────────────

    def _check_and_increment_rate(self, user_id: str) -> bool:
        """Atomically check and increment rate limit (100/hour).

        RATE-TOCTOU-FIX: Combines check + increment into a single operation
        to prevent concurrent coroutines from both passing the check.
        M7-FIX: Uses a sliding window with timestamp pruning.
        MEM-FIX: Periodic key eviction to prevent unbounded dict growth.
        """
        import time

        now = time.monotonic()

        # MEM-FIX: Periodically evict stale rate window keys
        self._rate_check_count += 1
        if self._rate_check_count >= 500:
            self._evict_stale_rate_keys(now)
            self._rate_check_count = 0

        window = self._rate_windows.get(user_id, [])
        # Prune entries older than 1 hour
        window = [t for t in window if now - t < 3600]

        if len(window) >= self._max_per_hour:
            self._rate_windows[user_id] = window
            return False

        # Atomically increment after passing check
        window.append(now)
        self._rate_windows[user_id] = window
        return True

    # R35-FIX: Removed dead code `_check_rate_limit()`. This was the pre-TOCTOU-FIX
    # version replaced by `_check_and_increment_rate()`. Despite the docstring
    # claiming "Read-only check", it mutated `self._rate_windows[user_id]`. Keeping
    # dead code with misleading docs invites misuse if a future dev calls it
    # thinking it's a safe read-only probe.

    def _evict_stale_rate_keys(self, now: float) -> None:
        """Remove rate window entries for users with no recent activity.

        MEM-FIX: Prevents unbounded growth of _rate_windows dict.
        """
        stale = [
            uid for uid, ts in self._rate_windows.items()
            if not ts or (now - ts[-1]) >= 3600
        ]
        for uid in stale:
            del self._rate_windows[uid]

        # Hard cap: evict oldest if still over limit
        if len(self._rate_windows) > self._MAX_RATE_KEYS:
            sorted_keys = sorted(
                self._rate_windows,
                key=lambda k: self._rate_windows[k][-1] if self._rate_windows[k] else 0,
            )
            for k in sorted_keys[: len(self._rate_windows) - self._MAX_RATE_KEYS]:
                del self._rate_windows[k]


def _safe_format(template: str, vars: dict[str, str]) -> str:
    """Format a template string, leaving unknown vars as-is.

    R8-FIX: Uses regex-based substitution exclusively instead of
    str.format(). Python's str.format() allows attribute access and
    indexing (e.g. {0.__class__.__init__.__globals__}) which could
    leak internal state if template_vars contain user-controlled values
    like project_name.

    R9-FIX: HTML-escapes variable values for defense-in-depth against
    stored XSS. User-controlled strings like project_name may contain
    <script> tags that render in browser notification UIs.
    """
    import html
    import re

    def replacer(m: re.Match) -> str:
        key = m.group(1)
        value = vars.get(key, m.group(0))
        # R37-FIX: Strip newlines to prevent log injection.
        value = str(value).replace("\n", " ").replace("\r", " ")
        return html.escape(value)
    return re.sub(r"\{(\w+)\}", replacer, template)


# ── Singleton ───────────────────────────────────────────────────

import threading as _threading
_notification_lock = _threading.Lock()
_service: NotificationService | None = None


def get_notification_service() -> NotificationService:
    """Get or create the notification service singleton."""
    global _service
    if _service is not None:
        return _service
    with _notification_lock:
        if _service is None:
            _service = NotificationService()
        return _service
