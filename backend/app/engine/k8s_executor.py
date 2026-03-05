"""Kubernetes Job-based sandbox executor.

I6-FIX: Alternative to Docker-in-Docker ExecutionEngine for production
environments with a Kubernetes cluster.  Creates ephemeral K8s Jobs in
isolated namespaces with NetworkPolicy, ResourceQuota, and auto-cleanup
via Job TTL.

Current Docker sandbox is already hardened (gVisor + seccomp + AppArmor +
resource limits + isolated network + approved base images).  This K8s
executor adds true process isolation — sandboxes run on separate nodes
and never share the API host's Docker daemon.

Configuration:
    Set ``executor_type=kubernetes`` in settings to enable.
    Requires ``kubernetes`` Python package and kubeconfig / in-cluster SA.

Architecture:
    build_sandbox()   → Create namespace, NetworkPolicy, ResourceQuota, ConfigMap
    start_sandbox()   → Create Job, wait for pod ready
    run_api_tests()   → Port-forward to pod, reuse httpx testing
    cleanup_sandbox() → Delete namespace (cascading delete)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
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


@dataclass(slots=True)
class K8sSandboxState:
    """Runtime state of a K8s sandbox."""

    pipeline_run_id: str
    namespace: str = ""
    job_name: str = ""
    is_running: bool = False
    is_healthy: bool = False
    base_url: str = ""


# ── Executor ───────────────────────────────────────────────────────


class KubernetesExecutor:
    """Kubernetes Job-based sandbox executor.

    Lifecycle:
    1. ``build_sandbox()``:   Create namespace + NetworkPolicy + ResourceQuota + ConfigMap
    2. ``start_sandbox()``:   Create Job, wait for pod healthy
    3. ``run_api_tests()``:   Port-forward to pod, run httpx tests
    4. ``cleanup_sandbox()``: Delete namespace (cascading delete of all resources)
    """

    def __init__(self, config: K8sConfig | None = None) -> None:
        self._config = config or K8sConfig()
        self._active_sandboxes: dict[str, K8sSandboxState] = {}
        self._k8s_available: bool | None = None

    def _get_k8s(self) -> Any:
        """Lazy-init Kubernetes client."""
        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()
            return client
        except ImportError:
            raise RuntimeError(
                "kubernetes package not installed. "
                "Install via: pip install kubernetes>=31.0.0"
            )

    def check_available(self) -> bool:
        """Check if Kubernetes is available."""
        if self._k8s_available is not None:
            return self._k8s_available
        try:
            k8s = self._get_k8s()
            core_v1 = k8s.CoreV1Api()
            # Quick check: list namespaces (will fail if no access)
            core_v1.list_namespace(limit=1)
            self._k8s_available = True
        except Exception as exc:
            logger.warning("k8s_not_available", error=str(exc)[:200])
            self._k8s_available = False
        return self._k8s_available

    async def build_sandbox(
        self,
        pipeline_run_id: str,
        project_files: dict[str, str],
        timeout_seconds: int = 300,
    ) -> bool:
        """Create K8s namespace with NetworkPolicy and ConfigMap."""
        k8s = self._get_k8s()

        # Sanitize run ID for K8s naming (max 63 chars, lowercase alphanumeric + dash)
        safe_id = pipeline_run_id[:8].lower().replace("_", "-")
        namespace = f"{self._config.namespace_prefix}-{safe_id}"
        state = K8sSandboxState(
            pipeline_run_id=pipeline_run_id,
            namespace=namespace,
            job_name=f"sandbox-{safe_id}",
        )

        try:
            core_v1 = k8s.CoreV1Api()
            networking_v1 = k8s.NetworkingV1Api()

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
            await asyncio.to_thread(core_v1.create_namespace, body=ns)
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
                            from_=[k8s.V1NetworkPolicyPeer(
                                pod_selector=k8s.V1LabelSelector(),
                            )],
                        ),
                    ],
                ),
            )
            await asyncio.to_thread(
                networking_v1.create_namespaced_network_policy,
                namespace=namespace,
                body=net_policy,
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
            await asyncio.to_thread(
                core_v1.create_namespaced_resource_quota,
                namespace=namespace,
                body=quota,
            )

            # 4. Create ConfigMaps with project files (1MB limit per ConfigMap)
            chunk: dict[str, str] = {}
            chunk_size = 0
            chunk_idx = 0
            for filepath, content in project_files.items():
                # K8s ConfigMap keys must be valid: replace / and . with safe chars
                safe_key = filepath.replace("/", "__").replace(".", "_dot_")
                content_bytes = len(content.encode("utf-8"))
                if chunk_size + content_bytes > 900_000:  # Leave margin
                    await self._create_configmap(
                        core_v1, k8s, namespace, f"project-files-{chunk_idx}", chunk,
                    )
                    chunk = {}
                    chunk_size = 0
                    chunk_idx += 1
                chunk[safe_key] = content
                chunk_size += content_bytes

            if chunk:
                await self._create_configmap(
                    core_v1, k8s, namespace, f"project-files-{chunk_idx}", chunk,
                )

            self._active_sandboxes[pipeline_run_id] = state
            logger.info("k8s_sandbox_built", namespace=namespace)
            return True

        except Exception as exc:
            logger.error("k8s_sandbox_build_failed", error=str(exc)[:500])
            # Best-effort cleanup
            try:
                await asyncio.to_thread(
                    k8s.CoreV1Api().delete_namespace, name=namespace,
                )
            except Exception:
                pass
            return False

    async def _create_configmap(
        self, core_v1: Any, k8s: Any, namespace: str, name: str, data: dict[str, str],
    ) -> None:
        cm = k8s.V1ConfigMap(
            metadata=k8s.V1ObjectMeta(name=name),
            data=data,
        )
        await asyncio.to_thread(
            core_v1.create_namespaced_config_map,
            namespace=namespace,
            body=cm,
        )

    async def start_sandbox(
        self, pipeline_run_id: str, timeout_seconds: int = 120,
    ) -> bool:
        """Create K8s Job and wait for it to be ready."""
        state = self._active_sandboxes.get(pipeline_run_id)
        if not state:
            return False

        k8s = self._get_k8s()
        batch_v1 = k8s.BatchV1Api()

        try:
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
                            containers=[k8s.V1Container(
                                name="sandbox",
                                image=self._config.builder_image,
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
                            restart_policy="Never",
                            automount_service_account_token=False,
                        ),
                    ),
                ),
            )

            await asyncio.to_thread(
                batch_v1.create_namespaced_job,
                namespace=state.namespace,
                body=job,
            )

            # Wait for pod to be ready
            deadline = time.monotonic() + timeout_seconds
            core_v1 = k8s.CoreV1Api()
            while time.monotonic() < deadline:
                pods = await asyncio.to_thread(
                    core_v1.list_namespaced_pod,
                    namespace=state.namespace,
                    label_selector="app=nexsidi-sandbox,role=runner",
                )
                for pod in pods.items:
                    if pod.status and pod.status.phase == "Running":
                        state.is_running = True
                        state.is_healthy = True
                        state.base_url = f"http://{pod.status.pod_ip}:8000"
                        logger.info(
                            "k8s_sandbox_started",
                            namespace=state.namespace,
                            pod_ip=pod.status.pod_ip,
                        )
                        return True
                await asyncio.sleep(2)

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
        """Delete the namespace (cascading delete of all resources)."""
        state = self._active_sandboxes.pop(pipeline_run_id, None)
        if not state:
            return

        try:
            k8s = self._get_k8s()
            await asyncio.to_thread(
                k8s.CoreV1Api().delete_namespace,
                name=state.namespace,
            )
            logger.info("k8s_sandbox_cleaned_up", namespace=state.namespace)
        except Exception as exc:
            logger.error(
                "k8s_sandbox_cleanup_failed",
                namespace=state.namespace,
                error=str(exc)[:200],
            )


# ── Factory ────────────────────────────────────────────────────────


def get_k8s_executor(config: K8sConfig | None = None) -> KubernetesExecutor:
    """Return a KubernetesExecutor instance."""
    return KubernetesExecutor(config)
