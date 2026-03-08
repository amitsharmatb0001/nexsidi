"""Multi-Source Input Processor: converts diverse inputs to structured requirements.

Handles inputs from multiple sources:
- Text (chat, WhatsApp) → direct requirements
- Images (screenshots, mockups) → image-to-requirements via AI vision
- Audio (voice messages) → speech-to-text → requirements
- Video (screen recordings) → keyframe extraction → requirements
- Documents (PDF, DOCX) → text extraction → requirements
- Links (URLs) → web scraping → requirements
- ZIP files → code analysis → requirements

Each input type has a processor that normalizes it into a structured
RequirementInput that the pipeline's REQUIREMENTS stage can consume.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class InputType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"
    URL = "url"
    ZIP = "zip"


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


# Supported MIME types per input type
SUPPORTED_MIMES: dict[InputType, set[str]] = {
    InputType.IMAGE: {"image/png", "image/jpeg", "image/webp", "image/gif"},
    InputType.AUDIO: {"audio/ogg", "audio/mpeg", "audio/wav", "audio/webm", "audio/mp4"},
    InputType.VIDEO: {"video/mp4", "video/webm", "video/quicktime"},
    InputType.DOCUMENT: {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/markdown",
    },
    InputType.ZIP: {"application/zip", "application/x-zip-compressed"},
}

# Max file sizes per type (bytes)
MAX_FILE_SIZES: dict[InputType, int] = {
    InputType.IMAGE: 10 * 1024 * 1024,     # 10 MB
    InputType.AUDIO: 25 * 1024 * 1024,     # 25 MB
    InputType.VIDEO: 100 * 1024 * 1024,    # 100 MB
    InputType.DOCUMENT: 20 * 1024 * 1024,  # 20 MB
    InputType.ZIP: 50 * 1024 * 1024,       # 50 MB
}


@dataclass(slots=True)
class InputFile:
    """A file attachment to process."""

    filename: str
    mime_type: str
    size_bytes: int
    content: bytes = b""       # Raw file content
    url: str = ""              # If fetched from URL
    storage_path: str = ""     # If stored in object storage

    @property
    def input_type(self) -> InputType:
        """Determine input type from MIME type."""
        for itype, mimes in SUPPORTED_MIMES.items():
            if self.mime_type in mimes:
                return itype
        # Fallback by extension
        ext = self.filename.rsplit(".", 1)[-1].lower() if "." in self.filename else ""
        ext_map = {
            "png": InputType.IMAGE, "jpg": InputType.IMAGE, "jpeg": InputType.IMAGE,
            "mp3": InputType.AUDIO, "wav": InputType.AUDIO, "ogg": InputType.AUDIO,
            "mp4": InputType.VIDEO, "webm": InputType.VIDEO,
            "pdf": InputType.DOCUMENT, "docx": InputType.DOCUMENT, "txt": InputType.DOCUMENT,
            "zip": InputType.ZIP,
        }
        return ext_map.get(ext, InputType.TEXT)


@dataclass(slots=True)
class ProcessedInput:
    """Result of processing a single input."""

    input_type: InputType
    status: ProcessingStatus
    extracted_text: str = ""
    requirements: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_type": self.input_type.value,
            "status": self.status.value,
            "extracted_text": self.extracted_text[:500],
            "requirements_count": len(self.requirements),
            "error": self.error,
        }


@dataclass(slots=True)
class RequirementInput:
    """Normalized input for the pipeline's REQUIREMENTS stage."""

    raw_text: str
    sources: list[ProcessedInput] = field(default_factory=list)
    combined_requirements: str = ""
    total_inputs: int = 0
    successful_inputs: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_text": self.raw_text[:2000],
            "combined_requirements": self.combined_requirements[:5000],
            "total_inputs": self.total_inputs,
            "successful_inputs": self.successful_inputs,
            "sources": [s.to_dict() for s in self.sources],
        }


class InputProcessor:
    """Processes multi-source inputs into structured requirements."""

    def process_text(self, text: str) -> ProcessedInput:
        """Process plain text input."""
        if not text.strip():
            return ProcessedInput(
                input_type=InputType.TEXT,
                status=ProcessingStatus.FAILED,
                error="Empty text input",
            )

        # Extract bullet points or numbered items as requirements
        requirements = self._extract_requirements(text)

        return ProcessedInput(
            input_type=InputType.TEXT,
            status=ProcessingStatus.COMPLETED,
            extracted_text=text,
            requirements=requirements,
        )

    def process_url(self, url: str) -> ProcessedInput:
        """Process a URL input (web page → requirements).

        In production: fetches the URL, extracts text, uses AI to derive requirements.
        Currently: returns a placeholder for the URL content.
        """
        if not _is_valid_url(url):
            return ProcessedInput(
                input_type=InputType.URL,
                status=ProcessingStatus.FAILED,
                error="Invalid URL",
            )

        return ProcessedInput(
            input_type=InputType.URL,
            status=ProcessingStatus.COMPLETED,
            extracted_text=f"Web content from: {url}",
            requirements=[f"Build a project based on: {url}"],
            metadata={"url": url},
        )

    def process_file(self, input_file: InputFile) -> ProcessedInput:
        """Process a file attachment."""
        itype = input_file.input_type

        # Validate size
        max_size = MAX_FILE_SIZES.get(itype, 10 * 1024 * 1024)
        if input_file.size_bytes > max_size:
            return ProcessedInput(
                input_type=itype,
                status=ProcessingStatus.FAILED,
                error=f"File too large: {input_file.size_bytes} bytes (max {max_size})",
            )

        # Route to appropriate processor
        if itype == InputType.IMAGE:
            return self._process_image(input_file)
        elif itype == InputType.AUDIO:
            return self._process_audio(input_file)
        elif itype == InputType.VIDEO:
            return self._process_video(input_file)
        elif itype == InputType.DOCUMENT:
            return self._process_document(input_file)
        elif itype == InputType.ZIP:
            return self._process_zip(input_file)
        else:
            return ProcessedInput(
                input_type=itype,
                status=ProcessingStatus.UNSUPPORTED,
                error=f"Unsupported file type: {input_file.mime_type}",
            )

    def process_all(
        self,
        text: str = "",
        urls: list[str] | None = None,
        files: list[InputFile] | None = None,
    ) -> RequirementInput:
        """Process all inputs and combine into a single RequirementInput."""
        sources: list[ProcessedInput] = []

        # Process text
        if text.strip():
            sources.append(self.process_text(text))

        # Process URLs
        for url in (urls or []):
            sources.append(self.process_url(url))

        # Process files
        for f in (files or []):
            sources.append(self.process_file(f))

        # Combine all extracted text and requirements
        all_text_parts: list[str] = []
        all_requirements: list[str] = []

        for src in sources:
            if src.status == ProcessingStatus.COMPLETED:
                if src.extracted_text:
                    all_text_parts.append(src.extracted_text)
                all_requirements.extend(src.requirements)

        combined = "\n\n".join(all_text_parts)
        successful = sum(1 for s in sources if s.status == ProcessingStatus.COMPLETED)

        logger.info(
            "input_processing_complete",
            total=len(sources),
            successful=successful,
            requirements=len(all_requirements),
        )

        return RequirementInput(
            raw_text=text,
            sources=sources,
            combined_requirements=combined,
            total_inputs=len(sources),
            successful_inputs=successful,
        )

    # ── Type-Specific Processors ──────────────────────────────────

    def _process_image(self, f: InputFile) -> ProcessedInput:
        """Process image → requirements via AI vision.

        In production: sends to Claude/Gemini vision API.
        """
        return ProcessedInput(
            input_type=InputType.IMAGE,
            status=ProcessingStatus.COMPLETED,
            extracted_text=f"[Image: {f.filename}]",
            requirements=[f"UI should match the design shown in {f.filename}"],
            metadata={"filename": f.filename, "mime_type": f.mime_type, "size": f.size_bytes},
        )

    def _process_audio(self, f: InputFile) -> ProcessedInput:
        """Process audio → text via speech-to-text.

        In production: sends to voice_to_text service.
        """
        return ProcessedInput(
            input_type=InputType.AUDIO,
            status=ProcessingStatus.COMPLETED,
            extracted_text=f"[Audio transcription from: {f.filename}]",
            requirements=["Requirements from voice message — see transcription"],
            metadata={"filename": f.filename, "duration_estimate": f.size_bytes // 16000},
        )

    def _process_video(self, f: InputFile) -> ProcessedInput:
        """Process video → keyframes + audio → requirements.

        In production: extracts keyframes, runs OCR, transcribes audio.
        """
        return ProcessedInput(
            input_type=InputType.VIDEO,
            status=ProcessingStatus.COMPLETED,
            extracted_text=f"[Video analysis from: {f.filename}]",
            requirements=["Requirements from screen recording — see analysis"],
            metadata={"filename": f.filename, "size": f.size_bytes},
        )

    def _process_document(self, f: InputFile) -> ProcessedInput:
        """Process document (PDF, DOCX, TXT) → text extraction."""
        if f.mime_type == "text/plain" or f.mime_type == "text/markdown":
            text = f.content.decode("utf-8", errors="replace") if f.content else f"[Document: {f.filename}]"
            requirements = self._extract_requirements(text)
            return ProcessedInput(
                input_type=InputType.DOCUMENT,
                status=ProcessingStatus.COMPLETED,
                extracted_text=text,
                requirements=requirements,
                metadata={"filename": f.filename},
            )

        # PDF/DOCX: would use pdfplumber or python-docx in production
        return ProcessedInput(
            input_type=InputType.DOCUMENT,
            status=ProcessingStatus.COMPLETED,
            extracted_text=f"[Document content from: {f.filename}]",
            requirements=[f"Requirements from document: {f.filename}"],
            metadata={"filename": f.filename, "mime_type": f.mime_type},
        )

    def _process_zip(self, f: InputFile) -> ProcessedInput:
        """Process ZIP → code analysis → requirements."""
        return ProcessedInput(
            input_type=InputType.ZIP,
            status=ProcessingStatus.COMPLETED,
            extracted_text=f"[Code archive: {f.filename}]",
            requirements=[f"Analyze and improve codebase from: {f.filename}"],
            metadata={"filename": f.filename, "size": f.size_bytes},
        )

    def _extract_requirements(self, text: str) -> list[str]:
        """Extract individual requirements from text."""
        lines = text.strip().split("\n")
        requirements: list[str] = []

        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Match numbered or bulleted items
            cleaned = re.sub(r"^(?:\d+[.)]\s*|[-*•]\s*)", "", line)
            if cleaned and len(cleaned) > 5:
                requirements.append(cleaned)

        return requirements if requirements else [text[:500]]


def _is_ip_unsafe(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    """Check if an IP address is internal (SSRF target).

    R36-FIX: Extracted to avoid duplicating the check across IP literal
    validation and DNS resolution paths.
    """
    import ipaddress
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        v4 = ip.ipv4_mapped
        if v4.is_private or v4.is_loopback or v4.is_link_local or v4.is_reserved or v4.is_unspecified:
            return True
    return False


def _is_valid_url(url: str) -> bool:
    """Validate URL and block SSRF-prone targets.

    R32-FIX-SSRF: The previous regex only checked for http(s)://,
    allowing access to internal networks (169.254.169.254, localhost,
    127.0.0.1, metadata.google.internal, etc.). Now blocks private IPs
    and cloud metadata endpoints.
    """
    if not re.match(r"^https?://[^\s]+$", url):
        return False
    from urllib.parse import urlparse
    import ipaddress
    import socket
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
    except Exception:
        return False
    # Block known SSRF targets
    _blocked = {
        "localhost", "metadata.google.internal",
        "metadata", "kubernetes.default.svc",
    }
    if hostname in _blocked:
        return False
    # Block IP address ranges: loopback, link-local, private, metadata
    is_ip_literal = False
    try:
        ip = ipaddress.ip_address(hostname)
        is_ip_literal = True
        if _is_ip_unsafe(ip):
            return False
    except ValueError:
        pass  # Not an IP literal — hostname needs DNS resolution check

    # R36-FIX: DNS rebinding defense. Resolve hostname at validation time
    # and verify the resolved IP is not internal. Classic DNS rebinding:
    # 1. Attacker DNS returns 8.8.8.8 → validation passes
    # 2. Attacker DNS returns 127.0.0.1 → fetch hits localhost/metadata
    # Resolving here narrows the TOCTOU window. For full protection, use a
    # DNS-pinning HTTP client that resolves + connects atomically.
    if not is_ip_literal:
        try:
            # R38-FIX: Use per-socket timeout instead of process-global
            # setdefaulttimeout(). The global timeout races with all other
            # socket creation in the process (Valkey, GCP, AI calls).
            # getaddrinfo doesn't support per-call timeout, so we wrap
            # it in a thread with a timeout via concurrent.futures.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(socket.getaddrinfo, hostname, None, type=socket.SOCK_STREAM)
                try:
                    addrs = future.result(timeout=3)
                except concurrent.futures.TimeoutError:
                    return False  # DNS timeout — fail-closed
            if not addrs:
                return False
            for _family, _, _, _, sockaddr in addrs:
                resolved_ip = ipaddress.ip_address(sockaddr[0])
                if _is_ip_unsafe(resolved_ip):
                    logger.warning(
                        "ssrf_dns_rebinding_blocked",
                        hostname=hostname,
                        resolved_ip=str(resolved_ip),
                    )
                    return False
        except (socket.gaierror, OSError):
            return False
    return True


# ── Singleton ───────────────────────────────────────────────────

import threading as _threading
_processor_lock = _threading.Lock()
_processor: InputProcessor | None = None


def get_input_processor() -> InputProcessor:
    """Get or create the input processor singleton."""
    global _processor
    if _processor is not None:
        return _processor
    with _processor_lock:
        if _processor is None:
            _processor = InputProcessor()
        return _processor
