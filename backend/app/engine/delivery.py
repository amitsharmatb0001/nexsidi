"""Delivery Engine: assembles the final project delivery package.

After deployment succeeds (or user downloads), the Delivery Engine:
1. Collects all generated code (Shubham + Aanya outputs)
2. Collects all reports (Karan security, Navya logic, Deepika perf, compliance)
3. Collects deployment config + logs
4. Packages everything into a ZIP with a structured manifest
5. Generates a delivery summary for the user

The delivery package structure:
    project_name/
    ├── code/
    │   ├── backend/
    │   └── frontend/
    ├── reports/
    │   ├── security_report.json
    │   ├── logic_report.json
    │   ├── performance_report.json
    │   ├── compliance_report.json
    │   └── test_report.json
    ├── deploy/
    │   ├── config files (service.yaml / vercel.json / railway.toml)
    │   └── deploy_log.txt
    ├── docs/
    │   └── architecture_contract.json
    └── manifest.json
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import orjson
import structlog

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class DeliveryManifest:
    """Metadata about the delivery package."""

    project_name: str
    organization_id: str
    pipeline_run_id: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    total_files: int = 0
    backend_files: int = 0
    frontend_files: int = 0
    report_files: int = 0
    deploy_files: int = 0
    deployment_url: str = ""
    deployment_provider: str = ""
    agents_involved: list[str] = field(default_factory=list)
    pipeline_duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "organization_id": self.organization_id,
            "pipeline_run_id": self.pipeline_run_id,
            "created_at": self.created_at,
            "total_files": self.total_files,
            "backend_files": self.backend_files,
            "frontend_files": self.frontend_files,
            "report_files": self.report_files,
            "deploy_files": self.deploy_files,
            "deployment_url": self.deployment_url,
            "deployment_provider": self.deployment_provider,
            "agents_involved": self.agents_involved,
            "pipeline_duration_ms": self.pipeline_duration_ms,
        }


@dataclass(slots=True)
class DeliveryPackage:
    """The complete delivery output."""

    manifest: DeliveryManifest
    zip_bytes: bytes = b""
    summary: str = ""


class DeliveryEngine:
    """Assembles the final project delivery package.

    Called during the DELIVERY pipeline stage.
    """

    def build_package(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> DeliveryPackage:
        """Build the complete delivery ZIP from pipeline context.

        Args:
            pipeline_run_id: UUID of the pipeline run.
            context: Accumulated context from all pipeline stages.

        Returns:
            DeliveryPackage with ZIP bytes and manifest.

        F4-FIX: Surfaces simulation mode — when Docker was unavailable,
        tests were SKIPPED but previously appeared as passing. Now the
        delivery summary explicitly warns the customer.
        """
        # F4-FIX + DELIVERY-FIX: Detect ALL simulation modes (sandbox, deploy, git)
        aarav_output = context.get("aarav", {})
        pranav_output = context.get("pranav", {})
        git_output = context.get("git_agent", {})
        sim_sandbox = (
            isinstance(aarav_output, dict)
            and aarav_output.get("is_simulation_sandbox", False)
        )
        sim_deploy = (
            isinstance(pranav_output, dict)
            and pranav_output.get("is_simulation_deploy", False)
        )
        sim_git = (
            isinstance(git_output, dict)
            and git_output.get("is_simulation_git", False)
        )
        is_simulation = sim_sandbox or sim_deploy or sim_git
        if is_simulation:
            context.setdefault("__simulation_mode__", True)
            logger.warning(
                "delivery_simulation_mode",
                pipeline_run_id=pipeline_run_id,
                sandbox_simulated=sim_sandbox,
                deploy_simulated=sim_deploy,
                git_simulated=sim_git,
                msg="Project has simulated stages — NOT fully verified",
            )

        contract = context.get("vikram", {}).get("contract", {})
        project_name = contract.get("project_name", "project")

        manifest = DeliveryManifest(
            project_name=project_name,
            organization_id=context.get("__organization_id__", ""),
            pipeline_run_id=pipeline_run_id,
        )

        zip_buffer = io.BytesIO()

        # AUDIT-T1-7: File size caps to prevent LLM hallucination blowup
        _MAX_FILE_SIZE = 5 * 1024 * 1024   # 5MB per file
        _MAX_TOTAL_SIZE = 200 * 1024 * 1024  # 200MB total uncompressed
        _total_uncompressed = 0

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            # 1. Code files (backend)
            backend_files = self._collect_backend_files(context)
            for path, content in backend_files.items():
                if len(content) > _MAX_FILE_SIZE:
                    logger.warning("delivery_file_too_large", path=path, size=len(content))
                    content = content[:_MAX_FILE_SIZE] + "\n# ... TRUNCATED (exceeded 5MB limit) ...\n"
                _total_uncompressed += len(content)
                zf.writestr(f"{project_name}/code/{path}", content)
                manifest.backend_files += 1

            # 2. Code files (frontend)
            frontend_files = self._collect_frontend_files(context)
            for path, content in frontend_files.items():
                if len(content) > _MAX_FILE_SIZE:
                    logger.warning("delivery_file_too_large", path=path, size=len(content))
                    content = content[:_MAX_FILE_SIZE] + "\n// ... TRUNCATED (exceeded 5MB limit) ...\n"
                _total_uncompressed += len(content)
                zf.writestr(f"{project_name}/code/{path}", content)
                manifest.frontend_files += 1

            # FIX-14: Completeness validation — warn if critical files are missing.
            # Prevents customers from receiving empty or broken projects.
            all_paths = set(backend_files.keys()) | set(frontend_files.keys())
            incomplete_warnings: list[str] = []
            total_code = len(backend_files) + len(frontend_files)
            if total_code == 0:
                incomplete_warnings.append("No code files generated — project is empty")
            if not any("requirements.txt" in f or "package.json" in f for f in all_paths):
                incomplete_warnings.append("Missing dependency manifest (requirements.txt or package.json)")
            # V5-FIX (HIGH-11): Use basename comparison instead of substring.
            # The old ``"dockerfile" in f.lower()`` matched docs/dockerfile-guide.md,
            # scripts/check_dockerfile.sh, etc. — any path containing "dockerfile".
            # Only actual Dockerfiles should satisfy this check.
            import os as _os_delivery
            _dockerfile_names = {"dockerfile", "dockerfile.dev", "dockerfile.prod", "dockerfile.staging"}
            if not any(_os_delivery.path.basename(f).lower() in _dockerfile_names for f in all_paths):
                incomplete_warnings.append("Missing Dockerfile — app cannot be containerized")
            if not any(".env" in f for f in all_paths):
                incomplete_warnings.append("Missing .env.example — app cannot start without env config")

            if incomplete_warnings:
                warning_content = (
                    "# INCOMPLETE PROJECT WARNING\n\n"
                    "The following critical files are missing from this delivery:\n\n"
                    + "\n".join(f"- {w}" for w in incomplete_warnings)
                    + "\n\n## What to do\n\n"
                    "These files are required for the project to build and run.\n"
                    "Re-run the pipeline or manually create the missing files.\n"
                )
                zf.writestr(f"{project_name}/INCOMPLETE_PROJECT_WARNING.md", warning_content)
                logger.warning(
                    "delivery_incomplete_project",
                    pipeline_run_id=pipeline_run_id,
                    warnings=incomplete_warnings,
                    total_files=total_code,
                )

            # 3. Reports
            reports = self._collect_reports(context)
            for report_name, report_data in reports.items():
                report_json = orjson.dumps(report_data, option=orjson.OPT_INDENT_2).decode("utf-8")
                zf.writestr(f"{project_name}/reports/{report_name}", report_json)
                manifest.report_files += 1

            # 4. Deploy config + logs
            deploy_files = self._collect_deploy_files(context)
            for path, content in deploy_files.items():
                zf.writestr(f"{project_name}/deploy/{path}", content)
                manifest.deploy_files += 1

            # 5. Architecture contract
            if contract:
                contract_json = orjson.dumps(contract, option=orjson.OPT_INDENT_2).decode("utf-8")
                zf.writestr(f"{project_name}/docs/architecture_contract.json", contract_json)

            # 5b. Docs agent output (README, API docs, user guide)
            docs_output = context.get("docs_agent", {})
            if isinstance(docs_output, dict):
                readme = docs_output.get("readme") or docs_output.get("README")
                if readme:
                    zf.writestr(f"{project_name}/README.md", readme)
                    manifest.total_files = getattr(manifest, "total_files", 0)  # will be set later
                api_docs = docs_output.get("api_docs") or docs_output.get("api_documentation")
                if api_docs:
                    zf.writestr(f"{project_name}/docs/api.md", api_docs)
                user_guide = docs_output.get("user_guide")
                if user_guide:
                    zf.writestr(f"{project_name}/docs/user_guide.md", user_guide)

            # F4-FIX + HIGH-7 FIX: Blocklist scan before packaging.
            # HIGH-7: Now BLOCKS delivery when dangerous patterns found,
            # instead of just logging a warning and handing malicious code
            # to the customer. This is the last safety gate.
            blocklist_findings = self._scan_for_dangerous_patterns(context)
            if blocklist_findings:
                blocklist_json = orjson.dumps(blocklist_findings, option=orjson.OPT_INDENT_2).decode("utf-8")
                zf.writestr(f"{project_name}/reports/blocklist_scan.json", blocklist_json)
                manifest.report_files += 1

                # HIGH-7 FIX: Block delivery — dangerous code must not reach customers.
                finding_summary = ", ".join(
                    f"{f['file']}:{f.get('line', '?')} ({f.get('description', 'dangerous pattern')[:60]})"
                    for f in blocklist_findings[:5]
                )
                raise RuntimeError(
                    f"DELIVERY BLOCKED: {len(blocklist_findings)} dangerous pattern(s) "
                    f"detected in generated code. First findings: {finding_summary}. "
                    f"Review reports/blocklist_scan.json. Route back to Fixer."
                )

            # DELIVERY-FIX: Add prominent warning file when stages were simulated
            if is_simulation:
                sim_parts = []
                if sim_sandbox:
                    sim_parts.append("- **Testing**: Docker sandbox was NOT available. All tests were SIMULATED (no real code execution).")
                if sim_deploy:
                    sim_parts.append("- **Deployment**: No cloud provider token was available. Deployment was SIMULATED.")
                if sim_git:
                    sim_parts.append("- **Git**: Git operations were SIMULATED.")
                sim_warning = (
                    "# ⚠️ SIMULATION WARNING\n\n"
                    "**This project was delivered with simulated stages.**\n\n"
                    "The following stages did NOT execute against real infrastructure:\n\n"
                    + "\n".join(sim_parts) + "\n\n"
                    "## What This Means\n\n"
                    "The generated code has NOT been verified to work in a real environment.\n"
                    "Before using this code in production:\n\n"
                    "1. Run `docker-compose up` locally and verify all services start\n"
                    "2. Run the test suite: `make test`\n"
                    "3. Verify frontend connects to backend\n"
                    "4. Test all API endpoints manually\n\n"
                    "## Why Did This Happen?\n\n"
                    "The NexSidi pipeline requires Docker for sandbox testing and cloud provider\n"
                    "credentials for deployment. When these are unavailable, the pipeline completes\n"
                    "with simulated results to deliver your code, but it has NOT been tested.\n"
                )
                zf.writestr(f"{project_name}/SIMULATION_WARNING.md", sim_warning)

            # 6. Manifest
            manifest.total_files = (
                manifest.backend_files + manifest.frontend_files
                + manifest.report_files + manifest.deploy_files + 1  # +1 for contract
            )
            # AUDIT-T3-15: Validate agent names against alphanumeric+underscore to prevent injection
            # V5-FIX (MEDIUM-6): Allow digits in agent names (e.g. ``gemini3_agent``).
            # The old pattern ``r'^[a-z_]+$'`` rejected any name containing digits.
            import re as _re_delivery
            manifest.agents_involved = [
                k for k in context
                if not k.startswith("__") and _re_delivery.match(r'^[a-z0-9_]+$', k)
            ]

            # Deployment info
            pranav_output = context.get("pranav", {})
            if isinstance(pranav_output, dict):
                manifest.deployment_url = pranav_output.get("deployment_url", "")
                manifest.deployment_provider = pranav_output.get("provider", "")

            manifest_json = orjson.dumps(manifest.to_dict(), option=orjson.OPT_INDENT_2).decode("utf-8")
            zf.writestr(f"{project_name}/manifest.json", manifest_json)

        zip_bytes = zip_buffer.getvalue()

        # Generate human-readable summary
        summary = self._generate_summary(
            manifest, reports,
            is_simulation=is_simulation,
            blocklist_findings=blocklist_findings,
        )

        logger.info(
            "delivery_package_built",
            project=project_name,
            total_files=manifest.total_files,
            zip_size_kb=len(zip_bytes) // 1024,
            deployment_url=manifest.deployment_url,
        )

        return DeliveryPackage(
            manifest=manifest,
            zip_bytes=zip_bytes,
            summary=summary,
        )

    # ── File Collection ────────────────────────────────────────────

    def _collect_backend_files(self, context: dict[str, Any]) -> dict[str, str]:
        """Collect backend code from Shubham's output."""
        shubham = context.get("shubham", {})
        if isinstance(shubham, dict):
            return shubham.get("file_contents", {})
        return {}

    def _collect_frontend_files(self, context: dict[str, Any]) -> dict[str, str]:
        """Collect frontend code from Aanya's output."""
        aanya = context.get("aanya", {})
        if isinstance(aanya, dict):
            return aanya.get("file_contents", {})
        return {}

    def _collect_reports(self, context: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Collect all quality/security/compliance reports."""
        reports: dict[str, dict[str, Any]] = {}

        # Karan (security)
        karan = context.get("karan", {})
        if isinstance(karan, dict) and karan:
            reports["security_report.json"] = karan

        # Navya (logic)
        navya = context.get("navya", {})
        if isinstance(navya, dict) and navya:
            reports["logic_report.json"] = navya

        # Deepika (performance)
        deepika = context.get("deepika", {})
        if isinstance(deepika, dict) and deepika:
            reports["performance_report.json"] = deepika

        # Aarav (test results)
        aarav = context.get("aarav", {})
        if isinstance(aarav, dict) and aarav:
            reports["test_report.json"] = aarav

        # Compliance (from karan's compliance run or standalone)
        compliance = context.get("compliance", {})
        if isinstance(compliance, dict) and compliance:
            reports["compliance_report.json"] = compliance

        # Fixer (fix attempts)
        fixer = context.get("fixer", {})
        if isinstance(fixer, dict) and fixer:
            reports["fixer_report.json"] = fixer

        return reports

    def _collect_deploy_files(self, context: dict[str, Any]) -> dict[str, str]:
        """Collect deployment config files and logs."""
        files: dict[str, str] = {}

        pranav = context.get("pranav", {})
        if isinstance(pranav, dict):
            # Config files (service.yaml, vercel.json, railway.toml)
            config_files = pranav.get("config_files", {})
            for name, content in config_files.items():
                files[name] = content

            # Deploy log
            deploy_log = pranav.get("deploy_log", "")
            if deploy_log:
                files["deploy_log.txt"] = deploy_log

            # Build log
            build_log = pranav.get("build_log", "")
            if build_log:
                files["build_log.txt"] = build_log

        return files

    # ── Summary ───────────────────────────────────────────────────

    def _generate_summary(
        self,
        manifest: DeliveryManifest,
        reports: dict[str, dict[str, Any]],
        *,
        is_simulation: bool = False,
        blocklist_findings: list[dict[str, Any]] | None = None,
    ) -> str:
        """Generate a human-readable delivery summary."""
        lines = [
            f"# Delivery Summary: {manifest.project_name}",
            f"Pipeline Run: {manifest.pipeline_run_id}",
            f"Delivered: {manifest.created_at}",
            "",
        ]

        # F4-FIX: Simulation mode warning
        if is_simulation:
            lines.extend([
                "## ⚠ SIMULATION MODE",
                "**This project was NOT tested in a real Docker sandbox.**",
                "Docker was unavailable during the pipeline run. All sandbox tests",
                "were SKIPPED. Treat test results as unverified. Deploy to a staging",
                "environment and run integration tests before going to production.",
                "",
            ])

        # F12-FIX: Blocklist findings
        if blocklist_findings:
            lines.extend([
                "## ⚠ BLOCKLIST FINDINGS",
                f"**{len(blocklist_findings)} dangerous pattern(s) detected in generated code.**",
                "Review `reports/blocklist_scan.json` before deploying.",
                "",
            ])

        lines.extend([
            "## Files",
            f"- Backend: {manifest.backend_files} files",
            f"- Frontend: {manifest.frontend_files} files",
            f"- Reports: {manifest.report_files} files",
            f"- Deploy Config: {manifest.deploy_files} files",
            f"- Total: {manifest.total_files} files",
            "",
        ])

        if manifest.deployment_url:
            lines.extend([
                "## Deployment",
                f"- Provider: {manifest.deployment_provider}",
                f"- URL: {manifest.deployment_url}",
                "",
            ])

        # Report summaries
        security = reports.get("security_report.json", {})
        if security:
            lines.extend([
                "## Security",
                f"- Total findings: {security.get('total_findings', 'N/A')}",
                f"- Critical: {security.get('critical_count', 0)}",
                f"- High: {security.get('high_count', 0)}",
                "",
            ])

        test = reports.get("test_report.json", {})
        if test:
            lines.extend([
                "## Testing",
                f"- Total tests: {test.get('total_tests', 'N/A')}",
                f"- Passed: {test.get('total_passed', 'N/A')}",
                f"- Failed: {test.get('total_failed', 0)}",
                "",
            ])

        lines.extend([
            "## Agents",
            f"- Pipeline agents: {', '.join(manifest.agents_involved)}",
        ])

        return "\n".join(lines)

    # ── F12-FIX: Blocklist Scan ──────────────────────────────────

    # Dangerous patterns that should NEVER appear in generated code.
    _BLOCKLIST_PATTERNS: list[tuple[str, str]] = [
        (r"\bos\.system\s*\(", "os.system() — use subprocess with shell=False"),
        (r"\beval\s*\(", "eval() — arbitrary code execution risk"),
        (r"\bexec\s*\(", "exec() — arbitrary code execution risk"),
        (r"subprocess\..*shell\s*=\s*True", "subprocess with shell=True — command injection risk"),
        (r"pickle\.loads?\s*\(", "pickle.load/loads — arbitrary code execution via deserialization"),
        (r"\b__import__\s*\(", "__import__() — dynamic import, potential RCE"),
        (r"(?:password|secret|api_key|token)\s*=\s*['\"][^'\"]{8,}['\"]",
         "Hardcoded credential — use environment variables"),
        (r"yaml\.load\s*\([^)]*\)", "yaml.load without SafeLoader — arbitrary code execution"),
        (r"marshal\.loads?\s*\(", "marshal.load/loads — arbitrary code execution"),
        (r"compile\s*\([^)]*,\s*['\"]exec['\"]", "compile() with exec mode — code injection risk"),
    ]

    def _scan_for_dangerous_patterns(
        self, context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """F12-FIX: Scan all generated code for dangerous patterns before packaging.

        Returns a list of findings, each with file path, line number, pattern matched,
        and the recommendation.
        """
        import re

        findings: list[dict[str, Any]] = []

        # Collect all code files
        all_files: dict[str, str] = {}
        shubham = context.get("shubham", {})
        if isinstance(shubham, dict):
            all_files.update(shubham.get("file_contents", {}))
        aanya = context.get("aanya", {})
        if isinstance(aanya, dict):
            all_files.update(aanya.get("file_contents", {}))

        for file_path, content in all_files.items():
            if not isinstance(content, str):
                continue
            for line_num, line in enumerate(content.splitlines(), 1):
                for pattern, description in self._BLOCKLIST_PATTERNS:
                    if re.search(pattern, line):
                        findings.append({
                            "file": file_path,
                            "line": line_num,
                            "pattern": description,
                            "snippet": line.strip()[:120],
                        })

        if findings:
            logger.warning(
                "blocklist_scan_findings",
                total=len(findings),
                files=list({f["file"] for f in findings}),
            )
        else:
            logger.info("blocklist_scan_clean", files_scanned=len(all_files))

        return findings


# ── Singleton ───────────────────────────────────────────────────

_engine: DeliveryEngine | None = None


def get_delivery_engine() -> DeliveryEngine:
    """Get or create the delivery engine singleton."""
    global _engine
    if _engine is None:
        _engine = DeliveryEngine()
    return _engine
