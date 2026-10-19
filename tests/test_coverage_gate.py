from __future__ import annotations

import sys
from types import SimpleNamespace

from Waam_tech.waamgen import pipeline


class FakeVector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        if all(hasattr(x, attr) for attr in ("x", "y", "z")):
            self.x = float(x.x)
            self.y = float(x.y)
            self.z = float(x.z)
        else:
            self.x = float(x)
            self.y = float(y)
            self.z = float(z)

    def __add__(self, other):
        return FakeVector(self.x + other.x, self.y + other.y, self.z + other.z)


class FakeRotation:
    def multVec(self, vec):
        return FakeVector(vec)


class FakePlacement:
    Base = FakeVector(0.0, 0.0, 0.0)
    Rotation = FakeRotation()

    def inverse(self):
        return self

    def multVec(self, vec):
        return FakeVector(vec)


class FakeShape:
    BoundBox = SimpleNamespace(XMin=0.0, XMax=10.0, YMin=0.0, YMax=2.0, ZMin=-1.0, ZMax=1.0)
    Solids = [object()]

    def isNull(self):
        return False

    def isInside(self, point, _tol=0.0, _strict=False):
        return (
            0.0 <= point.x <= 10.0
            and 0.0 <= point.y <= 2.0
            and -1.0 <= point.z <= 1.0
        )


def _job():
    return SimpleNamespace(
        post=SimpleNamespace(torch_on="M3", torch_off="M5"),
        deposition=SimpleNamespace(bead_width_mm=2.5, layer_height_mm=2.5),
    )


def _write_nc(path, moves):
    lines = ["%", "G21", "G90", "M3"]
    lines.extend(moves)
    lines.extend(["M5", "M30", "%"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_voxelize_deposition_path_ignores_travel_segments():
    covered = pipeline._voxelize_deposition_path(
        [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 2.0, 0.0)],
        [True, False],
        origin=(-0.5, -0.5, -0.5),
        dims=(4, 4, 2),
        voxel_size_mm=1.0,
        bead_width_mm=0.0,
        layer_height_mm=0.0,
        sample_step_mm=0.5,
    )

    assert pipeline._flatten_voxel_index(0, 0, 0, (4, 4, 2)) in covered
    assert pipeline._flatten_voxel_index(2, 0, 0, (4, 4, 2)) in covered
    assert pipeline._flatten_voxel_index(2, 2, 0, (4, 4, 2)) not in covered


def test_coverage_gate_compares_deposition_voxels_to_material_shape(tmp_path, monkeypatch):
    doc = SimpleNamespace(
        Name="CoverageDoc",
        ActiveObject=SimpleNamespace(Shape=FakeShape()),
        Objects=[],
    )
    fake_freecad = SimpleNamespace(
        Vector=FakeVector,
        ActiveDocument=doc,
        closeDocument=lambda _name: None,
    )
    monkeypatch.setitem(sys.modules, "FreeCAD", fake_freecad)
    monkeypatch.setattr(pipeline, "_open_coverage_document", lambda _path: (doc, False))
    monkeypatch.setattr(pipeline, "_coverage_wcs_placement", lambda _doc: FakePlacement())

    full_nc = tmp_path / "full.nc"
    _write_nc(full_nc, ["G1 X0.0 Y1.0 Z0.0 F100", "G1 X10.0 Y1.0 Z0.0 F100"])

    ok, pct = pipeline._run_coverage_gate(
        full_nc,
        _job(),
        doc_path=None,
        voxel_size_mm=1.0,
        bead_width_mm=2.5,
        layer_height_mm=2.5,
        sample_step_mm=0.5,
        max_voxels=10_000,
        threshold_pct=95.0,
    )

    assert ok is True
    assert pct >= 95.0

    short_nc = tmp_path / "short.nc"
    _write_nc(short_nc, ["G1 X0.0 Y1.0 Z0.0 F100", "G1 X1.0 Y1.0 Z0.0 F100"])

    ok, pct = pipeline._run_coverage_gate(
        short_nc,
        _job(),
        doc_path=None,
        voxel_size_mm=1.0,
        bead_width_mm=2.5,
        layer_height_mm=2.5,
        sample_step_mm=0.5,
        max_voxels=10_000,
        threshold_pct=95.0,
    )

    assert ok is False
    assert pct < 95.0
