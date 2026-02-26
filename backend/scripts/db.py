#!/usr/bin/env python3
"""Database management CLI for NexSidi v2.

Usage:
    python scripts/db.py migrate          # Run all pending migrations
    python scripts/db.py migrate --sql    # Generate SQL without executing
    python scripts/db.py rollback         # Rollback one migration
    python scripts/db.py seed             # Seed initial data (org, admin user)
    python scripts/db.py reset            # DROP everything and re-migrate (dev only)
    python scripts/db.py status           # Show current migration status
    python scripts/db.py flush-cache      # Clear all Valkey/Redis caches

Requires DATABASE_ADMIN_URL or DATABASE_URL environment variable.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

# Add backend/ to Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def cmd_migrate(args: argparse.Namespace) -> None:
    """Run alembic upgrade head."""
    from alembic.config import Config
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
    from alembic.config import Config
    from alembic import command

    alembic_cfg = _get_alembic_config()
    steps = args.steps if hasattr(args, "steps") else 1
    print(f"[DB] Rolling back {steps} migration(s)...")
    command.downgrade(alembic_cfg, f"-{steps}")
    print("[DB] Rollback complete.")


def cmd_status(args: argparse.Namespace) -> None:
    """Show current migration status."""
    from alembic.config import Config
    from alembic import command

    alembic_cfg = _get_alembic_config()
    command.current(alembic_cfg, verbose=True)
    print()
    command.history(alembic_cfg, verbose=False)


def cmd_seed(args: argparse.Namespace) -> None:
    """Seed initial data: default organization and admin user."""
    asyncio.run(_seed_data())


async def _seed_data() -> None:
    """Create initial organization and admin user for development."""
    from app.config import get_settings
    from app.database import setup_database
    from app.services.auth import hash_password

    settings = get_settings()
    engine, session_factory = setup_database(settings)

    async with session_factory() as session:
        async with session.begin():
            from sqlalchemy import text, select
            from app.models.core import Organization
            from app.models.auth import User

            # Check if data already exists
            result = await session.execute(select(Organization).limit(1))
            if result.scalar_one_or_none():
                print("[SEED] Data already exists. Skipping.")
                return

            org_id = uuid.uuid4()
            user_id = uuid.uuid4()

            # Create default organization
            org = Organization(
                id=org_id,
                name="NexSidi",
                slug="nexsidi",
                plan="enterprise",
                settings={"max_projects": 100, "max_users": 50},
            )
            session.add(org)

            # Create admin user
            admin = User(
                id=user_id,
                organization_id=org_id,
                email="admin@nexsidi.com",
                name="NexSidi Admin",
                role="super_admin",
                password_hash=hash_password("change-me-immediately"),
                auth_provider="email",
                is_active=True,
            )
            session.add(admin)

            # Create feature flags
            from app.models.auth import FeatureFlag
            for flag_key, desc in [
                ("enable_phone_otp", "Phone OTP authentication"),
                ("enable_totp", "TOTP two-factor authentication"),
                ("enable_passkeys", "WebAuthn/passkey authentication"),
                ("enable_whatsapp", "WhatsApp bot integration"),
                ("enable_gemini_fallback", "Auto-fallback to Gemini when Claude is unavailable"),
            ]:
                session.add(FeatureFlag(
                    flag_key=flag_key,
                    enabled=False,
                    description=desc,
                ))

            print(f"[SEED] Organization: {org.name} ({org_id})")
            print(f"[SEED] Admin user: {admin.email} ({user_id})")
            print(f"[SEED] Feature flags: 5 created (all disabled)")
            print("[SEED] Done. Change the admin password immediately!")

    await engine.dispose()


def cmd_reset(args: argparse.Namespace) -> None:
    """Full reset: downgrade to base, then upgrade to head. DEV ONLY."""
    from app.config import get_settings
    settings = get_settings()

    if settings.is_production:
        print("[ERROR] Cannot reset in production!")
        sys.exit(1)

    from alembic.config import Config
    from alembic import command

    alembic_cfg = _get_alembic_config()

    print("[DB] Downgrading to base...")
    command.downgrade(alembic_cfg, "base")

    print("[DB] Upgrading to head...")
    command.upgrade(alembic_cfg, "head")

    print("[DB] Reset complete.")

    if args.seed:
        print("[DB] Seeding data...")
        asyncio.run(_seed_data())


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
        # Flush context engine keys (ctx:*)
        ctx_keys = []
        async for key in client.scan_iter("ctx:*"):
            ctx_keys.append(key)
        if ctx_keys:
            await client.delete(*ctx_keys)
            print(f"[CACHE] Deleted {len(ctx_keys)} context engine keys")

        # Flush prompt cache keys (prompt:*)
        prompt_keys = []
        async for key in client.scan_iter("prompt:*"):
            prompt_keys.append(key)
        if prompt_keys:
            await client.delete(*prompt_keys)
            print(f"[CACHE] Deleted {len(prompt_keys)} prompt cache keys")

        if not ctx_keys and not prompt_keys:
            print("[CACHE] No cached keys found.")
        else:
            print(f"[CACHE] Total flushed: {len(ctx_keys) + len(prompt_keys)} keys")

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
        description="NexSidi v2 database management",
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

    # seed
    subparsers.add_parser("seed", help="Seed initial data")

    # reset
    reset_p = subparsers.add_parser("reset", help="Full reset (dev only)")
    reset_p.add_argument("--seed", action="store_true", help="Also seed after reset")

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
        "seed": cmd_seed,
        "reset": cmd_reset,
        "flush-cache": cmd_flush_cache,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
