"""Tests for Phase 5: Structured Content Blocks.

Covers:
- ContentBlock types: TextBlock, ToolUseBlock, ToolResultBlock
- AIMessage accepts str | list[ContentBlock]
- _serialize_content(): converts ContentBlock list to Anthropic format
- _flatten_content_to_text(): converts ContentBlock list to text for Google
- _build_anthropic_body(): handles structured content
- _build_google_body(): flattens structured content
- call_ai_with_tools(): uses proper ContentBlock types (not pseudo-XML)
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest


# ── ContentBlock Type Tests ───────────────────────────────────────


class TestContentBlockTypes:
    """ContentBlock hierarchy must exist with correct structure."""

    def test_content_block_exists(self):
        from app.services.ai_router import ContentBlock
        assert hasattr(ContentBlock, "type")

    def test_text_block(self):
        from app.services.ai_router import TextBlock
        block = TextBlock(text="Hello world")
        assert block.type == "text"
        assert block.text == "Hello world"

    def test_tool_use_block(self):
        from app.services.ai_router import ToolUseBlock
        block = ToolUseBlock(id="abc", name="read_file", input={"path": "foo.py"})
        assert block.type == "tool_use"
        assert block.id == "abc"
        assert block.name == "read_file"
        assert block.input == {"path": "foo.py"}

    def test_tool_result_block(self):
        from app.services.ai_router import ToolResultBlock
        block = ToolResultBlock(tool_use_id="abc", content="file contents here")
        assert block.type == "tool_result"
        assert block.tool_use_id == "abc"
        assert block.content == "file contents here"
        assert block.is_error is False

    def test_tool_result_block_with_error(self):
        from app.services.ai_router import ToolResultBlock
        block = ToolResultBlock(tool_use_id="abc", content="Error: not found", is_error=True)
        assert block.is_error is True

    def test_blocks_are_frozen(self):
        """ContentBlock subtypes must be frozen (immutable)."""
        from app.services.ai_router import TextBlock
        block = TextBlock(text="test")
        with pytest.raises(AttributeError):
            block.text = "changed"  # type: ignore


# ── AIMessage Backward Compatibility Tests ────────────────────────


class TestAIMessageContent:
    """AIMessage.content must accept both str and list[ContentBlock]."""

    def test_string_content(self):
        """Plain string content must still work."""
        from app.services.ai_router import AIMessage
        msg = AIMessage(role="user", content="Hello")
        assert msg.content == "Hello"

    def test_list_content(self):
        """List of ContentBlock must be accepted."""
        from app.services.ai_router import AIMessage, TextBlock, ToolUseBlock

        blocks = [
            TextBlock(text="Let me check..."),
            ToolUseBlock(id="abc", name="read_file", input={"path": "x.py"}),
        ]
        msg = AIMessage(role="assistant", content=blocks)
        assert isinstance(msg.content, list)
        assert len(msg.content) == 2

    def test_empty_default(self):
        """Default content should be empty string."""
        from app.services.ai_router import AIMessage
        msg = AIMessage(role="user")
        assert msg.content == ""


# ── _serialize_content Tests ──────────────────────────────────────


class TestSerializeContent:
    """_serialize_content must convert ContentBlock lists to Anthropic format."""

    def test_string_passthrough(self):
        from app.services.ai_router import AIRouter
        result = AIRouter._serialize_content("plain text")
        assert result == "plain text"

    def test_text_block_serialization(self):
        from app.services.ai_router import AIRouter, TextBlock
        blocks = [TextBlock(text="Hello")]
        result = AIRouter._serialize_content(blocks)
        assert result == [{"type": "text", "text": "Hello"}]

    def test_tool_use_block_serialization(self):
        from app.services.ai_router import AIRouter, ToolUseBlock
        blocks = [ToolUseBlock(id="t1", name="read_file", input={"path": "a.py"})]
        result = AIRouter._serialize_content(blocks)
        assert result == [{
            "type": "tool_use",
            "id": "t1",
            "name": "read_file",
            "input": {"path": "a.py"},
        }]

    def test_tool_result_block_serialization(self):
        from app.services.ai_router import AIRouter, ToolResultBlock
        blocks = [ToolResultBlock(tool_use_id="t1", content="result text")]
        result = AIRouter._serialize_content(blocks)
        assert result == [{
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": "result text",
        }]

    def test_tool_result_error_serialization(self):
        from app.services.ai_router import AIRouter, ToolResultBlock
        blocks = [ToolResultBlock(tool_use_id="t1", content="err", is_error=True)]
        result = AIRouter._serialize_content(blocks)
        assert result[0]["is_error"] is True

    def test_mixed_blocks(self):
        from app.services.ai_router import AIRouter, TextBlock, ToolUseBlock
        blocks = [
            TextBlock(text="Let me check"),
            ToolUseBlock(id="t1", name="search", input={"q": "test"}),
        ]
        result = AIRouter._serialize_content(blocks)
        assert len(result) == 2
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "tool_use"

    def test_empty_text_blocks_skipped(self):
        from app.services.ai_router import AIRouter, TextBlock
        blocks = [TextBlock(text="")]
        result = AIRouter._serialize_content(blocks)
        # Empty text blocks should be skipped
        assert result == ""


# ── _flatten_content_to_text Tests ────────────────────────────────


class TestFlattenContentToText:
    """_flatten_content_to_text must convert structured content to plain text."""

    def test_string_passthrough(self):
        from app.services.ai_router import AIRouter
        result = AIRouter._flatten_content_to_text("plain text")
        assert result == "plain text"

    def test_text_block_flattening(self):
        from app.services.ai_router import AIRouter, TextBlock
        blocks = [TextBlock(text="Hello"), TextBlock(text="World")]
        result = AIRouter._flatten_content_to_text(blocks)
        assert "Hello" in result
        assert "World" in result

    def test_tool_use_block_flattening(self):
        from app.services.ai_router import AIRouter, ToolUseBlock
        blocks = [ToolUseBlock(id="t1", name="search", input={"q": "test"})]
        result = AIRouter._flatten_content_to_text(blocks)
        assert "search" in result
        assert "test" in result

    def test_tool_result_block_flattening(self):
        from app.services.ai_router import AIRouter, ToolResultBlock
        blocks = [ToolResultBlock(tool_use_id="t1", content="found it")]
        result = AIRouter._flatten_content_to_text(blocks)
        assert "found it" in result


# ── _build_anthropic_body with Structured Content ──────────────────


class TestBuildAnthropicBodyStructured:
    """_build_anthropic_body must handle structured content messages."""

    def test_string_messages_work(self):
        """Plain string messages must still work."""
        from app.services.ai_router import AIRouter, AIRequest, AIMessage, MODELS

        router = AIRouter.__new__(AIRouter)
        spec = MODELS["sonnet"]
        request = AIRequest(
            messages=[AIMessage(role="user", content="Hello")],
        )
        body = router._build_anthropic_body(spec, request)
        assert body["messages"][0]["content"] == "Hello"

    def test_structured_messages_serialized(self):
        """Structured ContentBlock messages must be serialized."""
        from app.services.ai_router import (
            AIRouter, AIRequest, AIMessage, MODELS,
            TextBlock, ToolUseBlock,
        )

        router = AIRouter.__new__(AIRouter)
        spec = MODELS["sonnet"]

        blocks = [
            TextBlock(text="Checking..."),
            ToolUseBlock(id="t1", name="read_file", input={"path": "x.py"}),
        ]
        request = AIRequest(
            messages=[
                AIMessage(role="user", content="Help me"),
                AIMessage(role="assistant", content=blocks),
            ],
        )
        body = router._build_anthropic_body(spec, request)
        # First message: string → string
        assert body["messages"][0]["content"] == "Help me"
        # Second message: list → serialized blocks
        assert isinstance(body["messages"][1]["content"], list)
        assert body["messages"][1]["content"][0]["type"] == "text"
        assert body["messages"][1]["content"][1]["type"] == "tool_use"


# ── call_ai_with_tools() No Pseudo-XML Tests ──────────────────────


class TestCallAiWithToolsStructured:
    """call_ai_with_tools must use ContentBlock types, not pseudo-XML."""

    def test_no_pseudo_xml_tool_use_in_code(self):
        """Source code lines (not comments) must NOT use pseudo-XML tool_use."""
        from app.agents.base import call_ai_with_tools
        source = inspect.getsource(call_ai_with_tools)
        # Check that no non-comment line uses the old pseudo-XML pattern
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # Skip comments
            assert 'f\'[tool_use id="' not in stripped, f"Found pseudo-XML in code: {stripped}"

    def test_no_pseudo_xml_tool_result_in_code(self):
        """Source code lines (not comments) must NOT use pseudo-XML tool_result."""
        from app.agents.base import call_ai_with_tools
        source = inspect.getsource(call_ai_with_tools)
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert 'f\'[tool_result id="' not in stripped, f"Found pseudo-XML in code: {stripped}"

    def test_uses_tool_use_block(self):
        """Source must use ToolUseBlock."""
        from app.agents.base import call_ai_with_tools
        source = inspect.getsource(call_ai_with_tools)
        assert "ToolUseBlock" in source

    def test_uses_tool_result_block(self):
        """Source must use ToolResultBlock."""
        from app.agents.base import call_ai_with_tools
        source = inspect.getsource(call_ai_with_tools)
        assert "ToolResultBlock" in source

    def test_uses_text_block(self):
        """Source must use TextBlock for assistant text content."""
        from app.agents.base import call_ai_with_tools
        source = inspect.getsource(call_ai_with_tools)
        assert "TextBlock" in source

    def test_imports_content_blocks(self):
        """base module must import all ContentBlock types."""
        import app.agents.base as mod
        source = inspect.getsource(mod)
        assert "TextBlock" in source
        assert "ToolUseBlock" in source
        assert "ToolResultBlock" in source
        assert "ContentBlock" in source
