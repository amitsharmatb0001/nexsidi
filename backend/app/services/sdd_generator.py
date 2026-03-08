"""SDD Generator — converts Vikram's JSON contract to human-readable Markdown.

Phase 5A: Software Design Document (SDD) generation for checkpoint review.
Produces a structured Markdown document with sections for:
- Executive Summary
- Tech Stack
- Database Schema (ER description)
- API Endpoints (with examples)
- Frontend Pages
- Security Model
- Compliance Requirements

The SDD is attached to CHECKPOINT_DESIGN for user review and approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class SDDSection:
    """A single section of the SDD document."""

    title: str
    content: str
    order: int = 0


@dataclass(slots=True)
class SDDDocument:
    """Complete Software Design Document."""

    markdown: str = ""
    sections: list[SDDSection] = field(default_factory=list)
    version: str = "1.0"
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "markdown": self.markdown,
            "sections": [
                {"title": s.title, "content": s.content, "order": s.order}
                for s in self.sections
            ],
            "version": self.version,
            "generated_at": self.generated_at,
        }


class SDDGenerator:
    """Converts Vikram's architecture contract to a human-readable SDD."""

    def generate_sdd(
        self,
        contract: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> SDDDocument:
        """Generate a complete SDD from the architecture contract.

        Args:
            contract: Vikram's JSON architecture contract.
            context: Optional pipeline context for additional sections.

        Returns:
            SDDDocument with Markdown content and structured sections.
        """
        context = context or {}
        sections: list[SDDSection] = []

        # 1. Executive Summary
        sections.append(self._generate_executive_summary(contract, context))

        # 2. Tech Stack
        sections.append(self._generate_tech_stack(contract))

        # 3. Database Schema
        sections.append(self._generate_database_schema(contract))

        # 4. API Endpoints
        sections.append(self._generate_api_endpoints(contract))

        # 5. Frontend Pages
        sections.append(self._generate_frontend_pages(contract))

        # 6. Security Model
        sections.append(self._generate_security_model(contract, context))

        # 7. Compliance
        sections.append(self._generate_compliance(contract, context))

        # Build full Markdown
        markdown_parts = [
            f"# Software Design Document: {contract.get('project_name', 'Untitled Project')}",
            "",
            f"**Version:** 1.0  ",
            f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ",
            f"**Status:** Pending Review",
            "",
            "---",
            "",
        ]

        for section in sections:
            markdown_parts.append(section.content)
            markdown_parts.append("")

        markdown = "\n".join(markdown_parts)

        doc = SDDDocument(
            markdown=markdown,
            sections=sections,
        )

        logger.info(
            "sdd_generated",
            project=contract.get("project_name", "unknown"),
            sections=len(sections),
            markdown_length=len(markdown),
        )

        return doc

    def _generate_executive_summary(
        self,
        contract: dict[str, Any],
        context: dict[str, Any],
    ) -> SDDSection:
        """Section 1: Executive Summary."""
        project_name = contract.get("project_name", "Untitled")
        description = contract.get("description", "No description provided.")

        # Extract stats
        tables = len(contract.get("database", {}).get("tables", []))
        endpoints = len(contract.get("api", {}).get("endpoints", []))
        pages = len(contract.get("frontend", {}).get("pages", []))

        # Complexity from Saanvi
        saanvi = context.get("saanvi", {})
        complexity = saanvi.get("complexity_tier", "unknown") if isinstance(saanvi, dict) else "unknown"

        content = (
            f"## 1. Executive Summary\n\n"
            f"**Project:** {project_name}  \n"
            f"**Description:** {description}  \n"
            f"**Complexity:** {complexity}  \n\n"
            f"### Scope\n\n"
            f"| Metric | Count |\n"
            f"|--------|-------|\n"
            f"| Database Tables | {tables} |\n"
            f"| API Endpoints | {endpoints} |\n"
            f"| Frontend Pages | {pages} |\n"
        )

        return SDDSection(title="Executive Summary", content=content, order=1)

    def _generate_tech_stack(self, contract: dict[str, Any]) -> SDDSection:
        """Section 2: Technology Stack."""
        stack = contract.get("tech_stack", {})
        if not isinstance(stack, dict):
            stack = {}

        rows = []
        for layer, tech in stack.items():
            if isinstance(tech, str):
                rows.append(f"| {layer.replace('_', ' ').title()} | {tech} |")
            elif isinstance(tech, list):
                rows.append(f"| {layer.replace('_', ' ').title()} | {', '.join(str(t) for t in tech)} |")

        table = "\n".join(rows) if rows else "| (not specified) | - |"

        content = (
            f"## 2. Technology Stack\n\n"
            f"| Layer | Technology |\n"
            f"|-------|------------|\n"
            f"{table}\n"
        )

        return SDDSection(title="Tech Stack", content=content, order=2)

    def _generate_database_schema(self, contract: dict[str, Any]) -> SDDSection:
        """Section 3: Database Schema."""
        tables = contract.get("database", {}).get("tables", [])

        parts = ["## 3. Database Schema\n"]

        if not tables:
            parts.append("*No database tables defined.*\n")
        else:
            for table in tables:
                if not isinstance(table, dict):
                    continue
                name = table.get("name", "unnamed")
                parts.append(f"### Table: `{name}`\n")

                columns = table.get("columns", [])
                if columns:
                    parts.append("| Column | Type | Constraints |")
                    parts.append("|--------|------|-------------|")
                    for col in columns:
                        if not isinstance(col, dict):
                            continue
                        col_name = col.get("name", "?")
                        col_type = col.get("type", "?")
                        constraints = []
                        if col.get("primary_key"):
                            constraints.append("PK")
                        if col.get("nullable") is False:
                            constraints.append("NOT NULL")
                        if col.get("unique"):
                            constraints.append("UNIQUE")
                        if col.get("foreign_key"):
                            constraints.append(f"FK -> {col['foreign_key']}")
                        parts.append(f"| {col_name} | {col_type} | {', '.join(constraints) or '-'} |")
                    parts.append("")

                # Relationships
                rels = table.get("relationships", [])
                if rels:
                    parts.append("**Relationships:**")
                    for rel in rels:
                        if isinstance(rel, dict):
                            parts.append(f"- {rel.get('type', '?')}: `{rel.get('target', '?')}`")
                        elif isinstance(rel, str):
                            parts.append(f"- {rel}")
                    parts.append("")

        return SDDSection(title="Database Schema", content="\n".join(parts), order=3)

    def _generate_api_endpoints(self, contract: dict[str, Any]) -> SDDSection:
        """Section 4: API Endpoints."""
        endpoints = contract.get("api", {}).get("endpoints", [])

        parts = ["## 4. API Endpoints\n"]

        if not endpoints:
            parts.append("*No API endpoints defined.*\n")
        else:
            parts.append("| Method | Path | Description | Auth |")
            parts.append("|--------|------|-------------|------|")
            for ep in endpoints:
                if not isinstance(ep, dict):
                    continue
                method = ep.get("method", "GET")
                path = ep.get("path", "/")
                desc = ep.get("description", "-")[:60]
                auth = "Yes" if ep.get("auth_required", True) else "No"
                parts.append(f"| `{method}` | `{path}` | {desc} | {auth} |")
            parts.append("")

            # Detailed endpoint descriptions
            for ep in endpoints[:10]:  # Cap detail section
                if not isinstance(ep, dict):
                    continue
                method = ep.get("method", "GET")
                path = ep.get("path", "/")
                parts.append(f"### `{method} {path}`\n")
                if ep.get("description"):
                    parts.append(f"{ep['description']}\n")

                # Request body
                req_body = ep.get("request_body", {})
                if req_body and isinstance(req_body, dict):
                    parts.append("**Request Body:**")
                    parts.append("```json")
                    import json
                    parts.append(json.dumps(req_body, indent=2)[:500])
                    parts.append("```\n")

                # Response
                resp = ep.get("response", {})
                if resp and isinstance(resp, dict):
                    parts.append("**Response:**")
                    parts.append("```json")
                    parts.append(json.dumps(resp, indent=2)[:500])
                    parts.append("```\n")

        return SDDSection(title="API Endpoints", content="\n".join(parts), order=4)

    def _generate_frontend_pages(self, contract: dict[str, Any]) -> SDDSection:
        """Section 5: Frontend Pages."""
        pages = contract.get("frontend", {}).get("pages", [])

        parts = ["## 5. Frontend Pages\n"]

        if not pages:
            parts.append("*No frontend pages defined.*\n")
        else:
            parts.append("| Page | Route | Components |")
            parts.append("|------|-------|------------|")
            for page in pages:
                if not isinstance(page, dict):
                    continue
                name = page.get("name", "Unnamed")
                route = page.get("route", page.get("path", "/"))
                components = page.get("components", [])
                comp_str = ", ".join(str(c) for c in components[:5]) if components else "-"
                parts.append(f"| {name} | `{route}` | {comp_str} |")
            parts.append("")

        return SDDSection(title="Frontend Pages", content="\n".join(parts), order=5)

    def _generate_security_model(
        self,
        contract: dict[str, Any],
        context: dict[str, Any],
    ) -> SDDSection:
        """Section 6: Security Model."""
        auth = contract.get("auth", contract.get("authentication", {}))
        if not isinstance(auth, dict):
            auth = {}

        parts = ["## 6. Security Model\n"]

        auth_method = auth.get("method", auth.get("type", "JWT"))
        parts.append(f"**Authentication:** {auth_method}  ")

        if auth.get("mfa"):
            parts.append("**MFA:** Enabled  ")
        if auth.get("oauth_providers"):
            parts.append(f"**OAuth Providers:** {', '.join(auth['oauth_providers'])}  ")

        parts.append("")

        # Security features from Tilotma
        tilotma = context.get("tilotma", {})
        compliance = tilotma.get("compliance_auto_detected", []) if isinstance(tilotma, dict) else []
        if compliance:
            parts.append("### Compliance Requirements\n")
            for flag in compliance:
                parts.append(f"- {flag}")
            parts.append("")

        parts.append("### Security Checklist\n")
        parts.append("- [ ] Input validation on all endpoints")
        parts.append("- [ ] SQL injection prevention (parameterized queries)")
        parts.append("- [ ] XSS prevention (output encoding)")
        parts.append("- [ ] CSRF protection (token validation)")
        parts.append("- [ ] Rate limiting on auth endpoints")
        parts.append("- [ ] Secure password hashing (bcrypt/argon2)")
        parts.append("- [ ] JWT token expiry and refresh flow")
        parts.append("")

        return SDDSection(title="Security Model", content="\n".join(parts), order=6)

    def _generate_compliance(
        self,
        contract: dict[str, Any],
        context: dict[str, Any],
    ) -> SDDSection:
        """Section 7: Compliance & Deployment."""
        parts = ["## 7. Compliance & Deployment\n"]

        # Deployment from contract
        deploy = contract.get("deployment", {})
        if isinstance(deploy, dict) and deploy:
            parts.append("### Deployment Configuration\n")
            for key, value in deploy.items():
                parts.append(f"- **{key.replace('_', ' ').title()}:** {value}")
            parts.append("")

        # Saanvi's complexity analysis
        saanvi = context.get("saanvi", {})
        if isinstance(saanvi, dict) and saanvi.get("complexity_tier"):
            parts.append("### Complexity Analysis\n")
            parts.append(f"- **Tier:** {saanvi['complexity_tier']}")
            if saanvi.get("estimated_hours"):
                parts.append(f"- **Estimated Hours:** {saanvi['estimated_hours']}")
            parts.append("")

        parts.append("---\n")
        parts.append("*This SDD was auto-generated by NexSidi. Review all sections before approving.*")

        return SDDSection(title="Compliance", content="\n".join(parts), order=7)


# ── Singleton ────────────────────────────────────────────────────────

_generator: SDDGenerator | None = None


def get_sdd_generator() -> SDDGenerator:
    """Return the SDDGenerator singleton."""
    global _generator
    if _generator is None:
        _generator = SDDGenerator()
    return _generator
