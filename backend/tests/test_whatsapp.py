"""Tests for WhatsApp Agent: webhook parsing, signature verification, DPDP compliance."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from app.agents.whatsapp_agent import (
    WhatsAppIncoming,
    WhatsAppMessageType,
    WhatsAppOutgoing,
    WhatsAppSessionStatus,
    WHATSAPP_TEMPLATES,
    verify_webhook_signature,
    _redact_phone,
    _hash_phone,
)


class TestWebhookSignatureVerification:
    """Test HMAC-SHA256 webhook verification."""

    def test_valid_signature(self):
        secret = "my_app_secret"
        payload = b'{"entry":[{"changes":[]}]}'
        sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert verify_webhook_signature(payload, sig, secret) is True

    def test_invalid_signature(self):
        assert verify_webhook_signature(b"data", "sha256=deadbeef", "secret") is False

    def test_wrong_prefix(self):
        assert verify_webhook_signature(b"data", "md5=abc", "secret") is False

    def test_empty_signature(self):
        assert verify_webhook_signature(b"data", "", "secret") is False


class TestPhonePrivacy:
    """Test DPDP-compliant phone number handling."""

    def test_redact_full_number(self):
        redacted = _redact_phone("+919876543210")
        assert redacted.startswith("+9")
        assert redacted.endswith("10")
        assert "*" in redacted
        assert len(redacted) == len("+919876543210")

    def test_redact_short_number(self):
        assert _redact_phone("12") == "****"
        assert _redact_phone("") == "****"

    def test_hash_produces_fixed_length(self):
        h = _hash_phone("+919876543210")
        assert len(h) == 16
        assert h.isalnum()

    def test_hash_is_deterministic(self):
        h1 = _hash_phone("+919876543210")
        h2 = _hash_phone("+919876543210")
        assert h1 == h2

    def test_different_numbers_different_hashes(self):
        h1 = _hash_phone("+919876543210")
        h2 = _hash_phone("+919876543211")
        assert h1 != h2


class TestWebhookParsing:
    """Test WhatsApp incoming message parsing."""

    def test_parse_text_message(self):
        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "+919876543210",
                "type": "text",
                "text": {"body": "Build me a restaurant app"},
                "timestamp": "1700000000",
            }]}}]}]
        }
        msg = WhatsAppIncoming.from_webhook(payload)
        assert msg is not None
        assert msg.message_type == WhatsAppMessageType.TEXT
        assert msg.text == "Build me a restaurant app"

    def test_parse_image_message(self):
        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "+919876543210",
                "type": "image",
                "image": {"id": "media_123", "caption": "wireframe"},
                "timestamp": "1700000000",
            }]}}]}]
        }
        msg = WhatsAppIncoming.from_webhook(payload)
        assert msg is not None
        assert msg.message_type == WhatsAppMessageType.IMAGE
        assert msg.media_id == "media_123"
        assert msg.caption == "wireframe"

    def test_parse_audio_message(self):
        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "+919876543210",
                "type": "audio",
                "audio": {"id": "media_456"},
                "timestamp": "1700000001",
            }]}}]}]
        }
        msg = WhatsAppIncoming.from_webhook(payload)
        assert msg is not None
        assert msg.message_type == WhatsAppMessageType.AUDIO
        assert msg.media_id == "media_456"

    def test_parse_empty_messages(self):
        payload = {"entry": [{"changes": [{"value": {"messages": []}}]}]}
        assert WhatsAppIncoming.from_webhook(payload) is None

    def test_parse_malformed_payload(self):
        assert WhatsAppIncoming.from_webhook({}) is None
        assert WhatsAppIncoming.from_webhook({"entry": []}) is None

    def test_phone_is_redacted_in_parsed_message(self):
        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "+919876543210",
                "type": "text",
                "text": {"body": "hi"},
            }]}}]}]
        }
        msg = WhatsAppIncoming.from_webhook(payload)
        assert msg is not None
        # Phone should be redacted, not the original
        assert "+919876543210" != msg.phone_number
        assert "*" in msg.phone_number


class TestWhatsAppTemplates:
    """Test WhatsApp message templates."""

    def test_template_count(self):
        assert len(WHATSAPP_TEMPLATES) >= 6

    def test_project_started_template(self):
        tmpl = WHATSAPP_TEMPLATES["project_started"]
        rendered = tmpl.format(project_name="MyApp")
        assert "MyApp" in rendered

    def test_checkpoint_approval_template(self):
        tmpl = WHATSAPP_TEMPLATES["checkpoint_approval"]
        rendered = tmpl.format(project_name="MyApp", checkpoint_name="Design Review")
        assert "MyApp" in rendered
        assert "Design Review" in rendered

    def test_deploy_complete_template(self):
        tmpl = WHATSAPP_TEMPLATES["deploy_complete"]
        rendered = tmpl.format(project_name="MyApp", url="https://myapp.com")
        assert "https://myapp.com" in rendered


class TestWhatsAppEnums:
    """Test WhatsApp enum values."""

    def test_message_types(self):
        assert len(WhatsAppMessageType) == 7
        assert WhatsAppMessageType.TEXT.value == "text"
        assert WhatsAppMessageType.AUDIO.value == "audio"

    def test_session_statuses(self):
        assert len(WhatsAppSessionStatus) == 5
        assert WhatsAppSessionStatus.ACTIVE.value == "active"
        assert WhatsAppSessionStatus.EXPIRED.value == "expired"
