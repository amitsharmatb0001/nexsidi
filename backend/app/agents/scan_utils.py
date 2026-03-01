"""Shared utilities for quality-scanning agents.

Provides windowed content splitting so agents can scan 100% of files
instead of truncating to first 2000-3000 characters.
"""

from __future__ import annotations


def split_into_windows(
    content: str,
    window_size: int = 3000,
    overlap: int = 500,
) -> list[str]:
    """Split content into overlapping windows for full-file scanning.

    Instead of ``content[:3000]`` (which ignores 90% of large files),
    this splits content into overlapping windows so every line is scanned
    at least once.

    Args:
        content: Full file content to split.
        window_size: Maximum characters per window.
        overlap: Characters of overlap between adjacent windows.
            Overlap prevents issues at window boundaries (e.g., a
            vulnerability that spans two windows will appear in
            at least one window fully).

    Returns:
        List of content windows. For small files (<= window_size),
        returns a single-element list with the full content.

    Examples:
        >>> split_into_windows("short")
        ['short']
        >>> windows = split_into_windows("x" * 7000, 3000, 500)
        >>> len(windows)
        3
        >>> all(len(w) <= 3000 for w in windows)
        True
    """
    if len(content) <= window_size:
        return [content]

    windows: list[str] = []
    start = 0
    # R28-FIX-9: Guard against infinite loop when overlap >= window_size.
    # step=0 or negative causes while-loop to never terminate.
    if overlap >= window_size:
        overlap = 0
    step = max(window_size - overlap, 1)

    while start < len(content):
        end = start + window_size
        windows.append(content[start:end])
        start += step

    return windows
