"""Kubernetes Job-based sandbox executor — native async.

C1-FIX: Production-grade K8s executor replacing I6-FIX.  Key changes:
- ``kubernetes_asyncio`` for native async K8s API calls (no asyncio.to_thread)
- GCS object store for code delivery (no ConfigMap etcd thrashing)
- Watch API for pod readiness (no poll loop)
- InitContainer + emptyDir pattern for code injection

Architecture:
    build_sandbox()   → Create namespace, NetworkPolicy, ResourceQuota, upload code to GCS
    start_sandbox()   → Create Job with InitContainer (GCS download), watch for pod ready
    run_api_tests()   → httpx tests against pod ClusterIP
    cleanup_sandbox() → Delete namespace + GCS blob

Configuration:
    Set ``executor_type=kubernetes`` in settings to enable.
    Requires ``kubernetes_asyncio`` package, kubeconfig / in-cluster SA,
    and optionally ``gcs_code_bucket`` for large project files.
"""

from __future__ import annotations

import asyncio
import io
import tarfile
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Configuration ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class K8sConfig:
    """Kubernetes executor configuration."""

    namespace_prefix: str = "nexsidi-sandbox"
    job_ttl_seconds: int = 3600          # Auto-cleanup after 1 hour
    active_deadline_seconds: int = 1800  # 30 min total timeout
    cpu_limit: str = "2"
    memory_limit: str = "2Gi"
    storage_limit: str = "5Gi"
    max_pods: int = 5
    builder_image: str = "nexsidi/sandbox-builder:latest"
    gcs_bucket: str = ""                 # GCS bucket for project files (empty = ConfigMap fallback)
    init_image: str = "google/cloud-sdk:slim"  # InitContainer image for GCS download


@dataclass(slots=True)
class K8sSandboxState:
    """Runtime state of a K8s sandbox."""

    pipeline_run_id: str
    namespace: str = ""
    job_name: str = ""
    is_running: bool = False
    is_healthy: bool = False
    base_url: str = ""
    gcs_blob_path: str = ""  # GCS path for cleanup


# ── Executor ───────────────────────────────────────────────────────


class KubernetesExecutor:
    """Kubernetes Job-based sandbox executor (native async).

    Lifecycle:
    1. ``build_sandbox()``:   Create namespace + NetworkPolicy + ResourceQuota + upload code
    2. ``start_sandbox()``:   Create Job with InitContainer, watch for pod ready
    3. ``run_api_tests()``:   httpx tests against pod ClusterIP
    4. ``cleanup_sandbox()``: Delete namespace + GCS blob
    """

    def __init__(self, config: K8sConfig | None = None) -> None:
        self._config = config or K8sConfig()
        self._active_sandboxes: dict[str, K8sSandboxState] = {}
        self._k8s_available: bool | None = None
        self._api_client: Any | None = None

    async def _get_k8s(self) -> tuple[Any, Any]:
        """Lazy-init async Kubernetes client.

        Returns (client_module, api_client_instance).
        """
        try:
            from kubernetes_asyncio import client, config as k8s_config

            if self._api_client is None:
                try:
                    k8s_config.load_incluster_config()
                except k8s_config.ConfigException:
                    await k8s_config.load_kube_config()
                self._api_client = client.ApiClient()

            return client, self._api_client
        except ImportError:
            raise RuntimeError(
                "kubernetes_asyncio package not installed. "
                "Install via: pip install kubernetes_asyncio>=31.0.0"
            )

    async def close(self) -> None:
        """Clean up the async API client connection pool."""
        if self._api_client is not None:
            try:
                await self._api_client.close()
            except Exception:
                pass  # Non-critical — error logged upstream or handled by caller
            self._api_client = None

    async def check_available(self) -> bool:
        """Check if Kubernetes is available (async)."""
        if self._k8s_available is not None:
            return self._k8s_available
        try:
            k8s, api_client = await self._get_k8s()
            core_v1 = k8s.CoreV1Api(api_client)
            # Quick check: list namespaces (will fail if no access)
            await core_v1.list_namespace(limit=1)
            self._k8s_available = True
        except Exception as exc:
            logger.warning("k8s_not_available", error=str(exc)[:200])
            self._k8s_available = False
        return self._k8s_available

    # ── Code delivery ──────────────────────────────────────────────

    async def _upload_to_gcs(
        self, run_id: str, project_files: dict[str, str],
    ) -> str:
        """Upload project files as tar.gz to GCS, return gs:// URI.

        Uses google-cloud-storage (already in requirements for secret manager).
        Runs the sync GCS client in a thread to avoid blocking the event loop
        (GCS client is sync-only, but it's a single upload — acceptable).
        """
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for filepath, content in project_files.items():
                data = content.encode("utf-8")
                info = tarfile.TarInfo(name=filepath)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        buf.seek(0)

        blob_path = f"sandboxes/{run_id}/project.tar.gz"

        def _do_upload() -> None:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(self._config.gcs_bucket)
            blob = bucket.blob(blob_path)
            blob.upload_from_file(buf, content_type="application/gzip")

        await asyncio.to_thread(_do_upload)

        gcs_uri = f"gs://{self._config.gcs_bucket}/{blob_path}"
        logger.info("gcs_code_uploaded", uri=gcs_uri, files=len(project_files))
        return gcs_uri

    async def _cleanup_gcs(self, blob_path: str) -> None:
        """Delete the GCS blob for a sandbox."""
        if not blob_path or not self._config.gcs_bucket:
            return

        def _do_delete() -> None:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(self._config.gcs_bucket)
            blob = bucket.blob(blob_path)
            blob.delete()

        try:
            await asyncio.to_thread(_do_delete)
            logger.info("gcs_code_cleaned_up", blob=blob_path)
        except Exception as exc:
            logger.warning("gcs_cleanup_failed", blob=blob_path, error=str(exc)[:200])

    async def _create_configmap_fallback(
        self,
        core_v1: Any,
        k8s: Any,
        namespace: str,
        project_files: dict[str, str],
    ) -> None:
        """Fallback: create ConfigMaps when GCS is not configured (dev mode).

        Chunks files to stay under etcd's 1MB limit per ConfigMap.
        """
        chunk: dict[str, str] = {}
        chunk_size = 0
        chunk_idx = 0

        for filepath, content in project_files.items():
            safe_key = filepath.replace("/", "__").replace(".", "_dot_")
            content_bytes = len(content.encode("utf-8"))
            if chunk_size + content_bytes > 900_000:
                cm = k8s.V1ConfigMap(
                    metadata=k8s.V1ObjectMeta(name=f"project-files-{chunk_idx}"),
                    data=chunk,
                )
                await core_v1.create_namespaced_config_map(
                    namespace=namespace, body=cm,
                )
                chunk = {}
                chunk_size = 0
                chunk_idx += 1
            chunk[safe_key] = content
            chunk_size += content_bytes

        if chunk:
            cm = k8s.V1ConfigMap(
                metadata=k8s.V1ObjectMeta(name=f"project-files-{chunk_idx}"),
                data=chunk,
            )
            await core_v1.create_namespaced_config_map(
                namespace=namespace, body=cm,
            )

    # ── Sandbox lifecycle ──────────────────────────────────────────

    async def build_sandbox(
        self,
        pipeline_run_id: str,
        project_files: dict[str, str],
        timeout_seconds: int = 300,
    ) -> bool:
        """Create K8s namespace with NetworkPolicy, upload code to GCS."""
        k8s, api_client = await self._get_k8s()

        # Sanitize run ID for K8s naming (max 63 chars, lowercase alphanumeric + dash)
        safe_id = pipeline_run_id[:8].lower().replace("_", "-")
        namespace = f"{self._config.namespace_prefix}-{safe_id}"
        state = K8sSandboxState(
            pipeline_run_id=pipeline_run_id,
            namespace=namespace,
            job_name=f"sandbox-{safe_id}",
        )

        try:
            core_v1 = k8s.CoreV1Api(api_client)
            networking_v1 = k8s.NetworkingV1Api(api_client)

            # 1. Create namespace
            ns = k8s.V1Namespace(
                metadata=k8s.V1ObjectMeta(
                    name=namespace,
                    labels={
                        "app": "nexsidi-sandbox",
                        "run-id": safe_id,
                    },
                ),
            )
            await core_v1.create_namespace(body=ns)
            logger.info("k8s_namespace_created", namespace=namespace)

            # 2. Create NetworkPolicy — deny all egress except DNS
            net_policy = k8s.V1NetworkPolicy(
                metadata=k8s.V1ObjectMeta(name="sandbox-isolation"),
                spec=k8s.V1NetworkPolicySpec(
                    pod_selector=k8s.V1LabelSelector(),
                    policy_types=["Egress", "Ingress"],
                    egress=[
                        # Allow DNS only
                        k8s.V1NetworkPolicyEgressRule(
                            ports=[k8s.V1NetworkPolicyPort(port=53, protocol="UDP")],
                        ),
                        # Allow intra-namespace communication
                        k8s.V1NetworkPolicyEgressRule(
                            to=[k8s.V1NetworkPolicyPeer(
                                pod_selector=k8s.V1LabelSelector(),
                            )],
                        ),
                    ],
                    ingress=[
                        # Allow intra-namespace only
                        k8s.V1NetworkPolicyIngressRule(
                            _from=[k8s.V1NetworkPolicyPeer(
                                pod_selector=k8s.V1LabelSelector(),
                            )],
                        ),
                    ],
                ),
            )
            await networking_v1.create_namespaced_network_policy(
                namespace=namespace, body=net_policy,
            )

            # 3. Create ResourceQuota
            quota = k8s.V1ResourceQuota(
                metadata=k8s.V1ObjectMeta(name="sandbox-quota"),
                spec=k8s.V1ResourceQuotaSpec(hard={
                    "cpu": self._config.cpu_limit,
                    "memory": self._config.memory_limit,
                    "pods": str(self._config.max_pods),
                    "persistentvolumeclaims": "1",
                }),
            )
            await core_v1.create_namespaced_resource_quota(
                namespace=namespace, body=quota,
            )

            # 4. Upload project files — GCS (production) or ConfigMap (dev fallback)
            if self._config.gcs_bucket:
                gcs_uri = await self._upload_to_gcs(pipeline_run_id, project_files)
                blob_path = f"sandboxes/{pipeline_run_id}/project.tar.gz"
                state.gcs_blob_path = blob_path
                # Store GCS URI in namespace annotation for start_sandbox()
                ns_patch = k8s.V1Namespace(
                    metadata=k8s.V1ObjectMeta(
                        annotations={"nexsidi.dev/gcs-uri": gcs_uri},
                    ),
                )
                await core_v1.patch_namespace(name=namespace, body=ns_patch)
            else:
                await self._create_configmap_fallback(
                    core_v1, k8s, namespace, project_files,
                )
                logger.info(
                    "k8s_configmap_fallback",
                    namespace=namespace,
                    files=len(project_files),
                )

            self._active_sandboxes[pipeline_run_id] = state
            logger.info("k8s_sandbox_built", namespace=namespace)
            return True

        except Exception as exc:
            logger.error("k8s_sandbox_build_failed", error=str(exc)[:500])
            # Best-effort cleanup
            try:
                core_v1_cleanup = k8s.CoreV1Api(api_client)
                await core_v1_cleanup.delete_namespace(name=namespace)
            except Exception:
                pass  # Non-critical — error logged upstream or handled by caller
            return False

    async def start_sandbox(
        self, pipeline_run_id: str, timeout_seconds: int = 120,
    ) -> bool:
        """Create K8s Job with InitContainer and watch for pod ready."""
        state = self._active_sandboxes.get(pipeline_run_id)
        if not state:
            return False

        k8s, api_client = await self._get_k8s()
        batch_v1 = k8s.BatchV1Api(api_client)
        core_v1 = k8s.CoreV1Api(api_client)

        try:
            # Read GCS URI from namespace annotation (if GCS mode)
            gcs_uri = ""
            if self._config.gcs_bucket:
                ns = await core_v1.read_namespace(name=state.namespace)
                gcs_uri = (ns.metadata.annotations or {}).get(
                    "nexsidi.dev/gcs-uri", ""
                )

            # Build pod spec with InitContainer + emptyDir
            volumes = [
                k8s.V1Volume(
                    name="workspace",
                    empty_dir=k8s.V1EmptyDirVolumeSource(
                        size_limit=self._config.storage_limit,
                    ),
                ),
            ]

            workspace_mount = k8s.V1VolumeMount(
                name="workspace", mount_path="/workspace",
            )

            # InitContainer: download code from GCS into /workspace
            init_containers = []
            if gcs_uri:
                init_containers.append(k8s.V1Container(
                    name="code-loader",
                    image=self._config.init_image,
                    command=["sh", "-c",
                        f"gsutil cp {gcs_uri} /tmp/project.tar.gz && "
                        f"tar xzf /tmp/project.tar.gz -C /workspace",
                    ],
                    volume_mounts=[workspace_mount],
                    resources=k8s.V1ResourceRequirements(
                        requests={"cpu": "100m", "memory": "128Mi"},
                        limits={"cpu": "500m", "memory": "256Mi"},
                    ),
                ))

            job = k8s.V1Job(
                metadata=k8s.V1ObjectMeta(name=state.job_name),
                spec=k8s.V1JobSpec(
                    ttl_seconds_after_finished=self._config.job_ttl_seconds,
                    active_deadline_seconds=self._config.active_deadline_seconds,
                    template=k8s.V1PodTemplateSpec(
                        metadata=k8s.V1ObjectMeta(
                            labels={"app": "nexsidi-sandbox", "role": "runner"},
                        ),
                        spec=k8s.V1PodSpec(
                            init_containers=init_containers or None,
                            containers=[k8s.V1Container(
                                name="sandbox",
                                image=self._config.builder_image,
                                volume_mounts=[workspace_mount],
                                resources=k8s.V1ResourceRequirements(
                                    limits={
                                        "cpu": self._config.cpu_limit,
                                        "memory": self._config.memory_limit,
                                    },
                                    requests={
                                        "cpu": "500m",
                                        "memory": "512Mi",
                                    },
                                ),
                                security_context=k8s.V1SecurityContext(
                                    run_as_non_root=True,
                                    read_only_root_filesystem=True,
                                    allow_privilege_escalation=False,
                                    capabilities=k8s.V1Capabilities(
                                        drop=["ALL"],
                                        add=["NET_BIND_SERVICE"],
                                    ),
                                ),
                            )],
                            volumes=volumes,
                            restart_policy="Never",
                            automount_service_account_token=False,
                        ),
                    ),
                ),
            )

            await batch_v1.create_namespaced_job(
                namespace=state.namespace, body=job,
            )

            # Watch for pod ready — replaces poll loop
            from kubernetes_asyncio import watch

            w = watch.Watch()
            try:
                async for event in w.stream(
                    core_v1.list_namespaced_pod,
                    namespace=state.namespace,
                    label_selector="app=nexsidi-sandbox,role=runner",
                    timeout_seconds=timeout_seconds,
                ):
                    pod = event["object"]
                    phase = pod.status.phase if pod.status else None

                    if phase == "Running":
                        state.is_running = True
                        state.is_healthy = True
                        state.base_url = f"http://{pod.status.pod_ip}:8000"
                        logger.info(
                            "k8s_sandbox_started",
                            namespace=state.namespace,
                            pod_ip=pod.status.pod_ip,
                        )
                        return True

                    if phase in ("Failed", "Unknown"):
                        logger.error(
                            "k8s_sandbox_pod_failed",
                            namespace=state.namespace,
                            phase=phase,
                        )
                        return False
            except asyncio.TimeoutError:
                pass  # Expected: operation timed out — non-blocking
            finally:
                w.stop()

            logger.error("k8s_sandbox_start_timeout", namespace=state.namespace)
            return False

        except Exception as exc:
            logger.error("k8s_sandbox_start_failed", error=str(exc)[:500])
            return False

    async def run_api_tests(
        self,
        pipeline_run_id: str,
        endpoints: list[dict[str, Any]],
        timeout_seconds: int = 600,
    ) -> dict[str, Any]:
        """Run API tests against the K8s sandbox pod.

        Reuses the same httpx-based testing logic as ExecutionEngine
        but targeting the pod's ClusterIP endpoint.
        """
        state = self._active_sandboxes.get(pipeline_run_id)
        if not state or not state.base_url:
            return {"passed": False, "error": "Sandbox not running"}

        import httpx

        results: list[dict[str, Any]] = []
        passed = 0
        failed = 0

        async with httpx.AsyncClient(
            base_url=state.base_url, timeout=30.0,
        ) as client:
            for ep in endpoints:
                method = ep.get("method", "GET").upper()
                path = ep.get("path", "/")
                expected_status = ep.get("expected_status", 200)

                try:
                    resp = await client.request(method, path)
                    success = resp.status_code == expected_status
                    if success:
                        passed += 1
                    else:
                        failed += 1
                    results.append({
                        "endpoint": f"{method} {path}",
                        "status_code": resp.status_code,
                        "expected": expected_status,
                        "passed": success,
                    })
                except Exception as exc:
                    failed += 1
                    results.append({
                        "endpoint": f"{method} {path}",
                        "error": str(exc)[:200],
                        "passed": False,
                    })

        return {
            "passed": failed == 0,
            "total": len(results),
            "passed_count": passed,
            "failed_count": failed,
            "results": results,
        }

    async def cleanup_sandbox(self, pipeline_run_id: str) -> None:
        """Delete the namespace (cascading delete) and GCS blob."""
        state = self._active_sandboxes.pop(pipeline_run_id, None)
        if not state:
            return

        # Clean up GCS blob
        if state.gcs_blob_path:
            await self._cleanup_gcs(state.gcs_blob_path)

        # Delete K8s namespace (cascading delete of all resources)
        try:
            k8s, api_client = await self._get_k8s()
            core_v1 = k8s.CoreV1Api(api_client)
            await core_v1.delete_namespace(name=state.namespace)
            logger.info("k8s_sandbox_cleaned_up", namespace=state.namespace)
        except Exception as exc:
            logger.error(
                "k8s_sandbox_cleanup_failed",
                namespace=state.namespace,
                error=str(exc)[:200],
            )


# ── Phase 2A: DevBox Executor (Deployment-based daemon mode) ─────────
#
# Extends KubernetesExecutor from Job-based (run-once) to Deployment-based
# (long-running daemon). A DevBox runs docker-compose services and keeps
# them alive while tests execute. Targets GKE Autopilot cluster nexsidi-devbox.
#
# Multi-container Pod approach: each DevBox pod has sidecar containers
# for backend, frontend, and DB — GKE Autopilot natively orchestrates them.
# No Docker-in-Docker needed.
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DevBoxConfig:
    """DevBox configuration for cloud execution environments."""

    gcp_project_id: str = "nexsidi-ai"
    gke_cluster_name: str = "nexsidi-devbox"
    gke_cluster_zone: str = "asia-south1"
    artifact_registry: str = "asia-south1-docker.pkg.dev/nexsidi-ai/nexsidi"
    namespace_prefix: str = "nexsidi-devbox"
    default_ttl_hours: int = 24
    max_per_user: int = 1


# Resource tiers per billing plan
_DEVBOX_RESOURCE_TIERS: dict[str, dict[str, str]] = {
    "starter": {"cpu": "2", "memory": "4Gi", "storage": "10Gi"},
    "pro": {"cpu": "4", "memory": "8Gi", "storage": "20Gi"},
    "enterprise": {"cpu": "8", "memory": "16Gi", "storage": "50Gi"},
}


@dataclass(slots=True)
class DevBoxState:
    """Runtime state of a cloud DevBox."""

    devbox_id: str
    user_id: str
    plan: str  # starter | pro | enterprise
    namespace: str = ""
    deployment_name: str = ""
    service_url: str = ""
    status: str = "provisioning"  # provisioning | running | stopped | failed | expired
    created_at: str = ""
    expires_at: str = ""
    service_name: str = ""
    pvc_name: str = ""


class DevBoxExecutor(KubernetesExecutor):
    """Deployment-based long-running DevBox executor for GKE Autopilot.

    Unlike the base KubernetesExecutor (Job-based, run-once), DevBox creates
    a Deployment + Service + PVC that stays alive for the TTL duration.
    Users get a persistent development environment tied to their billing plan.

    Lifecycle:
    1. ``create_devbox()``:    Create namespace + PVC + Deployment + Service
    2. ``start_services()``:   Wait for pod ready, return service URL
    3. ``get_devbox_url()``:   Return the Service endpoint URL
    4. ``terminate_devbox()``: Delete Deployment + PVC + Service + namespace
    """

    def __init__(
        self,
        devbox_config: DevBoxConfig | None = None,
        k8s_config: K8sConfig | None = None,
    ) -> None:
        super().__init__(k8s_config)
        self._devbox_config = devbox_config or DevBoxConfig()
        self._active_devboxes: dict[str, DevBoxState] = {}

    def _plan_to_resources(self, plan: str) -> dict[str, str]:
        """Map billing plan to CPU/memory/storage resources."""
        return _DEVBOX_RESOURCE_TIERS.get(plan, _DEVBOX_RESOURCE_TIERS["starter"])

    async def create_devbox(
        self,
        user_id: str,
        plan: str,
        project_files: dict[str, str],
        devbox_id: str | None = None,
    ) -> DevBoxState:
        """Create a cloud DevBox — K8s Deployment + PVC + Service.

        Creates a multi-container pod with backend, frontend, and DB sidecars.
        GKE Autopilot provisions the node automatically.

        Args:
            user_id: Owner user ID.
            plan: Billing plan (starter/pro/enterprise).
            project_files: Generated project files to deploy.
            devbox_id: Optional explicit ID (auto-generated if None).

        Returns:
            DevBoxState with provisioning status and IDs.
        """
        import uuid as _uuid
        from datetime import datetime, timezone, timedelta

        k8s, api_client = await self._get_k8s()

        if devbox_id is None:
            devbox_id = str(_uuid.uuid4())[:12]

        safe_id = devbox_id.lower().replace("_", "-")[:12]
        namespace = f"{self._devbox_config.namespace_prefix}-{safe_id}"
        deployment_name = f"devbox-{safe_id}"
        service_name = f"devbox-svc-{safe_id}"
        pvc_name = f"devbox-pvc-{safe_id}"

        resources = self._plan_to_resources(plan)
        now = datetime.now(timezone.utc)
        ttl_hours = self._devbox_config.default_ttl_hours
        expires_at = now + timedelta(hours=ttl_hours)

        state = DevBoxState(
            devbox_id=devbox_id,
            user_id=user_id,
            plan=plan,
            namespace=namespace,
            deployment_name=deployment_name,
            service_name=service_name,
            pvc_name=pvc_name,
            status="provisioning",
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
        )

        try:
            core_v1 = k8s.CoreV1Api(api_client)
            apps_v1 = k8s.AppsV1Api(api_client)

            # 1. Create namespace with labels
            ns = k8s.V1Namespace(
                metadata=k8s.V1ObjectMeta(
                    name=namespace,
                    labels={
                        "app": "nexsidi-devbox",
                        "user-id": user_id[:63],
                        "plan": plan,
                        "devbox-id": safe_id,
                    },
                    annotations={
                        "nexsidi.dev/expires-at": expires_at.isoformat(),
                        "nexsidi.dev/user-id": user_id,
                    },
                ),
            )
            await core_v1.create_namespace(body=ns)

            # 2. Create PVC for persistent workspace
            pvc = k8s.V1PersistentVolumeClaim(
                metadata=k8s.V1ObjectMeta(name=pvc_name),
                spec=k8s.V1PersistentVolumeClaimSpec(
                    access_modes=["ReadWriteOnce"],
                    resources=k8s.V1VolumeResourceRequirements(
                        requests={"storage": resources["storage"]},
                    ),
                    storage_class_name="standard-rwo",  # GKE Autopilot default
                ),
            )
            await core_v1.create_namespaced_persistent_volume_claim(
                namespace=namespace, body=pvc,
            )

            # 3. Upload project code to GCS
            gcs_uri = ""
            if self._config.gcs_bucket:
                gcs_uri = await self._upload_to_gcs(devbox_id, project_files)

            # 4. Create Deployment with multi-container pod
            workspace_mount = k8s.V1VolumeMount(
                name="workspace", mount_path="/workspace",
            )

            # Init container: load code from GCS into workspace
            init_containers = []
            if gcs_uri:
                init_containers.append(k8s.V1Container(
                    name="code-loader",
                    image=self._config.init_image,
                    command=["sh", "-c",
                        f"gsutil cp {gcs_uri} /tmp/project.tar.gz && "
                        f"tar xzf /tmp/project.tar.gz -C /workspace",
                    ],
                    volume_mounts=[workspace_mount],
                    resources=k8s.V1ResourceRequirements(
                        requests={"cpu": "100m", "memory": "128Mi"},
                        limits={"cpu": "500m", "memory": "256Mi"},
                    ),
                ))

            # Main containers: backend + frontend sidecars
            backend_container = k8s.V1Container(
                name="backend",
                image=f"{self._devbox_config.artifact_registry}/devbox-backend:latest",
                ports=[k8s.V1ContainerPort(container_port=8000, name="backend")],
                volume_mounts=[workspace_mount],
                env=[
                    k8s.V1EnvVar(name="WORKSPACE_PATH", value="/workspace"),
                    k8s.V1EnvVar(name="DATABASE_URL", value="postgresql://devbox:devbox@localhost:5432/devbox"),
                ],
                resources=k8s.V1ResourceRequirements(
                    requests={"cpu": "250m", "memory": "512Mi"},
                    limits={
                        "cpu": str(int(resources["cpu"]) // 2),
                        "memory": str(int(resources["memory"].replace("Gi", "")) // 2) + "Gi",
                    },
                ),
                readiness_probe=k8s.V1Probe(
                    http_get=k8s.V1HTTPGetAction(path="/health", port=8000),
                    initial_delay_seconds=10,
                    period_seconds=5,
                ),
            )

            frontend_container = k8s.V1Container(
                name="frontend",
                image=f"{self._devbox_config.artifact_registry}/devbox-frontend:latest",
                ports=[k8s.V1ContainerPort(container_port=3000, name="frontend")],
                volume_mounts=[workspace_mount],
                env=[
                    k8s.V1EnvVar(name="WORKSPACE_PATH", value="/workspace"),
                ],
                resources=k8s.V1ResourceRequirements(
                    requests={"cpu": "250m", "memory": "256Mi"},
                    limits={
                        "cpu": str(max(1, int(resources["cpu"]) // 4)),
                        "memory": "1Gi",
                    },
                ),
            )

            db_container = k8s.V1Container(
                name="database",
                image="postgres:16-alpine",
                ports=[k8s.V1ContainerPort(container_port=5432, name="postgres")],
                env=[
                    k8s.V1EnvVar(name="POSTGRES_DB", value="devbox"),
                    k8s.V1EnvVar(name="POSTGRES_USER", value="devbox"),
                    k8s.V1EnvVar(name="POSTGRES_PASSWORD", value="devbox"),
                ],
                resources=k8s.V1ResourceRequirements(
                    requests={"cpu": "100m", "memory": "256Mi"},
                    limits={"cpu": "1", "memory": "1Gi"},
                ),
                volume_mounts=[k8s.V1VolumeMount(
                    name="pgdata", mount_path="/var/lib/postgresql/data",
                )],
            )

            deployment = k8s.V1Deployment(
                metadata=k8s.V1ObjectMeta(
                    name=deployment_name,
                    labels={
                        "app": "nexsidi-devbox",
                        "devbox-id": safe_id,
                    },
                ),
                spec=k8s.V1DeploymentSpec(
                    replicas=1,
                    selector=k8s.V1LabelSelector(
                        match_labels={"app": "nexsidi-devbox", "devbox-id": safe_id},
                    ),
                    template=k8s.V1PodTemplateSpec(
                        metadata=k8s.V1ObjectMeta(
                            labels={"app": "nexsidi-devbox", "devbox-id": safe_id},
                        ),
                        spec=k8s.V1PodSpec(
                            init_containers=init_containers or None,
                            containers=[backend_container, frontend_container, db_container],
                            volumes=[
                                k8s.V1Volume(
                                    name="workspace",
                                    persistent_volume_claim=k8s.V1PersistentVolumeClaimVolumeSource(
                                        claim_name=pvc_name,
                                    ),
                                ),
                                k8s.V1Volume(
                                    name="pgdata",
                                    empty_dir=k8s.V1EmptyDirVolumeSource(
                                        size_limit="2Gi",
                                    ),
                                ),
                            ],
                            automount_service_account_token=False,
                        ),
                    ),
                ),
            )
            await apps_v1.create_namespaced_deployment(
                namespace=namespace, body=deployment,
            )

            # 5. Create Service for external access
            service = k8s.V1Service(
                metadata=k8s.V1ObjectMeta(name=service_name),
                spec=k8s.V1ServiceSpec(
                    selector={"app": "nexsidi-devbox", "devbox-id": safe_id},
                    ports=[
                        k8s.V1ServicePort(name="backend", port=8000, target_port=8000),
                        k8s.V1ServicePort(name="frontend", port=3000, target_port=3000),
                    ],
                    type="ClusterIP",
                ),
            )
            await core_v1.create_namespaced_service(
                namespace=namespace, body=service,
            )

            state.status = "provisioning"
            self._active_devboxes[devbox_id] = state
            logger.info(
                "devbox_created",
                devbox_id=devbox_id,
                namespace=namespace,
                plan=plan,
                resources=resources,
            )
            return state

        except Exception as exc:
            state.status = "failed"
            logger.error("devbox_creation_failed", error=str(exc)[:500])
            # Best-effort cleanup
            try:
                core_v1_cleanup = k8s.CoreV1Api(api_client)
                await core_v1_cleanup.delete_namespace(name=namespace)
            except Exception:
                pass  # Non-critical — error logged upstream or handled by caller
            return state

    async def start_services(
        self, devbox_id: str, timeout_seconds: int = 180,
    ) -> bool:
        """Wait for DevBox pod to become ready and return service URL.

        Watches the Deployment's pod until the backend container passes
        its readiness probe.
        """
        state = self._active_devboxes.get(devbox_id)
        if not state:
            return False

        k8s, api_client = await self._get_k8s()
        core_v1 = k8s.CoreV1Api(api_client)

        try:
            from kubernetes_asyncio import watch

            w = watch.Watch()
            safe_id = devbox_id.lower().replace("_", "-")[:12]
            try:
                async for event in w.stream(
                    core_v1.list_namespaced_pod,
                    namespace=state.namespace,
                    label_selector=f"app=nexsidi-devbox,devbox-id={safe_id}",
                    timeout_seconds=timeout_seconds,
                ):
                    pod = event["object"]
                    phase = pod.status.phase if pod.status else None

                    if phase == "Running":
                        # Check readiness of all containers
                        container_statuses = pod.status.container_statuses or []
                        all_ready = all(cs.ready for cs in container_statuses)
                        if all_ready:
                            state.service_url = (
                                f"http://{state.service_name}.{state.namespace}.svc.cluster.local"
                            )
                            state.status = "running"
                            logger.info(
                                "devbox_started",
                                devbox_id=devbox_id,
                                service_url=state.service_url,
                            )
                            return True

                    if phase in ("Failed", "Unknown"):
                        state.status = "failed"
                        return False
            except asyncio.TimeoutError:
                pass  # Expected: operation timed out — non-blocking
            finally:
                w.stop()

            state.status = "failed"
            logger.error("devbox_start_timeout", devbox_id=devbox_id)
            return False

        except Exception as exc:
            state.status = "failed"
            logger.error("devbox_start_failed", error=str(exc)[:300])
            return False

    def get_devbox_url(self, devbox_id: str) -> str | None:
        """Return the Service endpoint URL for a running DevBox."""
        state = self._active_devboxes.get(devbox_id)
        if state and state.status == "running":
            return state.service_url
        return None

    def get_devbox_state(self, devbox_id: str) -> DevBoxState | None:
        """Return the full state of a DevBox."""
        return self._active_devboxes.get(devbox_id)

    async def terminate_devbox(self, devbox_id: str) -> bool:
        """Delete a DevBox — Deployment + PVC + Service + namespace.

        Cascading namespace deletion removes all contained resources.
        """
        state = self._active_devboxes.pop(devbox_id, None)
        if not state:
            return False

        try:
            k8s, api_client = await self._get_k8s()
            core_v1 = k8s.CoreV1Api(api_client)
            await core_v1.delete_namespace(name=state.namespace)
            state.status = "stopped"
            logger.info("devbox_terminated", devbox_id=devbox_id, namespace=state.namespace)
            return True
        except Exception as exc:
            logger.error("devbox_termination_failed", error=str(exc)[:300])
            return False

    async def extend_devbox(self, devbox_id: str, hours: int = 24) -> bool:
        """Extend a DevBox's TTL by the given number of hours."""
        state = self._active_devboxes.get(devbox_id)
        if not state:
            return False

        from datetime import datetime, timezone, timedelta
        current_expires = datetime.fromisoformat(state.expires_at)
        new_expires = current_expires + timedelta(hours=hours)
        state.expires_at = new_expires.isoformat()

        # Update namespace annotation
        try:
            k8s, api_client = await self._get_k8s()
            core_v1 = k8s.CoreV1Api(api_client)
            ns_patch = k8s.V1Namespace(
                metadata=k8s.V1ObjectMeta(
                    annotations={"nexsidi.dev/expires-at": new_expires.isoformat()},
                ),
            )
            await core_v1.patch_namespace(name=state.namespace, body=ns_patch)
            logger.info("devbox_extended", devbox_id=devbox_id, new_expires=new_expires.isoformat())
            return True
        except Exception as exc:
            logger.warning("devbox_extend_failed", error=str(exc)[:200])
            return False


# ── Factory ────────────────────────────────────────────────────────


def get_k8s_executor(config: K8sConfig | None = None) -> KubernetesExecutor:
    """Return a KubernetesExecutor instance."""
    return KubernetesExecutor(config)


def get_devbox_executor(
    devbox_config: DevBoxConfig | None = None,
    k8s_config: K8sConfig | None = None,
) -> DevBoxExecutor:
    """Return a DevBoxExecutor instance."""
    return DevBoxExecutor(devbox_config, k8s_config)
