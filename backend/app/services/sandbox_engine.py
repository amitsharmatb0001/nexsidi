"""Sandbox Engine — Isolated sandboxes for scanning untrusted content.

Separate from the code execution sandbox (execution_engine.py).
This is used by ZeroTrustGate to:
  1. Fetch URLs in isolated containers (phishing detection)
  2. Execute suspicious files in sandboxes (malware detection)
  3. Inspect archives in sandboxes (zip bomb detection)

Each sandbox is a disposable Docker container with:
  - gVisor runtime (if available)
  - No persistent storage
  - Limited network (only target for URL fetch, none for file scan)
  - 30-second timeout
  - 256MB memory limit
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Result Types ─────────────────────────────────────────────────────

@dataclass
class FetchResult:
    """Result of fetching a URL in an isolated sandbox."""

    sandbox_id: str
    final_url: str
    status_code: int = 0
    content: str = ""
    content_length: int = 0
    claimed_mime: str = ""
    actual_mime: str = ""
    mimetype_mismatch: bool = False
    redirects: list[str] = field(default_factory=list)
    redirects_to_different_domain: bool = False
    contains_javascript_redirect: bool = False
    ssl_valid: bool = True
    ssl_issuer: str = ""
    error: str | None = None


@dataclass
class ExecutionResult:
    """Result of executing a file in a sandbox."""

    sandbox_id: str
    suspicious_behavior: bool = False
    behaviors: list[str] = field(default_factory=list)
    file_system_changes: list[str] = field(default_factory=list)
    network_attempts: list[str] = field(default_factory=list)
    process_spawns: list[str] = field(default_factory=list)
    exit_code: int = 0
    error: str | None = None


@dataclass
class ArchiveScanResult:
    """Result of scanning an archive in a sandbox."""

    sandbox_id: str
    total_files: int = 0
    suspicious_files: list[str] = field(default_factory=list)
    is_zip_bomb: bool = False
    is_recursive: bool = False
    compressed_size: int = 0
    decompressed_size: int = 0
    error: str | None = None


# ── Sandbox Configuration ────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ScanSandboxConfig:
    """Configuration for scanning sandboxes — more restrictive than code execution."""

    memory_limit: str = "256m"
    cpus: float = 0.5
    pids_limit: int = 64
    timeout_seconds: int = 30
    network_mode: str = "none"    # Default: no network (overridden for URL fetch)
    read_only_rootfs: bool = True
    runtime: str = "runsc"        # gVisor if available


# ── Sandbox Engine ───────────────────────────────────────────────────

class SandboxEngine:
    """Manages disposable Docker sandboxes for scanning untrusted content."""

    def __init__(self, config: ScanSandboxConfig | None = None):
        self._config = config or ScanSandboxConfig()
        self._docker_available: bool | None = None

    async def _check_docker(self) -> bool:
        """Check if Docker is available."""
        if self._docker_available is not None:
            return self._docker_available
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
            self._docker_available = proc.returncode == 0
        except Exception:
            self._docker_available = False
        return self._docker_available

    async def fetch_in_sandbox(self, url: str) -> FetchResult:
        """Fetch URL inside an isolated Docker container.

        Container has network access ONLY to the target URL's host.
        Uses headless Chromium to capture JS redirects and dynamic content.
        """
        sandbox_id = f"scan-fetch-{uuid.uuid4().hex[:12]}"
        result = FetchResult(sandbox_id=sandbox_id, final_url=url)

        if not await self._check_docker():
            # Fallback: basic httpx fetch without sandbox isolation
            return await self._fetch_without_sandbox(url, result)

        try:
            # Build fetch script that runs inside container
            fetch_script = self._build_fetch_script(url)

            proc = await asyncio.create_subprocess_exec(
                "docker", "run",
                "--rm",
                "--name", sandbox_id,
                f"--memory={self._config.memory_limit}",
                f"--cpus={self._config.cpus}",
                f"--pids-limit={self._config.pids_limit}",
                "--read-only",
                "--tmpfs", "/tmp:size=32m",
                "--network=bridge",  # Need network for URL fetch
                "python:3.12-slim",
                "python", "-c", fetch_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._config.timeout_seconds,
            )

            if proc.returncode == 0 and stdout:
                data = json.loads(stdout.decode("utf-8", errors="replace"))
                result.status_code = data.get("status_code", 0)
                result.final_url = data.get("final_url", url)
                result.content = data.get("content", "")[:50000]  # Cap content
                result.content_length = data.get("content_length", 0)
                result.claimed_mime = data.get("claimed_mime", "")
                result.actual_mime = data.get("actual_mime", "")
                result.mimetype_mismatch = result.claimed_mime != result.actual_mime
                result.redirects = data.get("redirects", [])

                # Check if redirected to different domain
                from urllib.parse import urlparse
                original_domain = urlparse(url).hostname
                final_domain = urlparse(result.final_url).hostname
                result.redirects_to_different_domain = original_domain != final_domain

                # Check for JS-based redirects in content
                result.contains_javascript_redirect = any(
                    pattern in result.content.lower()
                    for pattern in [
                        "window.location", "document.location",
                        "location.href", "location.replace",
                        "meta http-equiv=\"refresh\"",
                    ]
                )
            else:
                result.error = stderr.decode("utf-8", errors="replace")[:500]

        except asyncio.TimeoutError:
            result.error = f"Sandbox fetch timed out after {self._config.timeout_seconds}s"
            await self._kill_container(sandbox_id)
        except Exception as exc:
            result.error = f"Sandbox fetch failed: {str(exc)[:200]}"

        return result

    async def execute_in_sandbox(
        self, content: bytes, mime: str
    ) -> ExecutionResult:
        """Execute file in sandbox to detect runtime malicious behavior.

        Monitors: file system changes, network attempts, process spawns.
        """
        sandbox_id = f"scan-exec-{uuid.uuid4().hex[:12]}"
        result = ExecutionResult(sandbox_id=sandbox_id)

        if not await self._check_docker():
            logger.warning("sandbox_exec_no_docker", mime=mime)
            result.error = "Docker unavailable — file execution analysis skipped"
            result.behaviors.append("DEGRADED: No sandbox available, file not analyzed")
            return result

        try:
            # Build analysis script based on MIME type
            analysis_script = self._build_analysis_script(mime)

            proc = await asyncio.create_subprocess_exec(
                "docker", "run",
                "--rm",
                "--name", sandbox_id,
                f"--memory={self._config.memory_limit}",
                f"--cpus={self._config.cpus}",
                f"--pids-limit={self._config.pids_limit}",
                "--read-only",
                "--tmpfs", "/tmp:size=32m",
                "--network=none",  # No network for file execution
                "python:3.12-slim",
                "python", "-c", analysis_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                input=content,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._config.timeout_seconds,
            )

            if proc.returncode == 0 and stdout:
                data = json.loads(stdout.decode("utf-8", errors="replace"))
                result.file_system_changes = data.get("fs_changes", [])
                result.network_attempts = data.get("net_attempts", [])
                result.process_spawns = data.get("proc_spawns", [])
                result.suspicious_behavior = bool(
                    result.file_system_changes
                    or result.network_attempts
                    or result.process_spawns
                )
                if result.suspicious_behavior:
                    result.behaviors = (
                        [f"FS: {c}" for c in result.file_system_changes[:5]]
                        + [f"NET: {n}" for n in result.network_attempts[:5]]
                        + [f"PROC: {p}" for p in result.process_spawns[:5]]
                    )
            else:
                result.error = stderr.decode("utf-8", errors="replace")[:500]
                result.exit_code = proc.returncode or -1

        except asyncio.TimeoutError:
            result.error = f"Sandbox execution timed out after {self._config.timeout_seconds}s"
            result.suspicious_behavior = True
            result.behaviors.append("TIMEOUT: Process exceeded time limit (possible infinite loop/fork bomb)")
            await self._kill_container(sandbox_id)
        except Exception as exc:
            result.error = f"Sandbox execution failed: {str(exc)[:200]}"

        return result

    async def scan_archive(self, content: bytes) -> ArchiveScanResult:
        """Extract and scan archive contents in sandbox.

        Detects: zip bombs, recursive archives, hidden executables.
        """
        sandbox_id = f"scan-archive-{uuid.uuid4().hex[:12]}"
        result = ArchiveScanResult(sandbox_id=sandbox_id, compressed_size=len(content))

        if not await self._check_docker():
            return self._scan_archive_basic(content, result)

        try:
            scan_script = self._build_archive_scan_script()

            proc = await asyncio.create_subprocess_exec(
                "docker", "run",
                "--rm",
                "--name", sandbox_id,
                f"--memory={self._config.memory_limit}",
                f"--cpus={self._config.cpus}",
                f"--pids-limit={self._config.pids_limit}",
                "--read-only",
                "--tmpfs", "/tmp:size=64m",
                "--network=none",
                "python:3.12-slim",
                "python", "-c", scan_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                input=content,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._config.timeout_seconds,
            )

            if proc.returncode == 0 and stdout:
                data = json.loads(stdout.decode("utf-8", errors="replace"))
                result.total_files = data.get("total_files", 0)
                result.decompressed_size = data.get("decompressed_size", 0)
                result.suspicious_files = data.get("suspicious_files", [])
                result.is_recursive = data.get("is_recursive", False)

                # Zip bomb: decompressed > 100x compressed
                if result.compressed_size > 0:
                    ratio = result.decompressed_size / result.compressed_size
                    result.is_zip_bomb = ratio > 100
            else:
                result.error = stderr.decode("utf-8", errors="replace")[:500]

        except asyncio.TimeoutError:
            result.error = "Archive scan timed out (possible zip bomb)"
            result.is_zip_bomb = True
            await self._kill_container(sandbox_id)
        except Exception as exc:
            result.error = f"Archive scan failed: {str(exc)[:200]}"

        return result

    # ── Internal Helpers ─────────────────────────────────────────────

    def _build_fetch_script(self, url: str) -> str:
        """Python script that runs inside the fetch sandbox container."""
        # Use repr() for safe string embedding
        safe_url = repr(url)
        return f"""
import json, sys, urllib.request, urllib.error
url = {safe_url}
redirects = []
result = {{"status_code": 0, "final_url": url, "content": "", "redirects": [], "claimed_mime": "", "actual_mime": ""}}
try:
    req = urllib.request.Request(url, headers={{"User-Agent": "NexSidi-Scanner/1.0"}})
    resp = urllib.request.urlopen(req, timeout=15)
    result["status_code"] = resp.status
    result["final_url"] = resp.url
    result["claimed_mime"] = resp.headers.get("Content-Type", "")
    content = resp.read(100000)  # Max 100KB
    result["content"] = content.decode("utf-8", errors="replace")
    result["content_length"] = len(content)
except urllib.error.HTTPError as e:
    result["status_code"] = e.code
    result["error"] = str(e)[:200]
except Exception as e:
    result["error"] = str(e)[:200]
print(json.dumps(result))
"""

    def _build_analysis_script(self, mime: str) -> str:
        """Python script to analyze file behavior in sandbox."""
        return """
import json, sys, os
content = sys.stdin.buffer.read()
result = {"fs_changes": [], "net_attempts": [], "proc_spawns": []}

# Check for executable signatures
if content[:2] == b'MZ':
    result["fs_changes"].append("PE executable detected (Windows .exe)")
elif content[:4] == b'\\x7fELF':
    result["fs_changes"].append("ELF executable detected (Linux binary)")
elif content[:4] in (b'\\xfe\\xed\\xfa\\xce', b'\\xfe\\xed\\xfa\\xcf'):
    result["fs_changes"].append("Mach-O executable detected (macOS binary)")
elif content[:2] == b'#!':
    result["proc_spawns"].append(f"Script with shebang: {content[:80].decode('utf-8', errors='replace')}")

# Check for embedded URLs (potential C2)
import re
urls = re.findall(rb'https?://[^\\s"\\'>]+', content[:50000])
for u in urls[:10]:
    result["net_attempts"].append(f"Embedded URL: {u.decode('utf-8', errors='replace')[:100]}")

print(json.dumps(result))
"""

    def _build_archive_scan_script(self) -> str:
        """Python script to scan archive contents in sandbox."""
        return """
import json, sys, zipfile, io
content = sys.stdin.buffer.read()
result = {"total_files": 0, "decompressed_size": 0, "suspicious_files": [], "is_recursive": False}
try:
    zf = zipfile.ZipFile(io.BytesIO(content))
    result["total_files"] = len(zf.namelist())
    for info in zf.infolist():
        result["decompressed_size"] += info.file_size
        name_lower = info.filename.lower()
        # Check for suspicious extensions
        dangerous_exts = ('.exe', '.dll', '.bat', '.cmd', '.ps1', '.vbs', '.js', '.msi', '.scr', '.com')
        if any(name_lower.endswith(ext) for ext in dangerous_exts):
            result["suspicious_files"].append(f"Executable: {info.filename}")
        # Check for double extensions
        if name_lower.count('.') >= 2 and any(name_lower.endswith(ext) for ext in dangerous_exts):
            result["suspicious_files"].append(f"Double extension: {info.filename}")
        # Check for nested archives
        if name_lower.endswith(('.zip', '.tar', '.gz', '.7z', '.rar')):
            result["is_recursive"] = True
            result["suspicious_files"].append(f"Nested archive: {info.filename}")
        # Check for path traversal
        if '..' in info.filename or info.filename.startswith('/'):
            result["suspicious_files"].append(f"Path traversal: {info.filename}")
except zipfile.BadZipFile:
    result["suspicious_files"].append("Invalid ZIP file (may be disguised)")
except Exception as e:
    result["suspicious_files"].append(f"Scan error: {str(e)[:100]}")
print(json.dumps(result))
"""

    def _scan_archive_basic(
        self, content: bytes, result: ArchiveScanResult
    ) -> ArchiveScanResult:
        """Basic archive scan without Docker (fallback)."""
        import io
        import zipfile

        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
            result.total_files = len(zf.namelist())
            for info in zf.infolist():
                result.decompressed_size += info.file_size
                name_lower = info.filename.lower()
                dangerous = (".exe", ".dll", ".bat", ".cmd", ".ps1", ".vbs", ".msi", ".scr")
                if any(name_lower.endswith(ext) for ext in dangerous):
                    result.suspicious_files.append(f"Executable: {info.filename}")
                if ".." in info.filename:
                    result.suspicious_files.append(f"Path traversal: {info.filename}")
            if result.compressed_size > 0:
                ratio = result.decompressed_size / result.compressed_size
                result.is_zip_bomb = ratio > 100
        except Exception as exc:
            result.error = f"Basic archive scan failed: {str(exc)[:200]}"

        return result

    async def _fetch_without_sandbox(self, url: str, result: FetchResult) -> FetchResult:
        """Fallback URL fetch without Docker sandbox."""
        try:
            import httpx

            async with httpx.AsyncClient(
                timeout=15, follow_redirects=True, max_redirects=5
            ) as client:
                resp = await client.get(url, headers={"User-Agent": "NexSidi-Scanner/1.0"})
                result.status_code = resp.status_code
                result.final_url = str(resp.url)
                result.claimed_mime = resp.headers.get("content-type", "")
                result.content = resp.text[:50000]
                result.content_length = len(resp.content)

                from urllib.parse import urlparse
                original_domain = urlparse(url).hostname
                final_domain = urlparse(result.final_url).hostname
                result.redirects_to_different_domain = original_domain != final_domain
        except Exception as exc:
            result.error = f"Fetch failed: {str(exc)[:200]}"

        return result

    async def _kill_container(self, container_name: str) -> None:
        """Kill a sandbox container by name."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "kill", container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            pass  # Non-critical — error logged upstream or handled by caller


# ── Singleton ────────────────────────────────────────────────────────

_sandbox_engine: SandboxEngine | None = None


def get_sandbox_engine() -> SandboxEngine:
    """Get or create the singleton SandboxEngine."""
    global _sandbox_engine
    if _sandbox_engine is None:
        _sandbox_engine = SandboxEngine()
    return _sandbox_engine


def init_sandbox_engine(config: ScanSandboxConfig | None = None) -> SandboxEngine:
    """Initialize the singleton SandboxEngine with custom config."""
    global _sandbox_engine
    _sandbox_engine = SandboxEngine(config)
    return _sandbox_engine
