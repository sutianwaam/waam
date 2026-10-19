from __future__ import annotations

import csv
import zipfile

from Waam_tech.core.point_cloud_export import write_point_cloud_csv, write_point_cloud_xlsx


def _rows():
    return [
        {
            "object": "part",
            "index": 1,
            "x_mm": 1.234567,
            "y_mm": 2.500004,
            "z_mm": 3.999996,
            "world_x_mm": 10.123456,
            "world_y_mm": 20.000004,
            "world_z_mm": 30.999996,
        }
    ]


def test_write_point_cloud_csv(tmp_path):
    path = write_point_cloud_csv(tmp_path / "points.csv", _rows())

    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows[0]["object"] == "part"
    assert rows[0]["x_mm"] == "1.23457"
    assert rows[0]["y_mm"] == "2.5"
    assert rows[0]["z_mm"] == "4.0"
    assert rows[0]["world_x_mm"] == "10.12346"
    assert rows[0]["world_z_mm"] == "31.0"


def test_write_point_cloud_xlsx(tmp_path):
    path = write_point_cloud_xlsx(
        tmp_path / "points.xlsx",
        _rows(),
        metadata={"coordinate_frame": "WAAM WCS", "spacing_mm": 5.0},
    )

    with zipfile.ZipFile(path) as workbook:
        names = set(workbook.namelist())
        assert "xl/worksheets/sheet1.xml" in names
        assert "xl/worksheets/sheet2.xml" in names
        sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        metadata = workbook.read("xl/worksheets/sheet2.xml").decode("utf-8")

    assert "x_mm" in sheet
    assert "1.23457" in sheet
    assert "10.12346" in sheet
    assert "coordinate_frame" in metadata
    assert "WAAM WCS" in metadata
