"""Flask framework configuration for Shubham's code generation.

Rules, golden examples, and file structure for generating production-grade
Flask + SQLAlchemy + Marshmallow + Flask-Migrate backends.
"""

from __future__ import annotations

from app.agents.frameworks import FrameworkConfig, register_framework

FLASK_RULES: tuple[str, ...] = (
    "1. Use SQLAlchemy 2.0 with Flask-SQLAlchemy — define models with `db.Model` base class, "
    "use `mapped_column()` and `Mapped[]` type annotations, NEVER use legacy `Column()` syntax",
    "2. Use Marshmallow for serialization/validation — NEVER use Pydantic. "
    "Use `ma.SQLAlchemyAutoSchema` or `ma.Schema` with `class Meta: model = ...; load_instance = True`",
    "3. Use Flask Blueprints for route grouping — "
    "`bp = Blueprint('users', __name__, url_prefix='/api/users')` then `@bp.route()`",
    "4. Use the application factory pattern — define `create_app()` in `app/__init__.py` "
    "that initializes extensions, registers blueprints, and returns the Flask app",
    "5. Use Flask-Login for session authentication — "
    "implement `UserMixin`, configure `login_manager.user_loader`, "
    "protect routes with `@login_required`",
    "6. Use Flask-Migrate (Alembic) for database migrations — "
    "initialize with `Migrate(app, db)`, NEVER call `db.create_all()` in production",
    "7. Password hashing: use `werkzeug.security.generate_password_hash` and "
    "`check_password_hash` — NEVER store plaintext passwords or use raw hashlib",
    "8. Use `abort(status_code)` or raise `HTTPException` for errors — "
    "NEVER return bare error dicts without proper status codes",
    "9. Every model MUST define `__tablename__`, a primary key with "
    "`mapped_column(Integer, primary_key=True)`, and `created_at`/`updated_at` timestamp columns",
    "10. Use WTForms via Flask-WTF for HTML form validation — "
    "define form classes inheriting from `FlaskForm`, use CSRF protection with `CSRFProtect(app)`",
    "11. NEVER use 'pass', '# TODO', '...', or 'raise NotImplementedError' — "
    "every function must have a REAL, COMPLETE implementation",
    "12. NEVER invent import paths — use ONLY names from the contract and previously generated code",
    "13. Type hints on EVERY function (parameters + return types) — "
    "use `list[Model]`, `Model | None`, not `Optional`",
    "14. Output ONLY the code file — no markdown fences, no explanations, no comments about what to add later",
)

FLASK_GOLDEN_EXAMPLES: dict[str, str] = {
    "models": '''\
from __future__ import annotations

from datetime import datetime

from app.extensions import db
from sqlalchemy import Integer, String, DateTime, Text, Boolean, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship


class User(db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    products: Mapped[list[Product]] = relationship("Product", back_populates="owner", lazy="select")

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"


class Product(db.Model):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped[User] = relationship("User", back_populates="products", lazy="select")
''',
    "schemas": '''\
from __future__ import annotations

from app.extensions import ma
from app.models import User, Product
from marshmallow import fields, validate, post_load


class UserSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = User
        load_instance = True
        exclude = ("hashed_password",)
        dump_only = ("id", "created_at", "updated_at")

    email = fields.Email(required=True)
    full_name = fields.String(required=True, validate=validate.Length(min=1, max=255))


class UserCreateSchema(ma.Schema):
    email = fields.Email(required=True)
    full_name = fields.String(required=True, validate=validate.Length(min=1, max=255))
    password = fields.String(required=True, validate=validate.Length(min=8, max=128), load_only=True)


class ProductSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = Product
        load_instance = True
        include_fk = True
        dump_only = ("id", "created_at", "updated_at")

    name = fields.String(required=True, validate=validate.Length(min=1, max=255))
    price = fields.Integer(required=True, validate=validate.Range(min=0))
    owner_id = fields.Integer(required=True)


user_schema = UserSchema()
users_schema = UserSchema(many=True)
user_create_schema = UserCreateSchema()
product_schema = ProductSchema()
products_schema = ProductSchema(many=True)
''',
    "routes": '''\
from __future__ import annotations

from flask import Blueprint, jsonify, request, abort
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import User
from app.schemas import user_schema, users_schema, user_create_schema

bp = Blueprint("users", __name__, url_prefix="/api/users")


@bp.route("/", methods=["GET"])
def list_users() -> tuple[dict, int]:
    users = db.session.execute(db.select(User).order_by(User.created_at.desc())).scalars().all()
    return jsonify(users_schema.dump(users)), 200


@bp.route("/", methods=["POST"])
def create_user() -> tuple[dict, int]:
    json_data = request.get_json()
    if not json_data:
        abort(400, description="No input data provided")

    errors = user_create_schema.validate(json_data)
    if errors:
        return jsonify({"errors": errors}), 422

    data = user_create_schema.load(json_data)
    existing = db.session.execute(db.select(User).where(User.email == data["email"])).scalar_one_or_none()
    if existing:
        abort(409, description="Email already registered")

    user = User(
        email=data["email"],
        full_name=data["full_name"],
        hashed_password=generate_password_hash(data["password"]),
    )
    db.session.add(user)
    db.session.commit()
    return jsonify(user_schema.dump(user)), 201


@bp.route("/<int:user_id>", methods=["GET"])
def get_user(user_id: int) -> tuple[dict, int]:
    user = db.session.get(User, user_id)
    if user is None:
        abort(404, description="User not found")
    return jsonify(user_schema.dump(user)), 200
''',
    "services": '''\
from __future__ import annotations

from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db
from app.models import User


class UserService:
    @staticmethod
    def get_by_email(email: str) -> User | None:
        return db.session.execute(
            db.select(User).where(User.email == email)
        ).scalar_one_or_none()

    @staticmethod
    def get_by_id(user_id: int) -> User | None:
        return db.session.get(User, user_id)

    @staticmethod
    def create(email: str, full_name: str, password: str) -> User:
        user = User(
            email=email,
            full_name=full_name,
            hashed_password=generate_password_hash(password),
        )
        db.session.add(user)
        db.session.commit()
        return user

    @staticmethod
    def authenticate(email: str, password: str) -> User | None:
        user = UserService.get_by_email(email)
        if user is None:
            return None
        if not check_password_hash(user.hashed_password, password):
            return None
        return user

    @staticmethod
    def update(user: User, **kwargs: str) -> User:
        for key, value in kwargs.items():
            if key == "password":
                user.hashed_password = generate_password_hash(value)
            elif hasattr(user, key):
                setattr(user, key, value)
        db.session.commit()
        return user
''',
}

FLASK_FILE_STRUCTURE: dict[str, str] = {
    "models": "backend/app/models.py",
    "schemas": "backend/app/schemas.py",
    "extensions": "backend/app/extensions.py",
    "routes": "backend/app/routes/",
    "services": "backend/app/services/",
    "tests": "backend/tests/",
    "seed_db": "backend/scripts/seed.py",
    "config": "backend/app/config.py",
    "app_init": "backend/app/__init__.py",
}


FLASK_CONFIG = FrameworkConfig(
    name="flask",
    display_name="Flask",
    language="python",
    code_block_lang="python",
    error_comment_prefix="#",
    file_structure=FLASK_FILE_STRUCTURE,
    rules=FLASK_RULES,
    golden_examples=FLASK_GOLDEN_EXAMPLES,
)

register_framework(FLASK_CONFIG)
