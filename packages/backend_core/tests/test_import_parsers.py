import asyncio
import hashlib
import io
import stat
import zipfile
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from backend_core.imports.enums import StoredFileType
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.parsers import (
    ParserLimits,
    parse_csv,
    parse_xlsx,
    validate_upload_type,
)
from backend_core.imports.storage import LocalStorageAdapter
from openpyxl import Workbook


def assert_import_error(error: ImportDomainError, code: str) -> None:
    assert error.code == code
    assert error.message


def make_xlsx(*rows: list[object]) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    for row in rows:
        worksheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def add_zip_entry(content: bytes, name: str, payload: bytes) -> bytes:
    source = io.BytesIO(content)
    output = io.BytesIO()
    with (
        zipfile.ZipFile(source) as existing,
        zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as rewritten,
    ):
        for entry in existing.infolist():
            rewritten.writestr(entry, existing.read(entry))
        rewritten.writestr(name, payload)
    return output.getvalue()


async def chunks(*values: bytes) -> AsyncIterator[bytes]:
    for value in values:
        yield value


def test_csv_utf8_bom_and_invalid_row_width_continue_parsing() -> None:
    content = (
        "\ufeffname,email\nAlpha,alpha@example.test\nShort\nOmega,omega@example.test\n"
    ).encode()

    table = parse_csv(content, "text/csv; charset=utf-8")

    assert table.file_type == StoredFileType.CSV
    assert table.encoding == "utf-8-sig"
    assert table.delimiter == ","
    assert table.headers == ["name", "email"]
    assert len(table.rows) == 3
    assert table.rows[0].values == {"name": "Alpha", "email": "alpha@example.test"}
    assert table.rows[1].values == {"name": "Short", "email": ""}
    assert table.rows[1].raw_data["__raw_values__"] == ["Short"]
    assert table.rows[1].errors == [
        {
            "code": "INVALID_ROW_WIDTH",
            "message": "Row has 1 fields; expected 2",
        }
    ]
    assert table.rows[2].row_number == 4
    assert table.rows[2].errors == []


def test_csv_gb18030_is_detected_without_corrupting_text() -> None:
    content = "达人名称,地域\n示例达人,上海\n".encode("gb18030")

    table = parse_csv(content, "application/vnd.ms-excel")

    assert table.encoding == "gb18030"
    assert table.rows[0].values == {"达人名称": "示例达人", "地域": "上海"}


@pytest.mark.parametrize(
    ("operation", "expected_code"),
    [
        (lambda: parse_csv(b"name\nvalue\n", "application/pdf"), "MIME_MISMATCH"),
        (lambda: parse_csv(b"%PDF-1.7\nname,value\n", "text/csv"), "MIME_MISMATCH"),
        (lambda: parse_csv(b"name,value\nalpha,evil\x00value\n", "text/csv"), "INVALID_CSV"),
        (
            lambda: validate_upload_type(
                b"PK\x03\x04not-a-csv",
                suffix=".csv",
                declared_mime="text/csv",
                limits=ParserLimits(),
            ),
            "MIME_MISMATCH",
        ),
        (
            lambda: validate_upload_type(
                b"name,value\nalpha,beta\n",
                suffix=".xlsm",
                declared_mime="application/vnd.ms-excel",
                limits=ParserLimits(),
            ),
            "INVALID_FILE_EXTENSION",
        ),
    ],
)
def test_csv_mime_magic_controls_and_extensions_are_rejected(
    operation: Callable[[], object], expected_code: str
) -> None:
    with pytest.raises(ImportDomainError) as caught:
        operation()
    assert_import_error(caught.value, expected_code)


def test_xlsx_parses_values_and_ignores_formula_cells() -> None:
    content = make_xlsx(
        ["name", "score", "calculated"],
        ["Alpha", 12.5, "=1+1"],
        ["Omega", 8, "plain"],
    )

    table = parse_xlsx(content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    assert table.file_type == StoredFileType.XLSX
    assert table.headers == ["name", "score", "calculated"]
    assert table.rows[0].values == {"name": "Alpha", "score": "12.5", "calculated": ""}
    assert table.rows[0].raw_data["calculated"] == "=1+1"
    assert table.rows[0].warnings == [{"code": "FORMULA_IGNORED", "location": "2:3"}]
    assert table.rows[1].values["calculated"] == "plain"


def test_xlsx_rejects_formula_header() -> None:
    content = make_xlsx(["name", "=1+1"], ["Alpha", "value"])

    with pytest.raises(ImportDomainError) as caught:
        parse_xlsx(content, "application/octet-stream")

    assert_import_error(caught.value, "INVALID_HEADER")


@pytest.mark.parametrize(
    ("mutate", "limits"),
    [
        (lambda value: add_zip_entry(value, "xl/vbaProject.bin", b"macro"), ParserLimits()),
        (lambda value: add_zip_entry(value, "../escaped.xml", b"unsafe"), ParserLimits()),
        (
            lambda value: add_zip_entry(
                value,
                "xl/_rels/worksheet.xml.rels",
                b'<Relationships><Relationship TargetMode="External" /></Relationships>',
            ),
            ParserLimits(),
        ),
        (lambda value: add_zip_entry(value, "large.bin", b"0" * 50_000), ParserLimits()),
        (lambda value: value, ParserLimits(max_xlsx_entries=1)),
    ],
)
def test_xlsx_rejects_macros_and_unsafe_archives(
    mutate: Callable[[bytes], bytes], limits: ParserLimits
) -> None:
    content = mutate(make_xlsx(["name"], ["Alpha"]))

    with pytest.raises(ImportDomainError) as caught:
        parse_xlsx(content, "application/zip", limits)

    assert_import_error(caught.value, "UNSAFE_XLSX")


def test_xlsx_mime_and_magic_must_agree() -> None:
    content = make_xlsx(["name"], ["Alpha"])

    with pytest.raises(ImportDomainError) as mime_error:
        parse_xlsx(content, "text/csv")
    assert_import_error(mime_error.value, "MIME_MISMATCH")

    with pytest.raises(ImportDomainError) as magic_error:
        parse_xlsx(b"not a zip archive", "application/zip")
    assert_import_error(magic_error.value, "MIME_MISMATCH")


def test_local_storage_hash_size_permissions_integrity_and_delete(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path / "imports"
        storage = LocalStorageAdapter(root)
        content = b"name,email\nAlpha,alpha@example.test\n"

        stored = await storage.store(
            chunks(content[:8], b"", content[8:]), suffix=".CSV", max_bytes=512
        )

        assert stored.storage_key.endswith(".csv")
        assert stored.storage_key != ".CSV"
        assert stored.sha256 == hashlib.sha256(content).hexdigest()
        assert stored.size == len(content)
        stored_path = root / stored.storage_key
        assert stored_path.read_bytes() == content
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(stored_path.stat().st_mode) == 0o600
        assert (
            await storage.read(
                stored.storage_key,
                expected_size=stored.size,
                expected_sha256=stored.sha256,
            )
            == content
        )

        with pytest.raises(ImportDomainError) as size_error:
            await storage.read(stored.storage_key, expected_size=stored.size + 1)
        assert_import_error(size_error.value, "FILE_INTEGRITY_FAILED")

        with pytest.raises(ImportDomainError) as hash_error:
            await storage.read(stored.storage_key, expected_sha256="0" * 64)
        assert_import_error(hash_error.value, "FILE_INTEGRITY_FAILED")

        await storage.delete(stored.storage_key)
        assert not stored_path.exists()
        await storage.delete(stored.storage_key)

    asyncio.run(scenario())


def test_local_storage_blocks_path_traversal(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = LocalStorageAdapter(tmp_path / "imports")
        outside = tmp_path / "outside.csv"
        outside.write_text("must remain", encoding="utf-8")

        with pytest.raises(ImportDomainError) as read_error:
            await storage.read("../outside.csv")
        assert_import_error(read_error.value, "INVALID_STORAGE_KEY")

        with pytest.raises(ImportDomainError) as delete_error:
            await storage.delete("../outside.csv")
        assert_import_error(delete_error.value, "INVALID_STORAGE_KEY")
        assert outside.read_text(encoding="utf-8") == "must remain"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("payload", "max_bytes", "expected_code"),
    [
        ((b"1234", b"5678"), 7, "FILE_TOO_LARGE"),
        ((b"",), 7, "EMPTY_FILE"),
    ],
)
def test_local_storage_failure_removes_partial_files(
    tmp_path: Path, payload: tuple[bytes, ...], max_bytes: int, expected_code: str
) -> None:
    async def scenario() -> None:
        root = tmp_path / expected_code.lower()
        storage = LocalStorageAdapter(root)

        with pytest.raises(ImportDomainError) as caught:
            await storage.store(chunks(*payload), suffix=".csv", max_bytes=max_bytes)

        assert_import_error(caught.value, expected_code)
        if expected_code == "FILE_TOO_LARGE":
            assert caught.value.status_code == 413
        assert list(root.iterdir()) == []

    asyncio.run(scenario())


def test_local_storage_rejects_unsupported_suffix_without_creating_file(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path / "imports"
        storage = LocalStorageAdapter(root)

        with pytest.raises(ImportDomainError) as caught:
            await storage.store(chunks(b"payload"), suffix=".xlsm", max_bytes=512)

        assert_import_error(caught.value, "INVALID_FILE_EXTENSION")
        assert list(root.iterdir()) == []

    asyncio.run(scenario())
