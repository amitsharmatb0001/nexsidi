#!/usr/bin/env python3
"""Database management CLI for NexSidi v2.

All secrets (DB password, JWT, API keys) are fetched from GCP Secret Manager.
No .env file needed. Just set these env vars on your machine:

    GCP_PROJECT_ID=your-nexsidi-project   # or auto-detected on Cloud Run
    DB_HOST=10.x.x.x                      # Cloud SQL private IP
    USE_CLOUD_SECRETS=true                 # enables GCP Secret Manager

Usage:
    python scripts/db.py migrate          # Run all pending migrations
    python scripts/db.py migrate --sql    # Generate SQL without executing
    python scripts/db.py rollback         # Rollback one migration
    python scripts/db.py reset            # DROP everything and re-migrate (dev only)
    python scripts/db.py status           # Show current migration status
    python scripts/db.py flush-cache      # Clear all Valkey/Redis caches
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Add backend/ to Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def cmd_migrate(args: argparse.Namespace) -> None:
    """Run alembic upgrade head."""
    from alembic import command

    alembic_cfg = _get_alembic_config()

    if args.sql:
        print("[DB] Generating SQL (offline mode)...")
        command.upgrade(alembic_cfg, "head", sql=True)
    else:
        print("[DB] Running migrations...")
        command.upgrade(alembic_cfg, "head")
        print("[DB] Migrations complete.")


def cmd_rollback(args: argparse.Namespace) -> None:
    """Run alembic downgrade -1."""
    from alembic import command

    alembic_cfg = _get_alembic_config()
    steps = args.steps if hasattr(args, "steps") else 1
    print(f"[DB] Rolling back {steps} migration(s)...")
    command.downgrade(alembic_cfg, f"-{steps}")
    print("[DB] Rollback complete.")


def cmd_status(args: argparse.Namespace) -> None:
    """Show current migration status."""
    from alembic import command

    alembic_cfg = _get_alembic_config()
    command.current(alembic_cfg, verbose=True)
    print()
    command.history(alembic_cfg, verbose=False)


def cmd_reset(args: argparse.Namespace) -> None:
    """Full reset: downgrade to base, then upgrade to head. DEV ONLY."""
    from app.config import get_settings
    settings = get_settings()

    if settings.is_production:
        print("[ERROR] Cannot reset in production!")
        sys.exit(1)

    from alembic import command

    alembic_cfg = _get_alembic_config()

    print("[DB] Downgrading to base...")
    command.downgrade(alembic_cfg, "base")

    print("[DB] Upgrading to head...")
    command.upgrade(alembic_cfg, "head")

    print("[DB] Reset complete.")


def cmd_flush_cache(args: argparse.Namespace) -> None:
    """Flush all Valkey/Redis caches."""
    asyncio.run(_flush_cache())


async def _flush_cache() -> None:
    """Clear all keys matching nexsidi patterns in Valkey."""
    import redis.asyncio as aioredis
    from app.config import get_settings

    settings = get_settings()
    client = aioredis.from_url(
        settings.valkey_url,
        password=settings.valkey_password or None,
    )

    try:
        deleted = 0
        for pattern in ("ctx:*", "prompt:*"):
            keys = []
            async for key in client.scan_iter(pattern):
                keys.append(key)
            if keys:
                await client.delete(*keys)
                print(f"[CACHE] Deleted {len(keys)} keys matching {pattern}")
                deleted += len(keys)

        if deleted == 0:
            print("[CACHE] No cached keys found.")
        else:
            print(f"[CACHE] Total flushed: {deleted} keys")

    finally:
        await client.aclose()


def _get_alembic_config():
    """Get Alembic config pointing to the right directory."""
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parent.parent
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    return alembic_cfg


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NexSidi v2 database management\n\n"
                    "Secrets fetched from GCP Secret Manager automatically.\n"
                    "Set: GCP_PROJECT_ID + DB_HOST env vars.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # migrate
    migrate_p = subparsers.add_parser("migrate", help="Run pending migrations")
    migrate_p.add_argument("--sql", action="store_true", help="Generate SQL without executing")

    # rollback
    rollback_p = subparsers.add_parser("rollback", help="Rollback migrations")
    rollback_p.add_argument("--steps", type=int, default=1, help="Number of steps to rollback")

    # status
    subparsers.add_parser("status", help="Show migration status")

    # reset
    subparsers.add_parser("reset", help="Full reset (dev only)")

    # flush-cache
    subparsers.add_parser("flush-cache", help="Clear all Valkey caches")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "migrate": cmd_migrate,
        "rollback": cmd_rollback,
        "status": cmd_status,
        "reset": cmd_reset,
        "flush-cache": cmd_flush_cache,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
