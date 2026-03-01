"""Phase 9 -- Asset Handling: tests for Gaps 193-205.

Covers:
- AssetType enum values and string behaviour
- AssetValidationResult frozen dataclass
- ProcessedAsset frozen dataclass
- AssetProcessor.validate_asset (images, fonts, oversized, unsupported, edge cases)
- AssetProcessor.detect_asset_type (extension and MIME heuristics)
- AssetProcessor.get_output_path (framework-aware placement)
- AssetProcessor.extract_color_palette (valid, invalid, partial)
- AssetProcessor.validate_hex_color (valid and invalid patterns)
- AssetProcessor.process_openapi_spec (valid JSON, invalid input)
- AssetProcessor.process_sql_schema (single / multiple tables)
- AssetProcessor.process_csv_data (headers, data rows, empty)
- Singleton get_asset_processor
"""

from __future__ import annotations

import json

import pytest

from app.services.asset_processor import (
    AssetProcessor,
    AssetType,
    AssetValidationResult,
    ProcessedAsset,
    get_asset_processor,
)


# ════════════════════════════════════════════════════════════════════
# Section 1 -- AssetType Enum
# ════════════════════════════════════════════════════════════════════


class TestAssetTypeEnum:
    """Verify all 11 AssetType members exist and behave as str enums."""

    def test_logo_value(self):
        assert AssetType.LOGO == "logo"

    def test_font_value(self):
        assert AssetType.FONT == "font"

    def test_color_palette_value(self):
        assert AssetType.COLOR_PALETTE == "color_palette"

    def test_image_value(self):
        assert AssetType.IMAGE == "image"

    def test_video_value(self):
        assert AssetType.VIDEO == "video"

    def test_document_value(self):
        assert AssetType.DOCUMENT == "document"

    def test_openapi_spec_value(self):
        assert AssetType.OPENAPI_SPEC == "openapi_spec"

    def test_sql_schema_value(self):
        assert AssetType.SQL_SCHEMA == "sql_schema"

    def test_csv_data_value(self):
        assert AssetType.CSV_DATA == "csv_data"

    def test_figma_tokens_value(self):
        assert AssetType.FIGMA_TOKENS == "figma_tokens"

    def test_codebase_zip_value(self):
        assert AssetType.CODEBASE_ZIP == "codebase_zip"

    def test_member_count(self):
        assert len(AssetType) == 11

    def test_is_str_subclass(self):
        assert isinstance(AssetType.LOGO, str)


# ════════════════════════════════════════════════════════════════════
# Section 2 -- AssetValidationResult Dataclass
# ════════════════════════════════════════════════════════════════════


class TestAssetValidationResult:
    """AssetValidationResult is frozen and carries expected fields."""

    def test_create_valid_result(self):
        r = AssetValidationResult(
            valid=True,
            asset_type=AssetType.IMAGE,
            file_name="photo.png",
            file_size_bytes=1024,
            mime_type="image/png",
        )
        assert r.valid is True
        assert r.file_name == "photo.png"
        assert r.errors == ()
        assert r.metadata == {}

    def test_create_invalid_result_with_errors(self):
        r = AssetValidationResult(
            valid=False,
            asset_type=AssetType.IMAGE,
            file_name="big.png",
            file_size_bytes=99_999_999,
            mime_type="image/png",
            errors=("File too large",),
        )
        assert r.valid is False
        assert len(r.errors) == 1

    def test_frozen_cannot_modify(self):
        r = AssetValidationResult(
            valid=True,
            asset_type=AssetType.FONT,
            file_name="a.ttf",
            file_size_bytes=500,
            mime_type="font/ttf",
        )
        with pytest.raises(AttributeError):
            r.valid = False  # type: ignore[misc]


# ════════════════════════════════════════════════════════════════════
# Section 3 -- ProcessedAsset Dataclass
# ════════════════════════════════════════════════════════════════════


class TestProcessedAsset:
    """ProcessedAsset is frozen and stores processing output."""

    def test_create_processed_asset(self):
        a = ProcessedAsset(
            asset_type=AssetType.LOGO,
            file_name="logo.svg",
            content_hash="abc123",
            output_path="frontend/public/logo.svg",
        )
        assert a.asset_type == AssetType.LOGO
        assert a.content_hash == "abc123"
        assert a.metadata == {}

    def test_frozen_cannot_modify(self):
        a = ProcessedAsset(
            asset_type=AssetType.LOGO,
            file_name="logo.svg",
            content_hash="abc123",
            output_path="frontend/public/logo.svg",
        )
        with pytest.raises(AttributeError):
            a.file_name = "other.svg"  # type: ignore[misc]

    def test_metadata_field(self):
        a = ProcessedAsset(
            asset_type=AssetType.IMAGE,
            file_name="bg.png",
            content_hash="def456",
            output_path="frontend/public/images/bg.png",
            metadata={"width": 1920, "height": 1080},
        )
        assert a.metadata["width"] == 1920


# ════════════════════════════════════════════════════════════════════
# Section 4 -- validate_asset
# ════════════════════════════════════════════════════════════════════


class TestValidateAsset:
    """AssetProcessor.validate_asset: type & size validation."""

    @pytest.fixture()
    def processor(self) -> AssetProcessor:
        return AssetProcessor()

    # -- valid cases --

    def test_valid_png_image(self, processor: AssetProcessor):
        r = processor.validate_asset("photo.png", 5000, "image/png")
        assert r.valid is True
        assert r.asset_type == AssetType.IMAGE

    def test_valid_jpeg_image(self, processor: AssetProcessor):
        r = processor.validate_asset("photo.jpg", 8000, "image/jpeg")
        assert r.valid is True

    def test_valid_svg_logo(self, processor: AssetProcessor):
        r = processor.validate_asset("logo.svg", 2000, "image/svg+xml")
        assert r.valid is True
        assert r.asset_type == AssetType.LOGO

    def test_valid_ttf_font(self, processor: AssetProcessor):
        r = processor.validate_asset("roboto.ttf", 120_000, "font/ttf")
        assert r.valid is True
        assert r.asset_type == AssetType.FONT

    def test_valid_woff2_font(self, processor: AssetProcessor):
        r = processor.validate_asset("inter.woff2", 80_000, "font/woff2")
        assert r.valid is True
        assert r.asset_type == AssetType.FONT

    def test_valid_pdf_document(self, processor: AssetProcessor):
        r = processor.validate_asset("spec.pdf", 300_000, "application/pdf")
        assert r.valid is True
        assert r.asset_type == AssetType.DOCUMENT

    def test_valid_json_spec(self, processor: AssetProcessor):
        r = processor.validate_asset("openapi.json", 10_000, "application/json")
        assert r.valid is True
        assert r.asset_type == AssetType.OPENAPI_SPEC

    def test_valid_sql_file(self, processor: AssetProcessor):
        r = processor.validate_asset("schema.sql", 5_000, "text/sql")
        assert r.valid is True
        assert r.asset_type == AssetType.SQL_SCHEMA

    def test_valid_csv_file(self, processor: AssetProcessor):
        r = processor.validate_asset("data.csv", 2_000, "text/csv")
        assert r.valid is True
        assert r.asset_type == AssetType.CSV_DATA

    def test_valid_zip_file(self, processor: AssetProcessor):
        r = processor.validate_asset("codebase.zip", 1_000_000, "application/zip")
        assert r.valid is True
        assert r.asset_type == AssetType.CODEBASE_ZIP

    # -- invalid cases --

    def test_oversized_file_rejected(self, processor: AssetProcessor):
        size = 60 * 1024 * 1024  # 60 MB
        r = processor.validate_asset("huge.zip", size, "application/zip")
        assert r.valid is False
        assert any("maximum size" in e.lower() for e in r.errors)

    def test_oversized_image_rejected(self, processor: AssetProcessor):
        size = 15 * 1024 * 1024  # 15 MB
        r = processor.validate_asset("large.png", size, "image/png")
        assert r.valid is False
        assert any("image" in e.lower() for e in r.errors)

    def test_zero_size_rejected(self, processor: AssetProcessor):
        r = processor.validate_asset("empty.png", 0, "image/png")
        assert r.valid is False
        assert any("greater than zero" in e for e in r.errors)

    def test_unsupported_mime_rejected(self, processor: AssetProcessor):
        r = processor.validate_asset("game.exe", 1000, "application/x-msdownload")
        assert r.valid is False
        assert any("unsupported" in e.lower() for e in r.errors)


# ════════════════════════════════════════════════════════════════════
# Section 5 -- detect_asset_type
# ════════════════════════════════════════════════════════════════════


class TestDetectAssetType:
    """AssetProcessor.detect_asset_type: extension & MIME heuristics."""

    @pytest.fixture()
    def processor(self) -> AssetProcessor:
        return AssetProcessor()

    def test_png_image(self, processor: AssetProcessor):
        assert processor.detect_asset_type("photo.png", "image/png") == AssetType.IMAGE

    def test_logo_by_name(self, processor: AssetProcessor):
        assert processor.detect_asset_type("logo.png", "image/png") == AssetType.LOGO

    def test_ico_is_logo(self, processor: AssetProcessor):
        assert processor.detect_asset_type("favicon.ico", "image/x-icon") == AssetType.LOGO

    def test_svg_is_logo(self, processor: AssetProcessor):
        assert processor.detect_asset_type("icon.svg", "image/svg+xml") == AssetType.LOGO

    def test_ttf_is_font(self, processor: AssetProcessor):
        assert processor.detect_asset_type("roboto.ttf", "font/ttf") == AssetType.FONT

    def test_woff2_is_font(self, processor: AssetProcessor):
        assert processor.detect_asset_type("inter.woff2", "font/woff2") == AssetType.FONT

    def test_json_is_openapi(self, processor: AssetProcessor):
        assert processor.detect_asset_type("spec.json", "application/json") == AssetType.OPENAPI_SPEC

    def test_yaml_is_openapi(self, processor: AssetProcessor):
        assert processor.detect_asset_type("api.yaml", "application/x-yaml") == AssetType.OPENAPI_SPEC

    def test_sql_is_schema(self, processor: AssetProcessor):
        assert processor.detect_asset_type("schema.sql", "text/sql") == AssetType.SQL_SCHEMA

    def test_csv_is_data(self, processor: AssetProcessor):
        assert processor.detect_asset_type("seed.csv", "text/csv") == AssetType.CSV_DATA

    def test_zip_is_codebase(self, processor: AssetProcessor):
        assert processor.detect_asset_type("project.zip", "application/zip") == AssetType.CODEBASE_ZIP

    def test_figma_tokens_json(self, processor: AssetProcessor):
        assert processor.detect_asset_type("figma-tokens.json", "application/json") == AssetType.FIGMA_TOKENS

    def test_pdf_is_document(self, processor: AssetProcessor):
        assert processor.detect_asset_type("readme.pdf", "application/pdf") == AssetType.DOCUMENT

    def test_video_mp4(self, processor: AssetProcessor):
        assert processor.detect_asset_type("demo.mp4", "video/mp4") == AssetType.VIDEO


# ════════════════════════════════════════════════════════════════════
# Section 6 -- get_output_path
# ════════════════════════════════════════════════════════════════════


class TestGetOutputPath:
    """AssetProcessor.get_output_path: framework-aware placement."""

    @pytest.fixture()
    def processor(self) -> AssetProcessor:
        return AssetProcessor()

    def test_logo_react(self, processor: AssetProcessor):
        p = processor.get_output_path(AssetType.LOGO, "logo.svg", "react")
        assert p == "frontend/public/logo.svg"

    def test_font_nextjs(self, processor: AssetProcessor):
        p = processor.get_output_path(AssetType.FONT, "inter.woff2", "nextjs")
        assert p == "frontend/public/fonts/inter.woff2"

    def test_image_vue(self, processor: AssetProcessor):
        p = processor.get_output_path(AssetType.IMAGE, "bg.png", "vue")
        assert p == "frontend/public/images/bg.png"

    def test_document_react(self, processor: AssetProcessor):
        p = processor.get_output_path(AssetType.DOCUMENT, "spec.pdf", "react")
        assert p == "docs/spec.pdf"

    def test_openapi_spec_path(self, processor: AssetProcessor):
        p = processor.get_output_path(AssetType.OPENAPI_SPEC, "api.json", "react")
        assert p == "specs/api.json"

    def test_sql_schema_path(self, processor: AssetProcessor):
        p = processor.get_output_path(AssetType.SQL_SCHEMA, "schema.sql", "react")
        assert p == "database/schema.sql"

    def test_csv_data_path(self, processor: AssetProcessor):
        p = processor.get_output_path(AssetType.CSV_DATA, "data.csv", "react")
        assert p == "data/data.csv"

    def test_codebase_zip_path(self, processor: AssetProcessor):
        p = processor.get_output_path(AssetType.CODEBASE_ZIP, "ref.zip", "react")
        assert p == "reference/ref.zip"

    def test_express_backend_framework_no_frontend_prefix(self, processor: AssetProcessor):
        p = processor.get_output_path(AssetType.LOGO, "logo.png", "express")
        assert p == "./public/logo.png"

    def test_django_backend_framework_no_frontend_prefix(self, processor: AssetProcessor):
        p = processor.get_output_path(AssetType.FONT, "font.ttf", "django")
        assert p == "./public/fonts/font.ttf"


# ════════════════════════════════════════════════════════════════════
# Section 7 -- extract_color_palette
# ════════════════════════════════════════════════════════════════════


class TestExtractColorPalette:
    """AssetProcessor.extract_color_palette: validation & mapping."""

    @pytest.fixture()
    def processor(self) -> AssetProcessor:
        return AssetProcessor()

    def test_five_valid_colors(self, processor: AssetProcessor):
        colors = ["#FF0000", "#00FF00", "#0000FF", "#FFFFFF", "#000000"]
        result = processor.extract_color_palette(colors)
        assert result["primary"] == "#FF0000"
        assert result["secondary"] == "#00FF00"
        assert result["accent"] == "#0000FF"
        assert result["background"] == "#FFFFFF"
        assert result["text"] == "#000000"

    def test_partial_colors_filled_with_defaults(self, processor: AssetProcessor):
        result = processor.extract_color_palette(["#ABC"])
        assert result["primary"] == "#ABC"
        assert result["secondary"] == "#10B981"  # default
        assert len(result) == 5

    def test_empty_colors_all_defaults(self, processor: AssetProcessor):
        result = processor.extract_color_palette([])
        assert result["primary"] == "#3B82F6"
        assert len(result) == 5

    def test_invalid_hex_raises(self, processor: AssetProcessor):
        with pytest.raises(ValueError, match="Invalid hex color"):
            processor.extract_color_palette(["#GGG"])

    def test_color_values_uppercased(self, processor: AssetProcessor):
        result = processor.extract_color_palette(["#aabbcc"])
        assert result["primary"] == "#AABBCC"


# ════════════════════════════════════════════════════════════════════
# Section 8 -- validate_hex_color
# ════════════════════════════════════════════════════════════════════


class TestValidateHexColor:
    """AssetProcessor.validate_hex_color: pattern matching."""

    @pytest.fixture()
    def processor(self) -> AssetProcessor:
        return AssetProcessor()

    def test_valid_three_digit(self, processor: AssetProcessor):
        assert processor.validate_hex_color("#FFF") is True

    def test_valid_six_digit(self, processor: AssetProcessor):
        assert processor.validate_hex_color("#FFFFFF") is True

    def test_valid_lowercase(self, processor: AssetProcessor):
        assert processor.validate_hex_color("#aabbcc") is True

    def test_valid_mixed_case(self, processor: AssetProcessor):
        assert processor.validate_hex_color("#AaBbCc") is True

    def test_valid_black_short(self, processor: AssetProcessor):
        assert processor.validate_hex_color("#000") is True

    def test_valid_black_long(self, processor: AssetProcessor):
        assert processor.validate_hex_color("#000000") is True

    def test_invalid_no_hash(self, processor: AssetProcessor):
        assert processor.validate_hex_color("FFFFFF") is False

    def test_invalid_ggg(self, processor: AssetProcessor):
        assert processor.validate_hex_color("#GGG") is False

    def test_invalid_five_digits(self, processor: AssetProcessor):
        assert processor.validate_hex_color("#12345") is False

    def test_invalid_named_color(self, processor: AssetProcessor):
        assert processor.validate_hex_color("red") is False

    def test_invalid_empty(self, processor: AssetProcessor):
        assert processor.validate_hex_color("") is False

    def test_invalid_none(self, processor: AssetProcessor):
        assert processor.validate_hex_color(None) is False  # type: ignore[arg-type]

    def test_invalid_single_digit(self, processor: AssetProcessor):
        assert processor.validate_hex_color("#F") is False

    def test_invalid_four_digits(self, processor: AssetProcessor):
        assert processor.validate_hex_color("#FFFF") is False


# ════════════════════════════════════════════════════════════════════
# Section 9 -- process_openapi_spec
# ════════════════════════════════════════════════════════════════════


class TestProcessOpenAPISpec:
    """AssetProcessor.process_openapi_spec: JSON parsing & endpoint extraction."""

    @pytest.fixture()
    def processor(self) -> AssetProcessor:
        return AssetProcessor()

    def test_valid_json_spec(self, processor: AssetProcessor):
        spec = json.dumps(
            {
                "info": {"title": "Pet Store", "version": "1.0.0"},
                "paths": {
                    "/pets": {
                        "get": {"summary": "List all pets"},
                        "post": {"summary": "Create a pet"},
                    },
                    "/pets/{id}": {
                        "get": {"summary": "Get pet by ID"},
                        "delete": {"summary": "Delete a pet"},
                    },
                },
            }
        )
        result = processor.process_openapi_spec(spec)
        assert result["title"] == "Pet Store"
        assert result["version"] == "1.0.0"
        assert result["endpoint_count"] == 4
        methods = {e["method"] for e in result["endpoints"]}
        assert "GET" in methods
        assert "POST" in methods
        assert "DELETE" in methods

    def test_spec_with_no_paths(self, processor: AssetProcessor):
        spec = json.dumps({"info": {"title": "Empty", "version": "0.1"}, "paths": {}})
        result = processor.process_openapi_spec(spec)
        assert result["endpoint_count"] == 0
        assert result["endpoints"] == []

    def test_invalid_json_raises(self, processor: AssetProcessor):
        with pytest.raises(ValueError, match="Unable to parse"):
            processor.process_openapi_spec("not valid json or yaml <<<>>>")

    def test_spec_missing_info(self, processor: AssetProcessor):
        spec = json.dumps({"paths": {"/health": {"get": {"summary": "Health check"}}}})
        result = processor.process_openapi_spec(spec)
        assert result["title"] == ""
        assert result["endpoint_count"] == 1


# ════════════════════════════════════════════════════════════════════
# Section 10 -- process_sql_schema
# ════════════════════════════════════════════════════════════════════


class TestProcessSQLSchema:
    """AssetProcessor.process_sql_schema: DDL parsing."""

    @pytest.fixture()
    def processor(self) -> AssetProcessor:
        return AssetProcessor()

    def test_single_table(self, processor: AssetProcessor):
        sql = "CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(255));"
        result = processor.process_sql_schema(sql)
        assert result["tables"] == ["users"]
        assert result["table_count"] == 1

    def test_multiple_tables(self, processor: AssetProcessor):
        sql = """
        CREATE TABLE users (id INT PRIMARY KEY);
        CREATE TABLE orders (id INT PRIMARY KEY, user_id INT);
        CREATE TABLE products (id INT PRIMARY KEY, name TEXT);
        """
        result = processor.process_sql_schema(sql)
        assert result["table_count"] == 3
        assert "users" in result["tables"]
        assert "orders" in result["tables"]
        assert "products" in result["tables"]

    def test_if_not_exists(self, processor: AssetProcessor):
        sql = "CREATE TABLE IF NOT EXISTS settings (key TEXT, value TEXT);"
        result = processor.process_sql_schema(sql)
        assert result["tables"] == ["settings"]

    def test_no_tables(self, processor: AssetProcessor):
        result = processor.process_sql_schema("SELECT 1;")
        assert result["table_count"] == 0
        assert result["tables"] == []

    def test_quoted_table_name(self, processor: AssetProcessor):
        sql = 'CREATE TABLE "events" (id INT);'
        result = processor.process_sql_schema(sql)
        assert result["tables"] == ["events"]


# ════════════════════════════════════════════════════════════════════
# Section 11 -- process_csv_data
# ════════════════════════════════════════════════════════════════════


class TestProcessCSVData:
    """AssetProcessor.process_csv_data: column & row extraction."""

    @pytest.fixture()
    def processor(self) -> AssetProcessor:
        return AssetProcessor()

    def test_headers_and_rows(self, processor: AssetProcessor):
        csv_text = "name,age,email\nAlice,30,alice@ex.com\nBob,25,bob@ex.com"
        result = processor.process_csv_data(csv_text)
        assert result["columns"] == ["name", "age", "email"]
        assert result["column_count"] == 3
        assert result["row_count"] == 2
        assert len(result["sample_rows"]) == 2

    def test_empty_csv(self, processor: AssetProcessor):
        result = processor.process_csv_data("")
        assert result["columns"] == []
        assert result["column_count"] == 0
        assert result["row_count"] == 0

    def test_header_only(self, processor: AssetProcessor):
        result = processor.process_csv_data("col1,col2,col3\n")
        assert result["column_count"] == 3
        assert result["row_count"] == 0

    def test_sample_rows_capped_at_five(self, processor: AssetProcessor):
        rows = "id\n" + "\n".join(str(i) for i in range(20))
        result = processor.process_csv_data(rows)
        assert result["row_count"] == 20
        assert len(result["sample_rows"]) == 5


# ════════════════════════════════════════════════════════════════════
# Section 12 -- Singleton
# ════════════════════════════════════════════════════════════════════


class TestSingleton:
    """get_asset_processor returns the same instance."""

    def test_singleton_identity(self):
        a = get_asset_processor()
        b = get_asset_processor()
        assert a is b

    def test_singleton_is_asset_processor(self):
        assert isinstance(get_asset_processor(), AssetProcessor)
