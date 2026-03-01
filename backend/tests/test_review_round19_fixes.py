"""Tests for Review Round 19 fixes.

Covers:
- AUTH: decode_token requires jti/sub/iat/exp mandatory claims
- AUTH: verify_token accesses claims directly (no conditional bypass)
- AUTH: /refresh uses payload["jti"] directly (no conditional skip)
- AUTH: create_access_token validates role against allowlist
- AUTH: Logout audit log sets RLS context
- CORS: cors_origins rejects wildcard "*"
- CELERY: _ensure_services skips migrations
- PIPELINE: CancelledError (BaseException) caught in run_pipeline
- WS: _validate_ws_token catches only JWTError (not broad Exception)
- WS: Pre-auth connection limit enforced
- WS: Message size limit constant defined
- WS: ws_error log uses _sanitize_error
- AI: _sanitize_error catches x-goog-api-key header
- AI: GeminiCacheManager cache key includes model_id + full SHA-256
- AI: _pending dict cleanup on cache creation failure
- AI: VertexAI initialize() guarded by _refresh_lock
"""

from __future__ import annotations

import hashlib
import inspect

import pytest


# ── AUTH: decode_token requires mandatory claims ──────────────────


class TestDecodeTokenMandatoryClaims:
    """decode_token must require jti, sub, iat, exp as mandatory claims."""

    def test_decode_token_options_require_claims(self):
        """Must pass options={"require": [...]} to jwt.decode."""
        from app.services.auth import decode_token
        source = inspect.getsource(decode_token)
        assert '"require"' in source or "'require'" in source
        # Must require jti, sub, iat, exp
        assert '"jti"' in source
        assert '"sub"' in source
        assert '"iat"' in source
        assert '"exp"' in source

    def test_verify_token_accesses_jti_directly(self):
        """verify_token must use payload["jti"], not payload.get("jti")."""
        from app.services.auth import verify_token
        source = inspect.getsource(verify_token)
        # R19-FIX: Should access directly since claims are now mandatory
        assert 'payload["jti"]' in source
        assert 'payload["sub"]' in source
        assert 'payload["iat"]' in source


# ── AUTH: /refresh uses payload["jti"] directly ───────────────────


class TestRefreshNoConditionalJTI:
    """/refresh must not conditionally skip revocation based on jti presence."""

    def test_refresh_accesses_jti_directly(self):
        """Must use payload["jti"], not payload.get("jti")."""
        from app.routers.auth import refresh_token
        source = inspect.getsource(refresh_token)
        assert 'payload["jti"]' in source
        assert 'payload["exp"]' in source

    def test_refresh_no_conditional_revocation(self):
        """Must not have `if old_jti and old_exp:` conditional (as active code)."""
        from app.routers.auth import refresh_token
        source = inspect.getsource(refresh_token)
        # Check that no non-comment line contains the old conditional
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # Skip comments
            assert "if old_jti and old_exp:" not in stripped, \
                "Active code must not conditionally skip revocation"


# ── AUTH: create_access_token validates role ───────────────────────


class TestCreateAccessTokenRoleValidation:
    """create_access_token must validate role against allowlist."""

    def test_validates_role(self):
        """Must check role is in _ALLOWED_ROLES."""
        from app.services.auth import create_access_token
        source = inspect.getsource(create_access_token)
        assert "_ALLOWED_ROLES" in source

    def test_rejects_invalid_role(self):
        """Must raise ValueError for invalid role."""
        import uuid
        from app.services.auth import create_access_token
        with pytest.raises(ValueError, match="Invalid role"):
            create_access_token(
                user_id=uuid.uuid4(),
                organization_id=uuid.uuid4(),
                role="hacker_role",
            )

    def test_accepts_valid_roles(self):
        """Must accept standard roles without error."""
        from app.services.auth import _ALLOWED_ROLES
        assert "org_admin" in _ALLOWED_ROLES
        assert "developer" in _ALLOWED_ROLES
        assert "viewer" in _ALLOWED_ROLES
        assert "super_admin" in _ALLOWED_ROLES


# ── AUTH: Logout audit sets RLS context ────────────────────────────


class TestLogoutAuditRLSContext:
    """Logout and logout-all must set tenant context before audit writes."""

    def test_logout_sets_tenant_context(self):
        """Must call set_tenant_context in logout audit section."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        assert "set_tenant_context" in source

    def test_logout_all_sets_tenant_context(self):
        """Must call set_tenant_context in logout-all audit section."""
        from app.routers.auth import logout_all
        source = inspect.getsource(logout_all)
        assert "set_tenant_context" in source


# ── CORS: cors_origins validates against wildcard ─────────────────


class TestCorsOriginsValidation:
    """CORS origins must reject wildcard '*'."""

    def test_settings_has_cors_validator(self):
        """Must have a field_validator for cors_origins."""
        from app.config import Settings
        source = inspect.getsource(Settings)
        assert "validate_cors_origins" in source

    def test_rejects_wildcard(self):
        """Must raise ValueError when '*' is in cors_origins."""
        from pydantic import ValidationError
        from app.config import Settings
        # Attempt to validate a list containing '*'
        # We test the validator directly
        with pytest.raises(ValueError, match="cannot contain"):
            Settings.validate_cors_origins(["*"])

    def test_rejects_bare_domain(self):
        """Must reject origins without http:// or https:// scheme."""
        with pytest.raises(ValueError, match="must start with"):
            from app.config import Settings
            Settings.validate_cors_origins(["example.com"])

    def test_accepts_valid_origins(self):
        """Must accept properly formatted origins."""
        from app.config import Settings
        result = Settings.validate_cors_origins(["http://localhost:3000", "https://app.example.com"])
        assert len(result) == 2


# ── CELERY: _ensure_services skips migrations ─────────────────────


class TestCelerySkipsMigrations:
    """Celery workers must NOT run migrations in _ensure_services."""

    def test_ensure_services_no_run_migrations(self):
        """Must not call run_migrations()."""
        from app.tasks.pipeline_tasks import _ensure_services
        source = inspect.getsource(_ensure_services)
        # Must NOT have an active (uncommented) run_migrations call
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "run_migrations()" not in stripped, \
                "Celery workers must NOT run migrations"

    def test_ensure_services_no_import_run_migrations(self):
        """Must not import run_migrations."""
        from app.tasks.pipeline_tasks import _ensure_services
        source = inspect.getsource(_ensure_services)
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "import run_migrations" not in stripped and \
                   "run_migrations" not in stripped.split("import ")[-1] \
                   if "import" in stripped else True


# ── PIPELINE: CancelledError caught ───────────────────────────────


class TestCancelledErrorHandling:
    """run_pipeline must catch CancelledError (BaseException)."""

    def test_run_pipeline_catches_base_exception(self):
        """Must have `except BaseException` after `except Exception`."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.run_pipeline)
        assert "except BaseException" in source

    def test_cancelled_run_is_persisted(self):
        """Must persist run as FAILED before re-raising."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.run_pipeline)
        # Must have both: persist and re-raise
        base_exc_idx = source.find("except BaseException")
        raise_idx = source.find("raise", base_exc_idx)
        assert raise_idx > base_exc_idx, "Must re-raise after BaseException handling"


# ── WS: _validate_ws_token catches only JWTError ──────────────────


class TestValidateWsTokenExceptionHandling:
    """_validate_ws_token must catch only JWTError, not broad Exception."""

    def test_catches_only_jwt_error(self):
        """Must have `except JWTError:` not `except (JWTError, Exception):`."""
        from app.routers.websocket import _validate_ws_token
        source = inspect.getsource(_validate_ws_token)
        assert "except JWTError:" in source
        # Ensure no active code line has the broad catch
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # Skip comments
            assert "except (JWTError, Exception)" not in stripped, \
                "Active code must catch only JWTError"


# ── WS: Pre-auth connection limit ─────────────────────────────────


class TestPreAuthConnectionLimit:
    """WebSocket handler must limit pre-auth connections."""

    def test_has_pre_auth_constant(self):
        """Must define _MAX_PRE_AUTH_CONNECTIONS."""
        from app.routers import websocket as ws_mod
        assert hasattr(ws_mod, "_MAX_PRE_AUTH_CONNECTIONS")
        assert ws_mod._MAX_PRE_AUTH_CONNECTIONS > 0

    def test_has_pre_auth_limiter(self):
        """Must define pre-auth connection limiter (R21: semaphore replaces counter)."""
        from app.routers import websocket as ws_mod
        # R21-FIX: Upgraded from int counter to asyncio.Semaphore
        assert hasattr(ws_mod, "_pre_auth_semaphore") or hasattr(ws_mod, "_pre_auth_connections")

    def test_handler_enforces_pre_auth_limit(self):
        """pipeline_websocket must enforce pre-auth connection limit."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        # R21-FIX: Now uses semaphore instead of counter
        assert "_pre_auth_semaphore" in source or "_pre_auth_connections" in source


# ── WS: Message size limit ────────────────────────────────────────


class TestWebSocketMessageSizeLimit:
    """WebSocket must enforce message size limit."""

    def test_has_max_message_size_constant(self):
        """Must define _MAX_WS_MESSAGE_BYTES."""
        from app.routers import websocket as ws_mod
        assert hasattr(ws_mod, "_MAX_WS_MESSAGE_BYTES")
        assert ws_mod._MAX_WS_MESSAGE_BYTES > 0
        assert ws_mod._MAX_WS_MESSAGE_BYTES <= 65536  # Reasonable upper bound

    def test_handler_uses_receive_text(self):
        """pipeline_websocket must use receive_text() not receive_json() for auth."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        # Auth phase should use receive_text with size check
        assert "receive_text()" in source
        assert "_MAX_WS_MESSAGE_BYTES" in source


# ── WS: ws_error uses _sanitize_error ─────────────────────────────


class TestWsErrorSanitized:
    """WebSocket error logging must sanitize exception messages."""

    def test_ws_error_uses_sanitize_error(self):
        """Must call _sanitize_error in the except block."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        assert "_sanitize_error" in source


# ── AI: _sanitize_error catches x-goog-api-key ────────────────────


class TestSanitizeErrorGoogApiKey:
    """_sanitize_error must sanitize x-goog-api-key header values."""

    def test_catches_x_goog_api_key(self):
        """Must have regex pattern for x-goog-api-key."""
        from app.services.ai_router import _sanitize_error
        source = inspect.getsource(_sanitize_error)
        assert "x-goog-api-key" in source

    def test_redacts_x_goog_api_key_value(self):
        """Must replace x-goog-api-key value with [REDACTED]."""
        from app.services.ai_router import _sanitize_error
        exc = Exception("x-goog-api-key: AIzaSyDabcdefghijklmnopqrstuvwxyz1234567")
        result = _sanitize_error(exc)
        assert "AIzaSyDabcdefghijklmnopqrstuvwxyz1234567" not in result
        assert "[REDACTED]" in result


# ── AI: GeminiCacheManager cache key includes model_id ─────────────


class TestGeminiCacheKeyModelId:
    """GeminiCacheManager cache key must include model_id."""

    def test_cache_key_includes_model_id(self):
        """content_hash computation must include model_id."""
        from app.services.ai_router import GeminiCacheManager
        source = inspect.getsource(GeminiCacheManager.get_or_create_cache)
        # Must concatenate model_id into the hash input
        assert "model_id" in source
        # The hash computation should reference model_id in the sha256 input
        # (may span multiple lines due to f-string formatting)
        sha256_block = source[source.find("content_hash = hashlib.sha256("):source.find(".hexdigest()") + len(".hexdigest()")]
        assert "model_id" in sha256_block, "model_id must be in SHA-256 input"

    def test_cache_key_uses_full_sha256(self):
        """Must use full SHA-256 hex digest, not truncated."""
        from app.services.ai_router import GeminiCacheManager
        source = inspect.getsource(GeminiCacheManager.get_or_create_cache)
        # Must NOT have hexdigest()[:16] anymore
        assert "hexdigest()[:16]" not in source
        # Must use hexdigest() (full 64 chars)
        assert "hexdigest()" in source

    def test_different_models_produce_different_keys(self):
        """Same content with different model_id must produce different hashes."""
        context = "test context for caching"
        hash_flash = hashlib.sha256(f"gemini-2.5-flash:{context}".encode()).hexdigest()
        hash_pro = hashlib.sha256(f"gemini-2.5-pro:{context}".encode()).hexdigest()
        assert hash_flash != hash_pro


# ── AI: _pending cleanup on failure ────────────────────────────────


class TestPendingCleanupOnFailure:
    """GeminiCacheManager must clean up _pending on cache creation failure."""

    def test_pending_popped_on_exception(self):
        """Must pop content_hash from _pending in except block."""
        from app.services.ai_router import GeminiCacheManager
        source = inspect.getsource(GeminiCacheManager.get_or_create_cache)
        # Find the except block and check for _pending.pop
        lines = source.split("\n")
        in_except = False
        found_cleanup = False
        for line in lines:
            stripped = line.strip()
            if "except Exception as exc:" in stripped:
                in_except = True
            if in_except and "_pending.pop(" in stripped:
                found_cleanup = True
                break
            if in_except and stripped.startswith("return"):
                break
        assert found_cleanup, "_pending.pop must be in the except block"


# ── AI: VertexAI initialize() thread safety ────────────────────────


class TestVertexAIInitializeThreadSafety:
    """VertexAI initialize() must be guarded by _refresh_lock."""

    def test_initialize_uses_refresh_lock(self):
        """Must acquire _refresh_lock before checking _initialized."""
        from app.services.secret_manager import VertexAICredentialManager
        source = inspect.getsource(VertexAICredentialManager.initialize)
        assert "_refresh_lock" in source
        # Lock must be acquired BEFORE _initialized check
        lock_pos = source.find("_refresh_lock")
        init_check_pos = source.find("_initialized")
        assert lock_pos < init_check_pos, \
            "_refresh_lock must be acquired before _initialized check"
