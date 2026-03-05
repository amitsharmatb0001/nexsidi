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
        # F4-FIX: Detect simulation mode
        aarav_output = context.get("aarav", {})
        is_simulation = (
            isinstance(aarav_output, dict)
            and aarav_output.get("is_simulation_sandbox", False)
        )
        if is_simulation:
            context.setdefault("__simulation_mode__", True)
            logger.warning(
                "delivery_simulation_mode",
                pipeline_run_id=pipeline_run_id,
                msg="Project was NOT tested in a real Docker sandbox",
            )

        contract = context.get("vikram", {}).get("contract", {})
        project_name = contract.get("project_name", "project")

        manifest = DeliveryManifest(
            project_name=project_name,
            organization_id=context.get("__organization_id__", ""),
            pipeline_run_id=pipeline_run_id,
        )

        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            # 1. Code files (backend)
            backend_files = self._collect_backend_files(context)
            for path, content in backend_files.items():
                zf.writestr(f"{project_name}/code/{path}", content)
                manifest.backend_files += 1

            # 2. Code files (frontend)
            frontend_files = self._collect_frontend_files(context)
            for path, content in frontend_files.items():
                zf.writestr(f"{project_name}/code/{path}", content)
                manifest.frontend_files += 1

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

            # F4-FIX: Blocklist scan before packaging
            blocklist_findings = self._scan_for_dangerous_patterns(context)
            if blocklist_findings:
                blocklist_json = orjson.dumps(blocklist_findings, option=orjson.OPT_INDENT_2).decode("utf-8")
                zf.writestr(f"{project_name}/reports/blocklist_scan.json", blocklist_json)
                manifest.report_files += 1

            # 6. Manifest
            manifest.total_files = (
                manifest.backend_files + manifest.frontend_files
                + manifest.report_files + manifest.deploy_files + 1  # +1 for contract
            )
            manifest.agents_involved = [k for k in context if not k.startswith("__")]

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
