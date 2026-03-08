"""DevBox Manager — bridges billing payments to cloud DevBox provisioning.

Phase 2A: When a user's payment clears (Razorpay webhook), this service
provisions a dedicated cloud DevBox on GKE Autopilot. Each DevBox is a
multi-container pod (backend + frontend + DB) scoped to the user's billing plan.

Responsibilities:
- Provision DevBox on payment clearance
- Track DevBox state in database (crash recovery)
- Enforce per-user limits (max 1 DevBox per user by default)
- Handle TTL extension and cleanup
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class DevBoxRecord:
    """Database-compatible DevBox record."""

    id: str
    user_id: str
    org_id: str | None
    plan: str
    namespace: str
    deployment_name: str
    service_url: str
    status: str
    created_at: str
    expires_at: str


class DevBoxManager:
    """Payment-to-DevBox provisioning bridge.

    Usage::

        manager = DevBoxManager()
        devbox_id = await manager.provision_on_payment(
            billing_record_id="bill_abc123",
            user_id="user_xyz",
            org_id="org_456",
            plan="pro",
        )

        state = await manager.get_user_devbox(user_id="user_xyz")
        await manager.extend_devbox(devbox_id, hours=12)
        await manager.deprovision_devbox(devbox_id)
    """

    def __init__(self) -> None:
        self._executor: Any = None  # Lazy-init DevBoxExecutor
        # In-memory state (backed by DB for crash recovery)
        self._records: dict[str, DevBoxRecord] = {}

    def _get_executor(self) -> Any:
        """Lazy-initialize the DevBoxExecutor."""
        if self._executor is None:
            from app.engine.k8s_executor import get_devbox_executor, DevBoxConfig
            from app.config import get_settings
            settings = get_settings()
            config = DevBoxConfig(
                gcp_project_id=settings.gcp_project_id or "nexsidi-ai",
                gke_cluster_name=settings.gke_cluster_name,
                gke_cluster_zone=settings.gke_cluster_zone,
                artifact_registry=settings.devbox_artifact_registry,
                default_ttl_hours=settings.devbox_default_ttl_hours,
                max_per_user=settings.devbox_max_per_user,
            )
            self._executor = get_devbox_executor(devbox_config=config)
        return self._executor

    async def provision_on_payment(
        self,
        billing_record_id: str,
        user_id: str,
        org_id: str | None = None,
        plan: str = "starter",
        project_files: dict[str, str] | None = None,
    ) -> str | None:
        """Provision a DevBox when payment clears.

        Called by the Razorpay webhook handler after payment.captured.

        Args:
            billing_record_id: The billing record / order ID.
            user_id: User who paid.
            org_id: Optional organization ID.
            plan: Billing plan (starter/pro/enterprise).
            project_files: Generated project files to deploy.

        Returns:
            DevBox ID if provisioned, None if failed or limit reached.
        """
        from app.config import get_settings
        settings = get_settings()

        if not settings.devbox_enabled:
            logger.info("devbox_disabled", user_id=user_id)
            return None

        # Check per-user limit
        existing = await self.get_user_devbox(user_id)
        if existing and existing.status in ("provisioning", "running"):
            logger.warning(
                "devbox_limit_reached",
                user_id=user_id,
                existing_id=existing.id,
                max_per_user=settings.devbox_max_per_user,
            )
            return None

        try:
            executor = self._get_executor()
            state = await executor.create_devbox(
                user_id=user_id,
                plan=plan,
                project_files=project_files or {},
            )

            if state.status == "failed":
                logger.error(
                    "devbox_provision_failed",
                    user_id=user_id,
                    billing_record_id=billing_record_id,
                )
                return None

            # Store record
            record = DevBoxRecord(
                id=state.devbox_id,
                user_id=user_id,
                org_id=org_id,
                plan=plan,
                namespace=state.namespace,
                deployment_name=state.deployment_name,
                service_url=state.service_url,
                status=state.status,
                created_at=state.created_at,
                expires_at=state.expires_at,
            )
            self._records[state.devbox_id] = record

            # Persist to Valkey for crash recovery (DB persistence deferred to DB migration)
            try:
                from app.services.valkey_pool import get_valkey_client
                _vk = get_valkey_client()
                if _vk:
                    import json as _json
                    await _vk.set(
                        f"devbox:record:{state.devbox_id}",
                        _json.dumps({
                            "id": record.id, "user_id": record.user_id,
                            "org_id": record.org_id, "plan": record.plan,
                            "devbox_id": record.devbox_id, "status": record.status,
                            "created_at": record.created_at.isoformat() if record.created_at else None,
                        }),
                        ex=int(self._settings.devbox_default_ttl_hours * 3600),
                    )
            except Exception as _persist_exc:
                logger.warning("devbox_persist_failed", error=str(_persist_exc)[:200])

            # Start services asynchronously
            started = await executor.start_services(state.devbox_id)
            if started:
                record.status = "running"
                record.service_url = executor.get_devbox_url(state.devbox_id) or ""
            else:
                record.status = "failed"

            logger.info(
                "devbox_provisioned",
                devbox_id=state.devbox_id,
                user_id=user_id,
                plan=plan,
                status=record.status,
                billing_record_id=billing_record_id,
            )
            return state.devbox_id

        except Exception as exc:
            logger.error(
                "devbox_provision_error",
                user_id=user_id,
                error=str(exc)[:500],
            )
            return None

    async def get_user_devbox(self, user_id: str) -> DevBoxRecord | None:
        """Get the active DevBox for a user, if any."""
        for record in self._records.values():
            if record.user_id == user_id and record.status in ("provisioning", "running"):
                return record
        # Check Valkey for crash-recovered records
        try:
            from app.services.valkey_pool import get_valkey_client
            _vk = get_valkey_client()
            if _vk:
                import json as _json
                # Scan for user's devbox keys
                async for key in _vk.scan_iter(match="devbox:record:*", count=50):
                    raw = await _vk.get(key)
                    if raw:
                        data = _json.loads(raw)
                        if data.get("user_id") == user_id and data.get("status") in ("provisioning", "running"):
                            return DevBoxRecord(**{
                                k: data[k] for k in ("id", "user_id", "org_id", "plan", "devbox_id", "status")
                                if k in data
                            })
        except Exception as _vk_exc:
            logger.debug("devbox_valkey_lookup_failed", error=str(_vk_exc)[:200])
        return None

    async def get_devbox(self, devbox_id: str) -> DevBoxRecord | None:
        """Get a DevBox by its ID."""
        return self._records.get(devbox_id)

    async def extend_devbox(self, devbox_id: str, hours: int = 24) -> bool:
        """Extend a DevBox's TTL."""
        record = self._records.get(devbox_id)
        if not record or record.status != "running":
            return False

        try:
            executor = self._get_executor()
            success = await executor.extend_devbox(devbox_id, hours)
            if success:
                # Update record expiry
                from datetime import timedelta
                current_expires = datetime.fromisoformat(record.expires_at)
                record.expires_at = (current_expires + timedelta(hours=hours)).isoformat()
            return success
        except Exception as exc:
            logger.warning("devbox_extend_error", error=str(exc)[:200])
            return False

    async def deprovision_devbox(self, devbox_id: str) -> bool:
        """Terminate and clean up a DevBox."""
        record = self._records.pop(devbox_id, None)
        if not record:
            return False

        try:
            executor = self._get_executor()
            return await executor.terminate_devbox(devbox_id)
        except Exception as exc:
            logger.error("devbox_deprovision_error", error=str(exc)[:300])
            return False


# ── Singleton ────────────────────────────────────────────────────────

_manager: DevBoxManager | None = None


def get_devbox_manager() -> DevBoxManager:
    """Return the DevBoxManager singleton."""
    global _manager
    if _manager is None:
        _manager = DevBoxManager()
    return _manager
