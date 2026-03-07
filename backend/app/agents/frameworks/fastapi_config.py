"""FastAPI framework configuration for Shubham's code generation.

Rules, golden examples, and file structure for generating production-grade
FastAPI + SQLAlchemy + Pydantic v2 backends.
"""

from __future__ import annotations

from app.agents.frameworks import FrameworkConfig, register_framework

FASTAPI_RULES: tuple[str, ...] = (
    "1. Use SQLAlchemy 2.0 async with `mapped_column()` and `Mapped[]` type annotations — "
    "NEVER use legacy `Column()` syntax",
    "2. Use Pydantic v2 `BaseModel` with `model_config = ConfigDict(from_attributes=True)` — "
    "NEVER use Pydantic v1 `class Config` or `orm_mode`",
    "3. Use `Depends()` for dependency injection — database sessions, current user, "
    "permissions MUST be injected via `Depends()`",
    "4. Raise `HTTPException(status_code=..., detail=...)` for errors — "
    "NEVER return error dicts or use bare `raise`",
    "5. ALL route handlers and service methods MUST be `async def` — "
    "NEVER use synchronous `def` for I/O operations",
    "6. Use `Annotated[type, Depends(...)]` for type-safe dependency injection — "
    "import from `typing` and declare type aliases",
    "7. Password hashing: `bcrypt` via `passlib.context.CryptContext` — "
    "JWT: `PyJWT` with HS256 algorithm and expiration (NEVER use python-jose — CVE-2024-33663)",
    "8. Database sessions: use `async with AsyncSession() as session` — "
    "ALWAYS use `session.execute(select(...))` not `session.query()`",
    "9. Every model MUST define `__tablename__`, primary key with `mapped_column(BigInteger, primary_key=True)`, "
    "and `created_at`/`updated_at` timestamp columns",
    "10. Every router file: `router = APIRouter(prefix=\"/...\", tags=[\"...\"])` — "
    "NEVER define routes on the `app` object directly",
    "11. NEVER use 'pass', '# TODO', '...', or 'raise NotImplementedError' — "
    "every function must have a REAL, COMPLETE implementation",
    "12. NEVER invent import paths — use ONLY names from the contract and previously generated code",
    "13. Type hints on EVERY function (parameters + return types) — "
    "use `list[Model]`, `Model | None`, not `Optional`",
    "14. Output ONLY the code file — no markdown fences, no explanations, no comments about what to add later",
)

FASTAPI_GOLDEN_EXAMPLES: dict[str, str] = {
    "models": '''\
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
''',
    "schemas": '''\
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class UserBase(BaseModel):
    email: EmailStr
    full_name: str


class UserCreate(UserBase):
    password: str


class UserResponse(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
''',
    "routers": '''\
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import UserCreate, UserResponse
from app.security import get_current_user
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[UserResponse, Depends(get_current_user)]


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(user_in: UserCreate, db: DbSession) -> UserResponse:
    service = UserService(db)
    existing = await service.get_by_email(user_in.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    return await service.create(user_in)
''',
    "services": '''\
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.schemas import UserCreate
from app.security import hash_password


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_email(self, email: str) -> User | None:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def create(self, user_in: UserCreate) -> User:
        user = User(
            email=user_in.email,
            full_name=user_in.full_name,
            hashed_password=hash_password(user_in.password),
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user
''',
}

FASTAPI_FILE_STRUCTURE: dict[str, str] = {
    "models": "backend/app/models.py",
    "schemas": "backend/app/schemas.py",
    "security": "backend/app/security.py",
    "routers": "backend/app/routers/",
    "services": "backend/app/services/",
    "tests": "backend/tests/",
    "seed_db": "backend/scripts/seed.py",
    "database": "backend/app/database.py",
    "config": "backend/app/config.py",
    "main": "backend/app/main.py",
}


FASTAPI_CONFIG = FrameworkConfig(
    name="fastapi",
    display_name="FastAPI",
    language="python",
    code_block_lang="python",
    error_comment_prefix="#",
    file_structure=FASTAPI_FILE_STRUCTURE,
    rules=FASTAPI_RULES,
    golden_examples=FASTAPI_GOLDEN_EXAMPLES,
    # OCP-FIX: generation DAG moved here from shubham.py module-level dicts
    generation_order=(
        {"name": "models", "path": "backend/app/models.py", "task_type": "general",
         "description": "SQLAlchemy models from architecture contract tables"},
        {"name": "schemas", "path": "backend/app/schemas.py", "task_type": "general",
         "description": "Pydantic request/response schemas matching models"},
        {"name": "security", "path": "backend/app/security.py", "task_type": "auth_code",
         "description": "Auth middleware, password hashing, JWT handling"},
        {"name": "routers", "path": "backend/app/routers/", "task_type": "general",
         "description": "FastAPI route handlers using schemas and services"},
        {"name": "services", "path": "backend/app/services/", "task_type": "general",
         "description": "Business logic services called by routers"},
        {"name": "tests", "path": "backend/tests/", "task_type": "general",
         "description": "Pytest tests for all endpoints and services"},
        {"name": "seed_db", "path": "backend/scripts/seed.py", "task_type": "general",
         "description": "Database seed script for development"},
    ),
    dependency_graph={
        "models": set(),
        "schemas": {"models"},
        "security": {"models", "schemas"},
        "routers": {"models", "schemas", "security"},
        "services": {"models", "schemas", "security"},
        "tests": {"models", "schemas", "security", "routers", "services"},
        "seed_db": {"models"},
    },
)

register_framework(FASTAPI_CONFIG)
