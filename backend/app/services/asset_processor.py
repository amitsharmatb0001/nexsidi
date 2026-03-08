"""Asset Processing Service: validates, classifies and processes uploaded assets.

Handles diverse file types that users can upload when building projects:
- Logos (PNG, JPEG, SVG, ICO, WebP) -> placed in frontend public directory
- Fonts (TTF, WOFF, WOFF2, OTF) -> placed in fonts directory
- Color palettes (hex values) -> validated and mapped to design tokens
- Images (general) -> placed in assets/images
- Documents (PDF, DOCX) -> text extraction targets
- OpenAPI specs (JSON/YAML) -> parsed for endpoint generation
- SQL schemas (DDL) -> parsed for table/model generation
- CSV data -> column detection for seed data
- Figma tokens -> design token extraction
- Codebase ZIPs -> analysis target

Each asset is validated, typed, hashed, and placed in the correct
output directory for the target framework.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ────────────────────────────────────────────────────────────────────
# Enums
# ────────────────────────────────────────────────────────────────────


class AssetType(str, Enum):
    """Classification of uploaded assets."""

    LOGO = "logo"
    FONT = "font"
    COLOR_PALETTE = "color_palette"
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    OPENAPI_SPEC = "openapi_spec"
    SQL_SCHEMA = "sql_schema"
    CSV_DATA = "csv_data"
    FIGMA_TOKENS = "figma_tokens"
    CODEBASE_ZIP = "codebase_zip"


# ────────────────────────────────────────────────────────────────────
# Dataclasses
# ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AssetValidationResult:
    """Immutable result of validating an uploaded asset."""

    valid: bool
    asset_type: AssetType
    file_name: str
    file_size_bytes: int
    mime_type: str
    errors: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ProcessedAsset:
    """Immutable representation of a successfully processed asset."""

    asset_type: AssetType
    file_name: str
    content_hash: str
    output_path: str
    metadata: dict = field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────
# Processor
# ────────────────────────────────────────────────────────────────────


class AssetProcessor:
    """Validates, classifies, and processes uploaded project assets."""

    SUPPORTED_IMAGE_TYPES: frozenset = frozenset(
        {
            "image/png",
            "image/jpeg",
            "image/svg+xml",
            "image/x-icon",
            "image/webp",
        }
    )

    SUPPORTED_FONT_TYPES: frozenset = frozenset(
        {
            "font/ttf",
            "font/woff",
            "font/woff2",
            "font/otf",
            "application/x-font-ttf",
        }
    )

    SUPPORTED_DOC_TYPES: frozenset = frozenset(
        {
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    )

    MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50 MB
    MAX_IMAGE_SIZE: int = 10 * 1024 * 1024  # 10 MB

    # ── Validation ─────────────────────────────────────────────────

    def validate_asset(
        self,
        file_name: str,
        file_size: int,
        mime_type: str,
    ) -> AssetValidationResult:
        """Validate an uploaded file by name, size and MIME type.

        Returns an ``AssetValidationResult`` with ``valid=True`` when the
        file is acceptable, or ``valid=False`` with a populated ``errors``
        tuple describing what went wrong.
        """
        errors: list[str] = []

        # R29-FIX-8: Validate filename against path traversal before any processing.
        # A malicious filename like "../../etc/passwd" would escape the output directory
        # when used in get_output_path().
        import os
        basename = os.path.basename(file_name)
        if basename != file_name or ".." in file_name or file_name.startswith(("/", "\\")):
            errors.append("Invalid file name: path traversal characters detected.")
            return AssetValidationResult(
                valid=False,
                asset_type=AssetType.DOCUMENT,  # placeholder, irrelevant for invalid
                file_name=file_name,
                file_size_bytes=file_size,
                mime_type=mime_type,
                errors=tuple(errors),
            )

        # Detect type first (may raise if completely unknown).
        asset_type = self.detect_asset_type(file_name, mime_type)

        # ---- size checks ----
        if file_size <= 0:
            errors.append("File size must be greater than zero.")

        if file_size > self.MAX_FILE_SIZE:
            errors.append(
                f"File exceeds maximum size of {self.MAX_FILE_SIZE} bytes "
                f"({self.MAX_FILE_SIZE // (1024 * 1024)} MB)."
            )

        # Stricter limit for images.
        if (
            asset_type in (AssetType.LOGO, AssetType.IMAGE)
            and file_size > self.MAX_IMAGE_SIZE
        ):
            errors.append(
                f"Image exceeds maximum size of {self.MAX_IMAGE_SIZE} bytes "
                f"({self.MAX_IMAGE_SIZE // (1024 * 1024)} MB)."
            )

        # ---- mime / extension checks ----
        all_supported = (
            self.SUPPORTED_IMAGE_TYPES
            | self.SUPPORTED_FONT_TYPES
            | self.SUPPORTED_DOC_TYPES
            | {
                "video/mp4",
                "video/webm",
                "application/json",
                "application/x-yaml",
                "text/yaml",
                "text/plain",
                "text/sql",
                "text/csv",
                "application/zip",
                "application/x-zip-compressed",
            }
        )

        if mime_type not in all_supported:
            errors.append(f"Unsupported MIME type: {mime_type}")

        valid = len(errors) == 0

        logger.info(
            "asset.validated",
            file_name=file_name,
            asset_type=asset_type.value,
            valid=valid,
            errors=errors,
        )

        return AssetValidationResult(
            valid=valid,
            asset_type=asset_type,
            file_name=file_name,
            file_size_bytes=file_size,
            mime_type=mime_type,
            errors=tuple(errors),
            metadata={},
        )

    # ── Type Detection ─────────────────────────────────────────────

    def detect_asset_type(self, file_name: str, mime_type: str) -> AssetType:
        """Determine the ``AssetType`` from a file name and MIME type."""
        lower = file_name.lower()

        # Extension-based heuristics first (more specific).
        if lower.endswith((".sql",)):
            return AssetType.SQL_SCHEMA

        if lower.endswith((".csv",)):
            return AssetType.CSV_DATA

        if lower.endswith((".zip",)):
            return AssetType.CODEBASE_ZIP

        if lower.endswith((".json",)) or lower.endswith((".yaml", ".yml")):
            # Could be OpenAPI spec or Figma tokens — default to OpenAPI.
            if "figma" in lower or "tokens" in lower:
                return AssetType.FIGMA_TOKENS
            return AssetType.OPENAPI_SPEC

        # MIME-based matching.
        if mime_type in self.SUPPORTED_FONT_TYPES:
            return AssetType.FONT

        if mime_type in self.SUPPORTED_DOC_TYPES:
            return AssetType.DOCUMENT

        if mime_type in self.SUPPORTED_IMAGE_TYPES:
            # Logos are usually named with "logo" or are ICO/SVG.
            if (
                "logo" in lower
                or mime_type == "image/x-icon"
                or mime_type == "image/svg+xml"
            ):
                return AssetType.LOGO
            return AssetType.IMAGE

        if mime_type.startswith("video/"):
            return AssetType.VIDEO

        if mime_type in ("text/sql",):
            return AssetType.SQL_SCHEMA

        if mime_type in ("text/csv",):
            return AssetType.CSV_DATA

        if mime_type in ("application/zip", "application/x-zip-compressed"):
            return AssetType.CODEBASE_ZIP

        if mime_type in ("application/json", "application/x-yaml", "text/yaml"):
            return AssetType.OPENAPI_SPEC

        if mime_type in ("text/plain",):
            return AssetType.DOCUMENT

        # Fallback
        return AssetType.DOCUMENT

    # ── Output Paths ───────────────────────────────────────────────

    def get_output_path(
        self,
        asset_type: AssetType,
        file_name: str,
        framework: str,
    ) -> str:
        """Return the placement path for an asset inside the generated project.

        The path is relative to the project root and depends on the
        framework being generated (e.g. ``react``, ``nextjs``, ``vue``).
        """
        # Normalise framework name.
        fw = framework.lower().replace(" ", "").replace("-", "")

        # Frontend-first frameworks put assets under a frontend directory.
        frontend_dir = "frontend" if fw not in ("express", "fastapi", "django") else "."

        path_map: dict[AssetType, str] = {
            AssetType.LOGO: f"{frontend_dir}/public/{file_name}",
            AssetType.FONT: f"{frontend_dir}/public/fonts/{file_name}",
            AssetType.IMAGE: f"{frontend_dir}/public/images/{file_name}",
            AssetType.VIDEO: f"{frontend_dir}/public/videos/{file_name}",
            AssetType.COLOR_PALETTE: f"{frontend_dir}/src/styles/colors.json",
            AssetType.DOCUMENT: f"docs/{file_name}",
            AssetType.OPENAPI_SPEC: f"specs/{file_name}",
            AssetType.SQL_SCHEMA: f"database/{file_name}",
            AssetType.CSV_DATA: f"data/{file_name}",
            AssetType.FIGMA_TOKENS: f"{frontend_dir}/src/styles/tokens.json",
            AssetType.CODEBASE_ZIP: f"reference/{file_name}",
        }

        return path_map.get(asset_type, f"assets/{file_name}")

    # ── Color Palette ──────────────────────────────────────────────

    def extract_color_palette(self, hex_colors: list[str]) -> dict[str, str]:
        """Validate a list of hex color strings and map them to design roles.

        Expects up to five colours.  If fewer are provided the missing
        roles are filled with sensible defaults.  Raises ``ValueError``
        when *any* supplied colour is invalid.

        Returns a dict with keys: primary, secondary, accent, background, text.
        """
        for c in hex_colors:
            if not self.validate_hex_color(c):
                raise ValueError(f"Invalid hex color: {c}")

        role_keys = ["primary", "secondary", "accent", "background", "text"]
        defaults = ["#3B82F6", "#10B981", "#F59E0B", "#FFFFFF", "#111827"]

        palette: dict[str, str] = {}
        for idx, key in enumerate(role_keys):
            if idx < len(hex_colors):
                palette[key] = hex_colors[idx].upper()
            else:
                palette[key] = defaults[idx]

        logger.info("asset.color_palette_extracted", palette=palette)
        return palette

    def validate_hex_color(self, color: str) -> bool:
        """Return ``True`` if *color* is a valid ``#RGB`` or ``#RRGGBB`` hex string."""
        if not isinstance(color, str):
            return False
        return bool(re.fullmatch(r"#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3})?", color))

    # ── OpenAPI Spec Processing ────────────────────────────────────

    def process_openapi_spec(self, content: str) -> dict:
        """Parse an OpenAPI specification from JSON or YAML text.

        Returns a dict with ``title``, ``version``, ``endpoints`` (list of
        dicts with ``path``, ``method``, ``summary``), and
        ``endpoint_count``.

        Raises ``ValueError`` on unparseable content.
        """
        spec: dict[str, Any] | None = None

        # Try JSON first.
        try:
            spec = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            pass  # Expected: type mismatch — fall through to default

        # Try YAML if JSON failed.
        if spec is None:
            try:
                import yaml  # optional dependency

                spec = yaml.safe_load(content)
            except Exception:
                pass  # Non-critical — error logged upstream or handled by caller

        if spec is None or not isinstance(spec, dict):
            raise ValueError("Unable to parse OpenAPI spec as JSON or YAML.")

        info = spec.get("info", {})
        paths = spec.get("paths", {})

        endpoints: list[dict[str, str]] = []
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, details in methods.items():
                if method.lower() in ("get", "post", "put", "patch", "delete", "head", "options"):
                    summary = ""
                    if isinstance(details, dict):
                        summary = details.get("summary", "")
                    endpoints.append(
                        {
                            "path": path,
                            "method": method.upper(),
                            "summary": summary,
                        }
                    )

        result = {
            "title": info.get("title", ""),
            "version": info.get("version", ""),
            "endpoints": endpoints,
            "endpoint_count": len(endpoints),
        }

        logger.info(
            "asset.openapi_processed",
            title=result["title"],
            endpoint_count=result["endpoint_count"],
        )
        return result

    # ── SQL Schema Processing ──────────────────────────────────────

    def process_sql_schema(self, content: str) -> dict:
        """Parse SQL DDL content and extract table information.

        Returns a dict with ``tables`` (list of table name strings) and
        ``table_count``.
        """
        # Match CREATE TABLE statements (case-insensitive).
        pattern = re.compile(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:`|\"|)(\w+)(?:`|\"|)",
            re.IGNORECASE,
        )
        tables = pattern.findall(content)

        result = {
            "tables": tables,
            "table_count": len(tables),
        }

        logger.info("asset.sql_schema_processed", table_count=result["table_count"])
        return result

    # ── CSV Data Processing ────────────────────────────────────────

    def process_csv_data(self, content: str) -> dict:
        """Parse CSV content and extract column information.

        Returns a dict with ``columns`` (list of header strings),
        ``column_count``, ``row_count`` (data rows, excluding header),
        and ``sample_rows`` (up to 5 rows as lists of strings).
        """
        reader = csv.reader(io.StringIO(content))

        # R38-FIX: Stream CSV — only read header + sample rows instead of
        # materializing entire file. A 50MB CSV can expand to several GB
        # of Python objects due to per-cell string overhead.
        header = next(reader, None)
        if header is None:
            return {
                "columns": [],
                "column_count": 0,
                "row_count": 0,
                "sample_rows": [],
            }
        sample_rows = []
        row_count = 0
        for row in reader:
            row_count += 1
            if len(sample_rows) < 5:
                sample_rows.append(row)

        result = {
            "columns": header,
            "column_count": len(header),
            "row_count": row_count,
            "sample_rows": sample_rows,
        }

        logger.info(
            "asset.csv_processed",
            column_count=result["column_count"],
            row_count=result["row_count"],
        )
        return result

    # ── Content Hashing ────────────────────────────────────────────

    def compute_content_hash(self, content: bytes) -> str:
        """Return the SHA-256 hex digest of *content*."""
        return hashlib.sha256(content).hexdigest()

    # ── Full Processing Pipeline ───────────────────────────────────

    def process_asset(
        self,
        file_name: str,
        file_size: int,
        mime_type: str,
        content: bytes,
        framework: str = "react",
    ) -> ProcessedAsset:
        """Validate and process a single uploaded asset end-to-end.

        Raises ``ValueError`` if validation fails.
        """
        validation = self.validate_asset(file_name, file_size, mime_type)
        if not validation.valid:
            raise ValueError(
                f"Asset validation failed for {file_name}: "
                + "; ".join(validation.errors)
            )

        content_hash = self.compute_content_hash(content)
        output_path = self.get_output_path(validation.asset_type, file_name, framework)

        processed = ProcessedAsset(
            asset_type=validation.asset_type,
            file_name=file_name,
            content_hash=content_hash,
            output_path=output_path,
            metadata={
                "mime_type": mime_type,
                "file_size_bytes": file_size,
                "framework": framework,
            },
        )

        logger.info(
            "asset.processed",
            file_name=file_name,
            asset_type=processed.asset_type.value,
            output_path=output_path,
        )
        return processed


# ────────────────────────────────────────────────────────────────────
# Singleton
# ────────────────────────────────────────────────────────────────────

_processor: AssetProcessor | None = None


def get_asset_processor() -> AssetProcessor:
    """Return the module-level ``AssetProcessor`` singleton."""
    global _processor
    if _processor is None:
        _processor = AssetProcessor()
    return _processor
