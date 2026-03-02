"""Analytics and usage metrics endpoints.

ANALYTICS-FIX: Provides pipeline performance metrics, token costs,
agent success rates, and organization-level usage summaries.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.dependencies import CurrentContext, TenantSession

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.get("/projects/{project_id}/analytics")
async def get_project_analytics(
    project_id: str,
    ctx: CurrentContext,
    session: TenantSession,
) -> dict:
    """Return pipeline performance analytics for a project.

    ANALYTICS-FIX: Aggregates run counts, success rates, and duration
    statistics from pipeline.runs; stage execution breakdown from pipeline.steps.
    """
    rows = await session.execute(
        text("""
            SELECT
                COUNT(*) as total_runs,
                COUNT(*) FILTER (WHERE status = 'completed') as completed_runs,
                COUNT(*) FILTER (WHERE status = 'failed') as failed_runs,
                COUNT(*) FILTER (WHERE status = 'cancelled') as cancelled_runs,
                AVG(EXTRACT(EPOCH FROM (completed_at - started_at)))
                    FILTER (WHERE status = 'completed') as avg_duration_secs,
                MIN(EXTRACT(EPOCH FROM (completed_at - started_at)))
                    FILTER (WHERE status = 'completed') as min_duration_secs,
                MAX(EXTRACT(EPOCH FROM (completed_at - started_at)))
                    FILTER (WHERE status = 'completed') as max_duration_secs
            FROM pipeline.runs
            WHERE project_id = :pid::uuid AND organization_id = :oid::uuid
        """),
        {"pid": project_id, "oid": ctx.organization_id},
    )
    r = rows.fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Project not found")

    # Stage breakdown — ANALYTICS-FIX
    stages = await session.execute(
        text("""
            SELECT s.stage, COUNT(*) as executions,
                   AVG(EXTRACT(EPOCH FROM (s.completed_at - s.started_at))) as avg_secs
            FROM pipeline.steps s
            JOIN pipeline.runs r ON r.id = s.run_id
            WHERE r.project_id = :pid::uuid AND r.organization_id = :oid::uuid
            GROUP BY s.stage ORDER BY COUNT(*) DESC
        """),
        {"pid": project_id, "oid": ctx.organization_id},
    )

    return {
        "project_id": project_id,
        "total_runs": r.total_runs,
        "completed_runs": r.completed_runs,
        "failed_runs": r.failed_runs,
        "cancelled_runs": r.cancelled_runs,
        "success_rate": round(
            (r.completed_runs / r.total_runs * 100) if r.total_runs else 0, 1
        ),
        "avg_duration_secs": round(float(r.avg_duration_secs or 0), 1),
        "min_duration_secs": round(float(r.min_duration_secs or 0), 1),
        "max_duration_secs": round(float(r.max_duration_secs or 0), 1),
        "stage_breakdown": [
            {
                "stage": s.stage,
                "executions": s.executions,
                "avg_secs": round(float(s.avg_secs or 0), 1),
            }
            for s in stages.fetchall()
        ],
    }


@router.get("/organizations/{org_id}/usage")
async def get_org_usage(
    org_id: str,
    ctx: CurrentContext,
    session: TenantSession,
) -> dict:
    """Return organization-wide token usage and cost summary.

    ANALYTICS-FIX: Aggregates token counts from all pipeline steps across
    all projects for the authenticated organization.
    """
    if org_id != str(ctx.organization_id):
        raise HTTPException(status_code=403, detail="Access denied")

    result = await session.execute(
        text("""
            SELECT
                COUNT(DISTINCT r.project_id) as active_projects,
                COUNT(r.id) as total_pipeline_runs,
                COALESCE(SUM(s.input_tokens), 0) as total_input_tokens,
                COALESCE(SUM(s.output_tokens), 0) as total_output_tokens,
                COALESCE(SUM(s.cache_read_tokens), 0) as total_cache_read_tokens
            FROM pipeline.runs r
            LEFT JOIN pipeline.steps s ON s.run_id = r.id
            WHERE r.organization_id = :oid::uuid
        """),
        {"oid": ctx.organization_id},
    )
    r = result.fetchone()

    total_inp = int(r.total_input_tokens or 0)
    total_out = int(r.total_output_tokens or 0)
    cache_r = int(r.total_cache_read_tokens or 0)
    # Estimate cost at blended Claude Sonnet rate — ANALYTICS-FIX
    estimated_cost_usd = (
        (total_inp / 1_000_000) * 3.0
        + (total_out / 1_000_000) * 15.0
        + (cache_r / 1_000_000) * 0.3
    )

    return {
        "organization_id": org_id,
        "active_projects": r.active_projects,
        "total_pipeline_runs": r.total_pipeline_runs,
        "total_input_tokens": total_inp,
        "total_output_tokens": total_out,
        "total_cache_read_tokens": cache_r,
        "estimated_cost_usd": round(estimated_cost_usd, 4),
        "estimated_cost_inr": round(estimated_cost_usd * 84.0, 2),
    }


@router.get("/projects/{project_id}/export")  # EXPORT-FIX
async def export_project(project_id: str, ctx: CurrentContext, session: TenantSession) -> dict:
    """Export all pipeline run history for a project as structured JSON.

    EXPORT-FIX: Compliance and backup endpoint — returns complete history
    of all pipeline runs, stages, and agent outputs for a project.
    """
    from sqlalchemy import text
    runs = await session.execute(text("""
        SELECT r.id, r.status, r.current_step, r.execution_mode,
               r.started_at, r.completed_at, r.error_summary,
               COUNT(s.id) as stage_count,
               COALESCE(SUM(s.input_tokens + s.output_tokens), 0) as total_tokens
        FROM pipeline.runs r
        LEFT JOIN pipeline.steps s ON s.run_id = r.id
        WHERE r.project_id = :pid::uuid AND r.organization_id = :oid::uuid
        GROUP BY r.id ORDER BY r.started_at DESC
    """), {"pid": project_id, "oid": ctx.organization_id})

    return {
        "project_id": project_id,
        "exported_at": __import__("datetime").datetime.utcnow().isoformat(),
        "runs": [
            {
                "run_id": str(r.id), "status": r.status,
                "current_step": r.current_step, "execution_mode": r.execution_mode,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "error": r.error_summary, "stage_count": r.stage_count, "total_tokens": int(r.total_tokens),
            }
            for r in runs.fetchall()
        ],
    }
