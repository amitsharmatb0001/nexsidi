"""Template marketplace — pre-built project starter templates.

TEMPLATE-FIX: Provides curated starting templates so users don't need
to write requirements from scratch. Each template pre-fills tech_stack,
requirements, and deployment target.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from fastapi import APIRouter
from app.dependencies import CurrentContext

router = APIRouter()

@dataclass(frozen=True)
class ProjectTemplate:
    id: str
    name: str
    description: str
    category: str  # saas | ecommerce | blog | mobile | api | dashboard
    icon: str  # emoji
    tech_stack: dict[str, str]
    requirements: str
    deployment_target: str
    estimated_files: int
    tags: list[str] = field(default_factory=list)

TEMPLATES: dict[str, ProjectTemplate] = {t.id: t for t in [
    ProjectTemplate(
        id="saas-fastapi-nextjs",
        name="SaaS Starter",
        description="Full-stack SaaS with auth, subscriptions, dashboard, and multi-tenancy",
        category="saas", icon="🚀",
        tech_stack={"backend": "fastapi", "frontend": "nextjs", "database": "postgresql", "auth": "jwt", "payments": "stripe"},
        requirements="Build a multi-tenant SaaS application with user registration, email verification, JWT authentication, role-based access control (admin/member), Stripe subscription billing with free/pro/enterprise tiers, project management dashboard, usage analytics, team invitations, and audit logs.",
        deployment_target="railway",
        estimated_files=45,
        tags=["saas", "fullstack", "payments", "auth"],
    ),
    ProjectTemplate(
        id="ecommerce-nextjs",
        name="E-Commerce Store",
        description="Full e-commerce with product catalog, cart, checkout, and order management",
        category="ecommerce", icon="🛒",
        tech_stack={"backend": "fastapi", "frontend": "nextjs", "database": "postgresql", "payments": "stripe", "search": "elasticsearch"},
        requirements="Build a complete e-commerce platform with product catalog (categories, variants, images), shopping cart, Stripe checkout, order management, inventory tracking, customer accounts, order history, admin dashboard, product search with filters, and email notifications.",
        deployment_target="vercel",
        estimated_files=60,
        tags=["ecommerce", "payments", "fullstack"],
    ),
    ProjectTemplate(
        id="blog-cms",
        name="Blog / CMS",
        description="Content management system with rich text editor, SEO, and comments",
        category="blog", icon="✍️",
        tech_stack={"backend": "fastapi", "frontend": "nextjs", "database": "postgresql"},
        requirements="Build a blog/CMS platform with rich text editor, markdown support, SEO optimization (meta tags, sitemap, RSS), categories and tags, comments with moderation, author profiles, scheduled publishing, image upload with CDN, and an admin panel.",
        deployment_target="netlify",
        estimated_files=35,
        tags=["blog", "cms", "seo"],
    ),
    ProjectTemplate(
        id="api-only-fastapi",
        name="REST API",
        description="Production-ready REST API with auth, rate limiting, and OpenAPI docs",
        category="api", icon="⚡",
        tech_stack={"backend": "fastapi", "database": "postgresql", "cache": "redis"},
        requirements="Build a production-ready REST API with JWT authentication, rate limiting per API key, request validation, OpenAPI documentation, versioned endpoints (v1/v2), pagination, filtering, sorting, webhook support, and comprehensive error handling.",
        deployment_target="gcp_cloud_run",
        estimated_files=25,
        tags=["api", "backend", "rest"],
    ),
    ProjectTemplate(
        id="admin-dashboard",
        name="Admin Dashboard",
        description="Data-rich admin panel with charts, tables, and user management",
        category="dashboard", icon="📊",
        tech_stack={"backend": "fastapi", "frontend": "react", "database": "postgresql"},
        requirements="Build an admin dashboard with user management (CRUD, roles, permissions), data tables with search/filter/sort/export, charts (line, bar, pie) for analytics, activity feed, notification center, bulk actions, CSV export, and dark/light mode.",
        deployment_target="railway",
        estimated_files=40,
        tags=["dashboard", "admin", "analytics"],
    ),
    ProjectTemplate(
        id="mobile-react-native",
        name="Mobile App",
        description="Cross-platform mobile app with React Native + FastAPI backend",
        category="mobile", icon="📱",
        tech_stack={"backend": "fastapi", "frontend": "react_native", "database": "postgresql", "push": "firebase"},
        requirements="Build a cross-platform mobile app (iOS + Android) with React Native, push notifications via Firebase, offline mode with local storage sync, JWT auth with biometric login, user profiles, in-app navigation, and a FastAPI backend with real-time WebSocket support.",
        deployment_target="railway",
        estimated_files=55,
        tags=["mobile", "react-native", "ios", "android"],
    ),
    ProjectTemplate(
        id="realtime-chat",
        name="Real-Time Chat",
        description="Slack-like messaging with channels, DMs, and file sharing",
        category="saas", icon="💬",
        tech_stack={"backend": "fastapi", "frontend": "nextjs", "database": "postgresql", "realtime": "websockets"},
        requirements="Build a real-time chat application with channels (public/private), direct messages, file and image sharing, message reactions/threads, user presence indicators, read receipts, push notifications, message search, and a REST API for integrations.",
        deployment_target="railway",
        estimated_files=50,
        tags=["chat", "realtime", "websockets"],
    ),
    ProjectTemplate(
        id="ai-saas",
        name="AI SaaS Tool",
        description="AI-powered SaaS with LLM integration, prompt management, and usage billing",
        category="saas", icon="🤖",
        tech_stack={"backend": "fastapi", "frontend": "nextjs", "database": "postgresql", "ai": "anthropic", "payments": "stripe"},
        requirements="Build an AI-powered SaaS tool with Anthropic Claude integration, prompt template management, conversation history, token usage tracking, usage-based billing via Stripe, API key management for B2B, rate limiting, output streaming, and a clean chat interface.",
        deployment_target="gcp_cloud_run",
        estimated_files=45,
        tags=["ai", "saas", "llm", "payments"],
    ),
]}


@router.get("")  # TEMPLATE-FIX
async def list_templates(category: str | None = None, ctx: CurrentContext = None) -> dict:
    """List all available project templates, optionally filtered by category."""
    templates = list(TEMPLATES.values())
    if category:
        templates = [t for t in templates if t.category == category]
    return {
        "templates": [
            {
                "id": t.id, "name": t.name, "description": t.description,
                "category": t.category, "icon": t.icon, "tags": t.tags,
                "tech_stack": t.tech_stack, "deployment_target": t.deployment_target,
                "estimated_files": t.estimated_files,
            }
            for t in templates
        ],
        "total": len(templates),
        "categories": sorted({t.category for t in TEMPLATES.values()}),
    }


@router.get("/{template_id}")  # TEMPLATE-FIX
async def get_template(template_id: str) -> dict:
    """Get full template details including pre-filled requirements."""
    t = TEMPLATES.get(template_id)
    if not t:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    return {
        "id": t.id, "name": t.name, "description": t.description,
        "category": t.category, "icon": t.icon, "tags": t.tags,
        "tech_stack": t.tech_stack, "requirements": t.requirements,
        "deployment_target": t.deployment_target, "estimated_files": t.estimated_files,
        "usage_hint": f"POST /api/v1/pipeline/start with tech_stack={t.tech_stack} and requirements from this template",
    }
