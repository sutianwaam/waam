"""Regenerating NC twice from identical input must produce identical bytes.

Any drift here (timestamps, dict ordering, randomness) is a determinism
defect in the generator itself, independent of whether the golden fixture
is up to date.
"""

from __future__ import annotations

from Waam_tech.waamgen.pipeline import generate_nc


def test_generate_nc_is_repeatable(tmp_path, golden_plan_path):
    out1 = tmp_path / "run1.nc"
    out2 = tmp_path / "run2.nc"
    generate_nc(golden_plan_path, output_path=out1, attempt_dir=tmp_path / "attempts1")
    generate_nc(golden_plan_path, output_path=out2, attempt_dir=tmp_path / "attempts2")
    assert out1.read_bytes() == out2.read_bytes()
