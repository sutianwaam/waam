from __future__ import annotations

from Waam_tech.nc import parser as nc_parser


def test_clean_nc_line_strips_comments_and_uppercases():
    assert nc_parser.clean_nc_line("g1 x1.0 y2.0 (a comment)") == "G1 X1.0 Y2.0"
    assert nc_parser.clean_nc_line("g0 x0 ; trailing semicolon comment") == "G0 X0"
    assert nc_parser.clean_nc_line("   ") == ""


def test_resolve_torch_codes_defaults_when_none_given():
    on_codes, off_codes = nc_parser.resolve_torch_codes(None, None, allow_config=False)
    assert "M62" in on_codes
    assert "M63" in off_codes


def test_resolve_torch_codes_uses_explicit_overrides():
    on_codes, off_codes = nc_parser.resolve_torch_codes(["m100"], ["m101"], allow_config=False)
    assert on_codes == frozenset({"M100"})
    assert off_codes == frozenset({"M101"})


def test_resolve_torch_codes_keeps_parameterized_and_base_m_code():
    on_codes, off_codes = nc_parser.resolve_torch_codes(["M62 P0"], ["M63 P0"], allow_config=False)
    assert {"M62 P0", "M62"} <= on_codes
    assert {"M63 P0", "M63"} <= off_codes


def test_parse_nc_text_marks_deposition_segments_between_torch_on_off():
    nc_text = "\n".join(
        [
            "G0 X0 Y0 Z10",
            "M62 P0",
            "G1 X10 Y0 F300",
            "G1 X10 Y10",
            "M63 P0",
            "G0 X0 Y0 Z10",
        ]
    )
    points, deposition = nc_parser.parse_nc_text(nc_text, allow_config=False)
    assert len(points) == len(deposition) + 1
    travel, depo = nc_parser.split_moves(points, deposition)
    assert len(depo) == 2
    # Initial G0 to Z10 and final G0 back to origin are both travel moves.
    assert len(travel) == 2


def test_parse_nc_text_tracks_parameterized_explicit_torch_codes():
    nc_text = "\n".join(
        [
            "G0 X0 Y0 Z5",
            "M62 P0",
            "G1 X10 Y0 Z0 F300",
            "M63 P0",
            "G1 X10 Y10 Z0 F300",
        ]
    )
    points, deposition = nc_parser.parse_nc_text(
        nc_text,
        torch_on_codes=["M62 P0"],
        torch_off_codes=["M63 P0"],
        allow_config=False,
    )
    travel, depo = nc_parser.split_moves(points, deposition)

    assert len(depo) == 1
    assert len(travel) == 2
    assert depo[0][1] == (10.0, 0.0, 0.0)


def test_parse_nc_text_raises_on_no_moves():
    import pytest

    with pytest.raises(ValueError):
        nc_parser.parse_nc_text("(only a comment)\n", allow_config=False)
