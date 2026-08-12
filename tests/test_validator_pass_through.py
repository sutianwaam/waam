"""Run the generator's own validator against its own output.

If waamgen can't validate the NC it just produced from the same plan, the
validator and generator have drifted apart from each other. This is the
project's own tool run against the project's own output, self-consistency.
"""

from __future__ import annotations

from Waam_tech.waamgen.pipeline import generate_nc, load_light_job, validate_gcode


def test_generated_baseline_nc_passes_its_own_validator(tmp_path, golden_plan_path):
    out_path = tmp_path / "candidate.nc"
    generate_nc(golden_plan_path, output_path=out_path, attempt_dir=tmp_path / "attempts")

    job = load_light_job(golden_plan_path)
    report = validate_gcode(out_path.read_text(encoding="utf-8"), job, geom_tol=0.8)

    assert report.syntax_ok, report.messages
    assert report.safety_ok, report.messages
    assert report.geometry_ok, report.messages
    assert report.ok


def test_golden_baseline_nc_passes_validator(golden_plan_path, golden_baseline_nc_path):
    job = load_light_job(golden_plan_path)
    report = validate_gcode(
        golden_baseline_nc_path.read_text(encoding="utf-8"), job, geom_tol=0.8
    )
    assert report.ok, report.messages
