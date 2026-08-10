import csv
import io
from pathlib import Path

from backend_core.imports.adapters import HuitunCsvAdapter, HuitunExcelAdapter
from backend_core.imports.mappings import HUITUN_FIELD_MAPPING
from backend_core.imports.parsers import parse_csv, parse_xlsx
from openpyxl import Workbook

FIXTURE = Path(__file__).parents[3] / "tests" / "fixtures" / "huitun_sanitized_37_columns.csv"


def xlsx_from_sanitized_csv() -> bytes:
    with FIXTURE.open(encoding="utf-8", newline="") as source:
        rows = list(csv.reader(source))
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    for row in rows:
        worksheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_sanitized_huitun_csv_and_xlsx_fixtures_keep_the_real_37_field_shape() -> None:
    csv_table = parse_csv(FIXTURE.read_bytes(), "text/csv")
    xlsx_table = parse_xlsx(
        xlsx_from_sanitized_csv(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert csv_table.headers == xlsx_table.headers == list(HUITUN_FIELD_MAPPING)
    assert len(csv_table.rows) == len(xlsx_table.rows) == 2
    csv_adapter = HuitunCsvAdapter()
    xlsx_adapter = HuitunExcelAdapter()
    csv_adapter.mapping_for_headers(csv_table.headers)
    xlsx_adapter.mapping_for_headers(xlsx_table.headers)
    adapted = [
        *(csv_adapter.adapt(row) for row in csv_table.rows),
        *(xlsx_adapter.adapt(row) for row in xlsx_table.rows),
    ]
    assert all(row.is_valid for row in adapted)
    assert all(
        (row.record.platform_identity.platform_account_id or "").startswith("sanitizedFixture")
        for row in adapted
    )
    assert {contact.normalized_value for row in adapted for contact in row.record.contacts} == {
        "fixture-a@example.test"
    }
