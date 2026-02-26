"""Tests for services: ai_router, notification, pipeline_audit, input_processor, voice_to_text, api_key."""

from __future__ import annotations

import pytest

from app.services.ai_router import (
    AIMessage,
    AIRequest,
    AIRouter,
    ModelSpec,
    TaskComplexity,
    get_ai_router,
)


class TestAIRouter:
    """Test the AI routing and model selection layer."""

    def test_select_model_returns_model_spec(self):
        router = get_ai_router()
        req = AIRequest(
            messages=[AIMessage(role="user", content="test")],
            complexity=TaskComplexity.LOW,
            task_type="test",
        )
        model = router.select_model(req)
        assert isinstance(model, ModelSpec)
        assert model.model_id
        assert model.provider.value in ("anthropic", "google")

    def test_select_model_different_complexities(self):
        router = get_ai_router()
        low_req = AIRequest(
            messages=[AIMessage(role="user", content="test")],
            complexity=TaskComplexity.LOW,
            task_type="test",
        )
        high_req = AIRequest(
            messages=[AIMessage(role="user", content="test")],
            complexity=TaskComplexity.HIGH,
            task_type="test",
        )
        low_model = router.select_model(low_req)
        high_model = router.select_model(high_req)
        assert isinstance(low_model, ModelSpec)
        assert isinstance(high_model, ModelSpec)

    def test_task_complexity_has_4_levels(self):
        assert len(TaskComplexity) == 4
        assert TaskComplexity.LOW.value == "low"
        assert TaskComplexity.CRITICAL.value == "critical"

    def test_ai_request_creation(self):
        req = AIRequest(
            messages=[AIMessage(role="user", content="test")],
            complexity=TaskComplexity.MEDIUM,
            task_type="test_task",
        )
        assert req.task_type == "test_task"
        assert len(req.messages) == 1

    def test_get_ai_router_singleton(self):
        r1 = get_ai_router()
        r2 = get_ai_router()
        assert r1 is r2


class TestNotificationService:
    """Test the notification service."""

    def test_notification_types(self):
        from app.services.notification import NotificationType
        assert len(NotificationType) == 14

    def test_notification_channels(self):
        from app.services.notification import NotificationChannel
        assert len(NotificationChannel) == 2

    def test_templates_exist(self):
        from app.services.notification import NOTIFICATION_TEMPLATES
        assert len(NOTIFICATION_TEMPLATES) == 14

    @pytest.mark.asyncio
    async def test_send_notification(self):
        from app.services.notification import NotificationService, NotificationType, NotificationChannel
        svc = NotificationService()
        result = await svc.send(
            notification_type=NotificationType.PIPELINE_STARTED,
            user_id="user-1",
            organization_id="org-1",
            channel=NotificationChannel.WEB,
            project_name="TestApp",
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_rate_limiting(self):
        from app.services.notification import NotificationService, NotificationType, NotificationChannel
        svc = NotificationService()
        # Send 100 notifications rapidly
        for _ in range(100):
            await svc.send(
                notification_type=NotificationType.SYSTEM_MAINTENANCE,
                user_id="rate-test-user",
                organization_id="org-1",
                channel=NotificationChannel.WEB,
            )
        # 101st should be rate limited (raises ValueError)
        with pytest.raises(ValueError, match="Rate limit exceeded"):
            await svc.send(
                notification_type=NotificationType.SYSTEM_MAINTENANCE,
                user_id="rate-test-user",
                organization_id="org-1",
                channel=NotificationChannel.WEB,
            )

    def test_singleton(self):
        from app.services.notification import get_notification_service
        s1 = get_notification_service()
        s2 = get_notification_service()
        assert s1 is s2


class TestPipelineAudit:
    """Test the pipeline audit service."""

    def test_event_types(self):
        from app.services.pipeline_audit import AuditEventType
        assert len(AuditEventType) == 21

    def test_pipeline_created_event(self):
        from app.services.pipeline_audit import PipelineAuditService
        svc = PipelineAuditService()
        event = svc.pipeline_created("run-1", "proj-1", "org-1", "user-1", "checkpoint")
        assert event is not None

    def test_get_events(self):
        from app.services.pipeline_audit import PipelineAuditService
        svc = PipelineAuditService()
        svc.pipeline_created("run-2", "proj-2", "org-2", "user-2", "checkpoint")
        svc.stage_started("run-2", "requirements", "tilotma")
        events = svc.get_events("run-2")
        assert len(events) == 2

    def test_singleton(self):
        from app.services.pipeline_audit import get_pipeline_audit
        s1 = get_pipeline_audit()
        s2 = get_pipeline_audit()
        assert s1 is s2


class TestInputProcessor:
    """Test the multi-source input processor."""

    def test_input_types(self):
        from app.services.input_processor import InputType
        assert len(InputType) == 7

    def test_processing_statuses(self):
        from app.services.input_processor import ProcessingStatus
        assert len(ProcessingStatus) == 5

    def test_process_text(self):
        from app.services.input_processor import InputProcessor
        proc = InputProcessor()
        result = proc.process_text("1. Login page\n2. Dashboard\n3. Reports")
        assert result.status.value == "completed"
        assert len(result.requirements) >= 3

    def test_process_empty_text_fails(self):
        from app.services.input_processor import InputProcessor
        proc = InputProcessor()
        result = proc.process_text("   ")
        assert result.status.value == "failed"

    def test_process_url(self):
        from app.services.input_processor import InputProcessor
        proc = InputProcessor()
        result = proc.process_url("https://example.com")
        assert result.status.value == "completed"

    def test_process_invalid_url(self):
        from app.services.input_processor import InputProcessor
        proc = InputProcessor()
        result = proc.process_url("not-a-url")
        assert result.status.value == "failed"

    def test_process_file_image(self):
        from app.services.input_processor import InputProcessor, InputFile
        proc = InputProcessor()
        f = InputFile(filename="wireframe.png", mime_type="image/png", size_bytes=1024)
        result = proc.process_file(f)
        assert result.status.value == "completed"

    def test_process_file_oversized(self):
        from app.services.input_processor import InputProcessor, InputFile
        proc = InputProcessor()
        f = InputFile(filename="huge.mp4", mime_type="video/mp4", size_bytes=200 * 1024 * 1024)
        result = proc.process_file(f)
        assert result.status.value == "failed"
        assert "too large" in result.error.lower()

    def test_process_all(self):
        from app.services.input_processor import InputProcessor, InputFile
        proc = InputProcessor()
        img = InputFile(filename="mock.png", mime_type="image/png", size_bytes=1024)
        result = proc.process_all(text="Build an app", urls=["https://x.com"], files=[img])
        assert result.total_inputs == 3
        assert result.successful_inputs == 3

    def test_singleton(self):
        from app.services.input_processor import get_input_processor
        s1 = get_input_processor()
        s2 = get_input_processor()
        assert s1 is s2


class TestVoiceToText:
    """Test the voice-to-text service."""

    def test_audio_formats(self):
        from app.services.voice_to_text import AudioFormat
        assert len(AudioFormat) == 6

    def test_transcription_statuses(self):
        from app.services.voice_to_text import TranscriptionStatus
        assert len(TranscriptionStatus) == 6

    def test_supported_languages(self):
        from app.services.voice_to_text import SUPPORTED_LANGUAGES
        assert len(SUPPORTED_LANGUAGES) >= 10
        assert "hi" in SUPPORTED_LANGUAGES
        assert "en" in SUPPORTED_LANGUAGES

    def test_detect_format_by_mime(self):
        from app.services.voice_to_text import VoiceToTextService, AudioFormat
        vtt = VoiceToTextService()
        assert vtt.detect_format("audio/ogg") == AudioFormat.OGG
        assert vtt.detect_format("audio/mpeg") == AudioFormat.MP3

    def test_detect_format_by_extension(self):
        from app.services.voice_to_text import VoiceToTextService, AudioFormat
        vtt = VoiceToTextService()
        assert vtt.detect_format("application/octet-stream", "file.wav") == AudioFormat.WAV

    def test_detect_format_unknown(self):
        from app.services.voice_to_text import VoiceToTextService
        vtt = VoiceToTextService()
        assert vtt.detect_format("text/html") is None

    def test_validate_valid_audio(self):
        from app.services.voice_to_text import VoiceToTextService
        vtt = VoiceToTextService()
        assert vtt.validate_audio(b"\x00" * 1000, "audio/ogg") is None

    def test_validate_unsupported_format(self):
        from app.services.voice_to_text import VoiceToTextService, TranscriptionStatus
        vtt = VoiceToTextService()
        result = vtt.validate_audio(b"\x00", "text/html")
        assert result is not None
        assert result.status == TranscriptionStatus.UNSUPPORTED_FORMAT

    def test_validate_too_large(self):
        from app.services.voice_to_text import VoiceToTextService
        vtt = VoiceToTextService()
        result = vtt.validate_audio(b"\x00" * (26 * 1024 * 1024), "audio/ogg")
        assert result is not None
        assert "too large" in result.error.lower()

    @pytest.mark.asyncio
    async def test_transcribe_success(self):
        from app.services.voice_to_text import VoiceToTextService, TranscriptionStatus
        vtt = VoiceToTextService()
        result = await vtt.transcribe(b"\x00" * 1000, "audio/ogg", "test.ogg")
        assert result.status == TranscriptionStatus.COMPLETED
        assert result.language_code == "en"
        assert result.confidence > 0

    @pytest.mark.asyncio
    async def test_transcribe_hindi(self):
        from app.services.voice_to_text import VoiceToTextService
        vtt = VoiceToTextService()
        result = await vtt.transcribe(b"\x00" * 500, "audio/ogg", "v.ogg", language_hint="hi")
        assert result.language_code == "hi"
        assert result.language_name == "Hindi"

    @pytest.mark.asyncio
    async def test_transcribe_whatsapp_voice(self):
        from app.services.voice_to_text import VoiceToTextService, TranscriptionStatus
        vtt = VoiceToTextService()
        result = await vtt.transcribe_whatsapp_voice(b"\x00" * 800, "hash123")
        assert result.status == TranscriptionStatus.COMPLETED

    def test_singleton(self):
        from app.services.voice_to_text import get_voice_to_text_service
        s1 = get_voice_to_text_service()
        s2 = get_voice_to_text_service()
        assert s1 is s2


class TestApiKeyService:
    """Test the enterprise API key service."""

    def test_generate_key(self):
        from app.services.api_key_service import generate_api_key
        plaintext, key_hash, prefix = generate_api_key()
        assert plaintext.startswith("nxsd_")
        assert len(key_hash) == 64
        assert len(prefix) == 12

    def test_hash_consistency(self):
        from app.services.api_key_service import generate_api_key, hash_api_key
        plaintext, key_hash, _ = generate_api_key()
        assert hash_api_key(plaintext) == key_hash

    def test_create_and_verify(self):
        from app.services.api_key_service import ApiKeyService, ApiKeyStatus
        svc = ApiKeyService()
        result = svc.create_key("org-1", "user-1", "Test Key")
        assert result.plaintext_key.startswith("nxsd_")
        assert result.record.status == ApiKeyStatus.ACTIVE

        verified = svc.verify_key(result.plaintext_key)
        assert verified is not None
        assert verified.id == result.record.id

    def test_verify_invalid_key(self):
        from app.services.api_key_service import ApiKeyService
        svc = ApiKeyService()
        assert svc.verify_key("nxsd_invalid") is None

    def test_revoke_key(self):
        from app.services.api_key_service import ApiKeyService
        svc = ApiKeyService()
        result = svc.create_key("org-1", "user-1", "Revoke Me")
        assert svc.revoke_key(result.record.id, "user-1") is True
        assert svc.verify_key(result.plaintext_key) is None

    def test_revoke_wrong_user(self):
        from app.services.api_key_service import ApiKeyService
        svc = ApiKeyService()
        result = svc.create_key("org-1", "user-1", "Protected")
        assert svc.revoke_key(result.record.id, "wrong-user") is False

    def test_regenerate_key(self):
        from app.services.api_key_service import ApiKeyService
        svc = ApiKeyService()
        original = svc.create_key("org-1", "user-1", "Regen Me")
        regen = svc.regenerate_key(original.record.id, "user-1")
        assert regen is not None
        assert regen.plaintext_key != original.plaintext_key
        assert svc.verify_key(original.plaintext_key) is None  # Old key invalidated
        assert svc.verify_key(regen.plaintext_key) is not None

    def test_rate_limiting(self):
        from app.services.api_key_service import ApiKeyService, ApiKeyTier
        svc = ApiKeyService()
        result = svc.create_key("org-1", "user-1", "Rate Test", tier=ApiKeyTier.STANDARD)
        for _ in range(100):
            svc.check_rate_limit(result.plaintext_key)
        assert svc.check_rate_limit(result.plaintext_key) is False  # 101st blocked

    def test_tier_rate_limits(self):
        from app.services.api_key_service import TIER_RATE_LIMITS, ApiKeyTier
        assert TIER_RATE_LIMITS[ApiKeyTier.STANDARD] == 100
        assert TIER_RATE_LIMITS[ApiKeyTier.PROFESSIONAL] == 500
        assert TIER_RATE_LIMITS[ApiKeyTier.ENTERPRISE] == 1000

    def test_to_dict_no_hash_exposed(self):
        from app.services.api_key_service import ApiKeyService
        svc = ApiKeyService()
        result = svc.create_key("org-1", "user-1", "Dict Test")
        d = result.record.to_dict()
        assert "key_hash" not in d
        assert "key_prefix" in d
        assert "tier" in d

    def test_list_keys(self):
        from app.services.api_key_service import ApiKeyService
        svc = ApiKeyService()
        svc.create_key("org-1", "user-a", "Key 1")
        svc.create_key("org-1", "user-a", "Key 2")
        svc.create_key("org-1", "user-b", "Key 3")
        assert len(svc.list_keys("user-a")) == 2
        assert len(svc.list_org_keys("org-1")) == 3

    def test_singleton(self):
        from app.services.api_key_service import get_api_key_service
        s1 = get_api_key_service()
        s2 = get_api_key_service()
        assert s1 is s2
