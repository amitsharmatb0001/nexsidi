"""Security Template Library: per-framework security middleware/config templates.

Gap 223-242: Agents use these templates to inject security features into every
generated app. Each template is a real, copy-paste-ready code snippet that
configures a specific security layer (CORS, CSP, rate limiting, auth, etc.)
for a specific backend framework.

Supported frameworks:
- FastAPI (Python)
- Express (Node.js / TypeScript)
- Django (Python)

Security categories:
- cors: Cross-Origin Resource Sharing configuration
- csp: Content Security Policy headers
- rate_limit: Request rate limiting / throttling
- input_validation: Schema-based input sanitization
- auth: Authentication guards (JWT, session, etc.)
- session: Secure session configuration
- file_upload: Upload size / type restrictions
- password_hash: Password hashing (bcrypt, argon2)
- rbac: Role-based access control
- headers: Security headers (HSTS, X-Frame-Options, etc.)
- csrf: Cross-Site Request Forgery protection
- encryption: Data encryption at rest / in transit
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import structlog

logger = structlog.get_logger(__name__)


# ── Security Category Constants ──────────────────────────────────

VALID_CATEGORIES: Final[frozenset[str]] = frozenset({
    "cors",
    "csp",
    "rate_limit",
    "input_validation",
    "auth",
    "session",
    "file_upload",
    "password_hash",
    "rbac",
    "headers",
    "csrf",
    "encryption",
})


# ── Security Template Dataclass ──────────────────────────────────


@dataclass(frozen=True, slots=True)
class SecurityTemplate:
    """A single security middleware/config template for a framework.

    Attributes:
        name: Human-readable template identifier.
        category: Security category (must be one of VALID_CATEGORIES).
        framework: Target framework (e.g., "fastapi", "express", "django").
        code: The actual code snippet — real, copy-paste-ready.
        description: What this template does and why it matters.
    """

    name: str
    category: str
    framework: str
    code: str
    description: str

    def __post_init__(self) -> None:
        if self.category not in VALID_CATEGORIES:
            raise ValueError(
                f"Invalid category '{self.category}'. "
                f"Must be one of: {sorted(VALID_CATEGORIES)}"
            )


# ── Security Checklist ───────────────────────────────────────────

SECURITY_CHECKLIST: Final[dict[str, str]] = {
    "cors": "CORS must restrict origins to known frontend domains; never use wildcard in production.",
    "csp": "Content Security Policy must be set to prevent XSS and data injection attacks.",
    "rate_limit": "Rate limiting must be applied to auth endpoints and public APIs to prevent brute-force.",
    "input_validation": "All user input must be validated with a schema library before processing.",
    "auth": "Authentication must be enforced on all non-public endpoints using JWT or session tokens.",
    "session": "Sessions must use secure, httponly, samesite cookies with short expiry.",
    "file_upload": "File uploads must enforce size limits, allowed MIME types, and filename sanitization.",
    "password_hash": "Passwords must be hashed with bcrypt (cost >= 12) or argon2id; never store plaintext.",
    "rbac": "Role-based access control must gate sensitive operations by user role.",
    "headers": "Security headers (HSTS, X-Content-Type-Options, X-Frame-Options) must be set on all responses.",
    "csrf": "CSRF protection must be enabled for all state-changing endpoints using tokens or SameSite cookies.",
    "encryption": "Sensitive data must be encrypted at rest (AES-256) and in transit (TLS 1.2+).",
}


# ── FastAPI Templates ────────────────────────────────────────────

_FASTAPI_TEMPLATES: Final[list[SecurityTemplate]] = [
    SecurityTemplate(
        name="FastAPI CORS Configuration",
        category="cors",
        framework="fastapi",
        description="Configures CORS middleware with explicit origin allowlist. Rejects wildcard origins in production.",
        code="""\
from fastapi.middleware.cors import CORSMiddleware

ALLOWED_ORIGINS = [
    "https://yourdomain.com",
    "https://app.yourdomain.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)""",
    ),
    SecurityTemplate(
        name="FastAPI Rate Limiting Middleware",
        category="rate_limit",
        framework="fastapi",
        description="Sliding-window rate limiter using Redis. Blocks excessive requests with 429 status.",
        code="""\
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
import redis.asyncio as redis

_redis = redis.from_url("redis://localhost:6379/0")

class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host
        key = f"rate:{client_ip}"
        count = await _redis.incr(key)
        if count == 1:
            await _redis.expire(key, 60)
        if count > 100:
            raise HTTPException(status_code=429, detail="Too many requests")
        return await call_next(request)""",
    ),
    SecurityTemplate(
        name="FastAPI Pydantic Input Validation",
        category="input_validation",
        framework="fastapi",
        description="Pydantic model with constrained fields for strict input validation and sanitization.",
        code="""\
from pydantic import BaseModel, Field, EmailStr, field_validator
import re

class UserCreateRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=12, max_length=128)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not re.search(r"[A-Z]", v) or not re.search(r"[0-9]", v):
            raise ValueError("Password must contain uppercase letter and digit")
        return v""",
    ),
    SecurityTemplate(
        name="FastAPI Security Headers Middleware",
        category="headers",
        framework="fastapi",
        description="Injects OWASP-recommended security headers on every response.",
        code="""\
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response""",
    ),
    SecurityTemplate(
        name="FastAPI CSRF Protection",
        category="csrf",
        framework="fastapi",
        description="Double-submit cookie CSRF protection for state-changing endpoints.",
        code="""\
import secrets
from fastapi import Request, HTTPException, Response

CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"

def set_csrf_cookie(response: Response) -> str:
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE, token, httponly=False, samesite="strict", secure=True,
    )
    return token

def verify_csrf(request: Request) -> None:
    cookie_token = request.cookies.get(CSRF_COOKIE)
    header_token = request.headers.get(CSRF_HEADER)
    if not cookie_token or not header_token or cookie_token != header_token:
        raise HTTPException(status_code=403, detail="CSRF token mismatch")""",
    ),
    SecurityTemplate(
        name="FastAPI bcrypt Password Hashing",
        category="password_hash",
        framework="fastapi",
        description="Bcrypt password hashing with configurable cost factor. Never stores plaintext.",
        code="""\
import bcrypt

BCRYPT_ROUNDS = 12

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))""",
    ),
    SecurityTemplate(
        name="FastAPI JWT Auth Guard",
        category="auth",
        framework="fastapi",
        description="JWT bearer token dependency that protects endpoints. Verifies signature and expiry.",
        code="""\
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

SECRET_KEY = "CHANGE_ME"  # load from env
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

security = HTTPBearer()

def create_access_token(sub: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": sub, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(cred: HTTPAuthorizationCredentials = Depends(security)) -> str:
    try:
        payload = jwt.decode(cred.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return payload["sub"]
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")""",
    ),
    SecurityTemplate(
        name="FastAPI File Upload Validation",
        category="file_upload",
        framework="fastapi",
        description="Validates uploaded files for size, MIME type, and filename before processing.",
        code="""\
from fastapi import UploadFile, HTTPException
import os

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
BLOCKED_EXTENSIONS = {".exe", ".bat", ".sh", ".cmd", ".ps1", ".dll"}

async def validate_upload(file: UploadFile) -> UploadFile:
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"File type {file.content_type} not allowed")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext in BLOCKED_EXTENSIONS:
        raise HTTPException(400, f"Extension {ext} is blocked")
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, "File exceeds 10 MB limit")
    await file.seek(0)
    return file""",
    ),
]


# ── Express Templates ────────────────────────────────────────────

_EXPRESS_TEMPLATES: Final[list[SecurityTemplate]] = [
    SecurityTemplate(
        name="Express CORS Configuration",
        category="cors",
        framework="express",
        description="Configures the cors package with an explicit origin allowlist and credentials support.",
        code="""\
import cors from 'cors';

const ALLOWED_ORIGINS = [
  'https://yourdomain.com',
  'https://app.yourdomain.com',
];

app.use(cors({
  origin: ALLOWED_ORIGINS,
  credentials: true,
  methods: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'],
  allowedHeaders: ['Authorization', 'Content-Type'],
  maxAge: 600,
}));""",
    ),
    SecurityTemplate(
        name="Express Helmet Security Headers",
        category="headers",
        framework="express",
        description="Applies helmet middleware for HSTS, CSP, X-Frame-Options, and other security headers.",
        code="""\
import helmet from 'helmet';

app.use(helmet({
  contentSecurityPolicy: {
    directives: {
      defaultSrc: ["'self'"],
      scriptSrc: ["'self'"],
      styleSrc: ["'self'", "'unsafe-inline'"],
      imgSrc: ["'self'", 'data:', 'https:'],
      frameAncestors: ["'none'"],
    },
  },
  hsts: { maxAge: 63072000, includeSubDomains: true },
  referrerPolicy: { policy: 'strict-origin-when-cross-origin' },
}));""",
    ),
    SecurityTemplate(
        name="Express Rate Limiter",
        category="rate_limit",
        framework="express",
        description="Applies express-rate-limit with a 15-minute sliding window and 429 response.",
        code="""\
import rateLimit from 'express-rate-limit';

const limiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 100,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many requests, please try again later.' },
});

app.use('/api/', limiter);

const authLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 10,
  message: { error: 'Too many login attempts, try again later.' },
});

app.use('/api/auth/', authLimiter);""",
    ),
    SecurityTemplate(
        name="Express Zod Input Validation",
        category="input_validation",
        framework="express",
        description="Zod schema validation middleware that rejects invalid payloads with 400 errors.",
        code="""\
import { z, ZodSchema } from 'zod';
import { Request, Response, NextFunction } from 'express';

const UserCreateSchema = z.object({
  email: z.string().email(),
  username: z.string().min(3).max(32).regex(/^[a-zA-Z0-9_]+$/),
  password: z.string().min(12).max(128),
});

function validate(schema: ZodSchema) {
  return (req: Request, res: Response, next: NextFunction) => {
    const result = schema.safeParse(req.body);
    if (!result.success) {
      return res.status(400).json({ errors: result.error.flatten().fieldErrors });
    }
    req.body = result.data;
    next();
  };
}""",
    ),
    SecurityTemplate(
        name="Express CSRF Protection",
        category="csrf",
        framework="express",
        description="CSRF protection using the csrf-csrf double-submit cookie pattern.",
        code="""\
import { doubleCsrf } from 'csrf-csrf';
import cookieParser from 'cookie-parser';

app.use(cookieParser());

const { doubleCsrfProtection, generateToken } = doubleCsrf({
  getSecret: () => process.env.CSRF_SECRET!,
  cookieName: '__Host-csrf',
  cookieOptions: { sameSite: 'strict', secure: true, httpOnly: true },
  getTokenFromRequest: (req) => req.headers['x-csrf-token'] as string,
});

app.use(doubleCsrfProtection);
app.get('/api/csrf-token', (req, res) => {
  res.json({ token: generateToken(req, res) });
});""",
    ),
    SecurityTemplate(
        name="Express bcrypt Password Hashing",
        category="password_hash",
        framework="express",
        description="Bcrypt password hashing with salt rounds configured for production security.",
        code="""\
import bcrypt from 'bcryptjs';

const SALT_ROUNDS = 12;

async function hashPassword(plain: string): Promise<string> {
  return bcrypt.hash(plain, SALT_ROUNDS);
}

async function verifyPassword(plain: string, hash: string): Promise<boolean> {
  return bcrypt.compare(plain, hash);
}""",
    ),
    SecurityTemplate(
        name="Express JWT Auth Middleware",
        category="auth",
        framework="express",
        description="JWT verification middleware that protects routes and attaches the user to the request.",
        code="""\
import jwt from 'jsonwebtoken';
import { Request, Response, NextFunction } from 'express';

const JWT_SECRET = process.env.JWT_SECRET!;

interface AuthRequest extends Request {
  userId?: string;
}

function authMiddleware(req: AuthRequest, res: Response, next: NextFunction) {
  const header = req.headers.authorization;
  if (!header?.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Missing bearer token' });
  }
  try {
    const payload = jwt.verify(header.slice(7), JWT_SECRET) as { sub: string };
    req.userId = payload.sub;
    next();
  } catch {
    return res.status(401).json({ error: 'Invalid or expired token' });
  }
}""",
    ),
    SecurityTemplate(
        name="Express Multer File Upload Limits",
        category="file_upload",
        framework="express",
        description="Multer configuration with file size limits, MIME type filtering, and safe storage.",
        code="""\
import multer from 'multer';
import path from 'path';

const ALLOWED_MIMES = ['image/jpeg', 'image/png', 'image/webp', 'application/pdf'];
const MAX_SIZE = 10 * 1024 * 1024; // 10 MB

const upload = multer({
  storage: multer.diskStorage({
    destination: './uploads',
    filename: (_req, file, cb) => {
      const safe = file.originalname.replace(/[^a-zA-Z0-9._-]/g, '_');
      cb(null, `${Date.now()}-${safe}`);
    },
  }),
  limits: { fileSize: MAX_SIZE },
  fileFilter: (_req, file, cb) => {
    if (!ALLOWED_MIMES.includes(file.mimetype)) {
      return cb(new Error(`File type ${file.mimetype} not allowed`));
    }
    cb(null, true);
  },
});""",
    ),
]


# ── Django Templates ─────────────────────────────────────────────

_DJANGO_TEMPLATES: Final[list[SecurityTemplate]] = [
    SecurityTemplate(
        name="Django CORS Configuration",
        category="cors",
        framework="django",
        description="django-cors-headers settings with explicit origin allowlist for production.",
        code="""\
# settings.py
INSTALLED_APPS += ["corsheaders"]

MIDDLEWARE.insert(
    MIDDLEWARE.index("django.middleware.common.CommonMiddleware"),
    "corsheaders.middleware.CorsMiddleware",
)

CORS_ALLOWED_ORIGINS = [
    "https://yourdomain.com",
    "https://app.yourdomain.com",
]
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = ["authorization", "content-type", "x-csrf-token"]""",
    ),
    SecurityTemplate(
        name="Django CSP Configuration",
        category="csp",
        framework="django",
        description="django-csp middleware with strict Content Security Policy directives.",
        code="""\
# settings.py  (pip install django-csp)
MIDDLEWARE += ["csp.middleware.CSPMiddleware"]

CSP_DEFAULT_SRC = ("'self'",)
CSP_SCRIPT_SRC = ("'self'",)
CSP_STYLE_SRC = ("'self'", "'unsafe-inline'")
CSP_IMG_SRC = ("'self'", "data:", "https:")
CSP_FONT_SRC = ("'self'", "https://fonts.gstatic.com")
CSP_FRAME_ANCESTORS = ("'none'",)
CSP_FORM_ACTION = ("'self'",)
CSP_BASE_URI = ("'self'",)""",
    ),
    SecurityTemplate(
        name="Django Rate Limiting",
        category="rate_limit",
        framework="django",
        description="django-ratelimit decorator to throttle views per IP with cache backend.",
        code="""\
# views.py  (pip install django-ratelimit)
from django_ratelimit.decorators import ratelimit
from django.http import JsonResponse

@ratelimit(key='ip', rate='100/h', method='ALL', block=True)
def api_view(request):
    return JsonResponse({"status": "ok"})

@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def login_view(request):
    # ... authentication logic
    return JsonResponse({"status": "authenticated"})""",
    ),
    SecurityTemplate(
        name="Django CSRF Middleware",
        category="csrf",
        framework="django",
        description="Django built-in CSRF protection settings for session and API endpoints.",
        code="""\
# settings.py
MIDDLEWARE += ["django.middleware.csrf.CsrfViewMiddleware"]

CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Strict"
CSRF_TRUSTED_ORIGINS = [
    "https://yourdomain.com",
    "https://app.yourdomain.com",
]
CSRF_USE_SESSIONS = False  # use cookie-based CSRF for SPA compatibility""",
    ),
    SecurityTemplate(
        name="Django Password Validation",
        category="password_hash",
        framework="django",
        description="Password validators enforcing length, complexity, and common-password rejection.",
        code="""\
# settings.py
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]""",
    ),
    SecurityTemplate(
        name="Django Session Security",
        category="session",
        framework="django",
        description="Secure session cookie configuration with short expiry and strict SameSite.",
        code="""\
# settings.py
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
SESSION_COOKIE_AGE = 3600  # 1 hour
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_SAVE_EVERY_REQUEST = True""",
    ),
    SecurityTemplate(
        name="Django File Upload Validators",
        category="file_upload",
        framework="django",
        description="Custom validators for upload size, MIME type, and extension safety.",
        code="""\
from django.core.exceptions import ValidationError

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB

def validate_file_extension(upload):
    import os
    ext = os.path.splitext(upload.name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(f"Extension '{ext}' not allowed.")

def validate_file_size(upload):
    if upload.size > MAX_UPLOAD_SIZE:
        raise ValidationError(f"File exceeds {MAX_UPLOAD_SIZE // (1024*1024)} MB limit.")

# models.py usage:
# document = models.FileField(validators=[validate_file_extension, validate_file_size])""",
    ),
]


# ── Template Registry ────────────────────────────────────────────

_SECURITY_TEMPLATES: dict[str, list[SecurityTemplate]] = {
    "fastapi": list(_FASTAPI_TEMPLATES),
    "express": list(_EXPRESS_TEMPLATES),
    "django": list(_DJANGO_TEMPLATES),
}


# ── Helper Functions ─────────────────────────────────────────────


def get_security_templates(framework: str) -> list[SecurityTemplate]:
    """Return all security templates for a given framework.

    Args:
        framework: Framework name (e.g., "fastapi", "express", "django").

    Returns:
        List of SecurityTemplate objects. Empty list if framework is unknown.
    """
    return list(_SECURITY_TEMPLATES.get(framework, []))


def get_template_by_category(
    framework: str, category: str
) -> SecurityTemplate | None:
    """Return the first security template matching framework + category.

    Args:
        framework: Framework name.
        category: Security category (e.g., "cors", "auth").

    Returns:
        The matching SecurityTemplate, or None if not found.
    """
    for template in _SECURITY_TEMPLATES.get(framework, []):
        if template.category == category:
            return template
    return None


def list_supported_frameworks() -> list[str]:
    """Return sorted list of frameworks that have security templates."""
    return sorted(_SECURITY_TEMPLATES.keys())


def get_security_checklist(framework: str) -> list[str]:
    """Return the list of security feature names that should be present.

    Uses the categories covered by the framework's templates to determine
    which checklist items apply. Falls back to the full checklist if the
    framework has no templates.

    Args:
        framework: Framework name.

    Returns:
        List of security feature description strings.
    """
    templates = _SECURITY_TEMPLATES.get(framework, [])
    if not templates:
        # Return full checklist for unknown frameworks
        return [
            f"{cat}: {desc}" for cat, desc in sorted(SECURITY_CHECKLIST.items())
        ]

    covered_categories = {t.category for t in templates}
    checklist: list[str] = []
    for cat, desc in sorted(SECURITY_CHECKLIST.items()):
        if cat in covered_categories:
            checklist.append(f"{cat}: {desc}")
        else:
            checklist.append(f"{cat}: {desc} [NOT COVERED — add manually]")
    return checklist


# ── Singleton Registry ───────────────────────────────────────────

_registry: dict[str, list[SecurityTemplate]] | None = None


def get_security_template_registry() -> dict[str, list[SecurityTemplate]]:
    """Get or create the security template registry singleton.

    Returns:
        Dict mapping framework names to their security templates.
    """
    global _registry
    if _registry is None:
        _registry = {
            fw: list(templates)
            for fw, templates in _SECURITY_TEMPLATES.items()
        }
        logger.info(
            "security_template_registry_initialized",
            frameworks=list(_registry.keys()),
            total_templates=sum(len(v) for v in _registry.values()),
        )
    return _registry
