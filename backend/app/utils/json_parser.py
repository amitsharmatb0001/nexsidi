"""Robust JSON parsing for LLM outputs.

3.4-FIX: Shared utility replacing fragile per-agent JSON parsing.
LLM responses frequently contain malformed JSON: markdown fences,
trailing commas, missing closing brackets, or embedded explanations.

Multi-strategy approach:
1. Direct json.loads() — fast path for well-formed JSON
2. Strip markdown fences + retry
3. Regex extract first balanced {...} or [...]
4. Partial JSON repair (trailing commas, missing brackets)
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def parse_json(text: str, *, fallback: Any = None) -> Any:
    """Parse JSON from LLM output using multiple strategies.

    Args:
        text: Raw LLM output (may contain markdown, explanations, etc.)
        fallback: Value to return if all parsing strategies fail.

    Returns:
        Parsed JSON object, or ``fallback`` if unparseable.
    """
    if not text or not text.strip():
        return fallback

    # Strategy 1: Direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: Strip markdown code fences
    stripped = _strip_markdown_fences(text)
    if stripped != text:
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 3: Extract first balanced JSON object/array
    extracted = _extract_balanced_json(stripped)
    if extracted:
        try:
            return json.loads(extracted)
        except (json.JSONDecodeError, ValueError):
            # Strategy 4: Repair common issues and retry
            repaired = _repair_json(extracted)
            try:
                return json.loads(repaired)
            except (json.JSONDecodeError, ValueError):
                pass

    # Strategy 4b: Repair on the stripped text directly
    repaired = _repair_json(stripped)
    try:
        return json.loads(repaired)
    except (json.JSONDecodeError, ValueError):
        pass

    logger.debug("json_parse_failed", text_len=len(text), preview=text[:100])
    return fallback


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ```)."""
    # Remove opening fence with optional language tag
    text = re.sub(r"^```\w*\s*\n?", "", text.strip())
    # Remove closing fence
    text = re.sub(r"\n?```\s*$", "", text.strip())
    return text.strip()


def _extract_balanced_json(text: str) -> str | None:
    """Extract the first balanced {...} or [...] from text.

    Uses brace-depth tracking (not find/rfind) to handle nested objects.
    """
    for open_char, close_char in [("{", "}"), ("[", "]")]:
        start = text.find(open_char)
        if start == -1:
            continue

        depth = 0
        in_string = False
        escape = False

        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == open_char:
                depth += 1
            elif c == close_char:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

    return None


def _repair_json(text: str) -> str:
    """Attempt common JSON repairs.

    Fixes:
    - Trailing commas before } or ]
    - Missing closing brackets (add them)
    - Single quotes → double quotes (for simple cases)
    """
    # Fix trailing commas: ,} or ,]
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # Fix single-quoted strings (simple cases only)
    # Only do this if there are no double quotes at all (avoid breaking mixed)
    if '"' not in text and "'" in text:
        text = text.replace("'", '"')

    # Fix missing closing brackets
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    if open_braces > 0:
        text += "}" * open_braces
    if open_brackets > 0:
        text += "]" * open_brackets

    return text
