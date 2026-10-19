"""Export helpers for WAAM XYZ point clouds."""

from __future__ import annotations

import csv
import math
import zipfile
from pathlib import Path
from typing import Mapping, Sequence
from xml.sax.saxutils import escape


POINT_CLOUD_COLUMNS = (
    "object",
    "index",
    "x_mm",
    "y_mm",
    "z_mm",
    "world_x_mm",
    "world_y_mm",
    "world_z_mm",
)

POINT_CLOUD_COORD_COLUMNS = frozenset(
    (
        "x_mm",
        "y_mm",
        "z_mm",
        "world_x_mm",
        "world_y_mm",
        "world_z_mm",
    )
)
POINT_CLOUD_DECIMALS = 5


def _normalise_rows(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    normalised = []
    for row in rows:
        clean = {}
        for key, value in row.items():
            if key in POINT_CLOUD_COORD_COLUMNS and isinstance(value, (int, float)) and not isinstance(value, bool):
                clean[key] = round(float(value), POINT_CLOUD_DECIMALS)
            else:
                clean[key] = value
        normalised.append(clean)
    return normalised


def write_point_cloud_csv(
    output_path: Path | str,
    rows: Sequence[Mapping[str, object]],
    *,
    columns: Sequence[str] = POINT_CLOUD_COLUMNS,
) -> Path:
    """Write point cloud rows to a CSV file and return the resolved path."""
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in _normalise_rows(rows):
            writer.writerow({column: row.get(column, "") for column in columns})
    return target


def _column_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell_ref(col_idx: int, row_idx: int) -> str:
    return f"{_column_name(col_idx)}{row_idx + 1}"


def _xml(value: object) -> str:
    return escape(str(value), {'"': "&quot;"})


def _xlsx_cell(value: object, col_idx: int, row_idx: int) -> str:
    ref = _cell_ref(col_idx, row_idx)
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return f'<c r="{ref}"><v>{float(value):.12g}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t>{_xml(value)}</t></is></c>'


def _sheet_xml(table: Sequence[Sequence[object]]) -> str:
    row_xml = []
    for row_idx, row in enumerate(table):
        cells = "".join(_xlsx_cell(value, col_idx, row_idx) for col_idx, value in enumerate(row))
        row_xml.append(f'<row r="{row_idx + 1}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        + "".join(row_xml)
        + "</sheetData></worksheet>"
    )


def _content_types_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/worksheets/sheet2.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )


def _root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def _workbook_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        '<sheet name="points" sheetId="1" r:id="rId1"/>'
        '<sheet name="metadata" sheetId="2" r:id="rId2"/>'
        "</sheets></workbook>"
    )


def _workbook_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet2.xml"/>'
        "</Relationships>"
    )


def write_point_cloud_xlsx(
    output_path: Path | str,
    rows: Sequence[Mapping[str, object]],
    *,
    columns: Sequence[str] = POINT_CLOUD_COLUMNS,
    metadata: Mapping[str, object] | None = None,
) -> Path:
    """Write point cloud rows to a minimal Excel workbook."""
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    rows_list = _normalise_rows(rows)
    point_table: list[list[object]] = [list(columns)]
    point_table.extend([[row.get(column, "") for column in columns] for row in rows_list])

    meta = dict(metadata or {})
    meta.setdefault("point_count", len(rows_list))
    metadata_table: list[list[object]] = [["key", "value"]]
    metadata_table.extend([[key, value] for key, value in sorted(meta.items())])

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", _content_types_xml())
        workbook.writestr("_rels/.rels", _root_rels_xml())
        workbook.writestr("xl/workbook.xml", _workbook_xml())
        workbook.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml())
        workbook.writestr("xl/worksheets/sheet1.xml", _sheet_xml(point_table))
        workbook.writestr("xl/worksheets/sheet2.xml", _sheet_xml(metadata_table))
    return target


def write_point_cloud_table(
    output_path: Path | str,
    rows: Sequence[Mapping[str, object]],
    *,
    file_format: str,
    columns: Sequence[str] = POINT_CLOUD_COLUMNS,
    metadata: Mapping[str, object] | None = None,
) -> Path:
    """Write point cloud rows as CSV or XLSX."""
    fmt = str(file_format or "csv").strip().lower()
    if fmt == "xlsx":
        return write_point_cloud_xlsx(output_path, rows, columns=columns, metadata=metadata)
    return write_point_cloud_csv(output_path, rows, columns=columns)


__all__ = [
    "POINT_CLOUD_COLUMNS",
    "POINT_CLOUD_COORD_COLUMNS",
    "POINT_CLOUD_DECIMALS",
    "write_point_cloud_csv",
    "write_point_cloud_table",
    "write_point_cloud_xlsx",
]
