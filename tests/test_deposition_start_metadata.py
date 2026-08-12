"""Unit tests for the deposition-start extraction logic in waamgen/pipeline.py.

This is the pure-Python half of the wcs_marker -> slice_plan -> NC
propagation chain (AGENTS.md "Conventions"). The FreeCAD half
(core/wcs_resolver.py) can't be unit tested without a FreeCAD environment;
this covers what can run headlessly.
"""

from __future__ import annotations

from Waam_tech.waamgen.pipeline import _start_point_from_metadata


def test_extracts_point_wcs_from_deposition_start_mapping():
    metadata = {"deposition_start": {"point_wcs": [1.0, 2.0, 3.0], "source": "wcs_marker"}}
    assert _start_point_from_metadata(metadata) == (1.0, 2.0, 3.0)


def test_defaults_missing_z_to_zero():
    metadata = {"deposition_start": {"point_wcs": [1.0, 2.0]}}
    assert _start_point_from_metadata(metadata) == (1.0, 2.0, 0.0)


def test_falls_back_to_point_key():
    metadata = {"deposition_start": {"point": [4.0, 5.0]}}
    assert _start_point_from_metadata(metadata) == (4.0, 5.0, 0.0)


def test_accepts_bare_sequence_without_mapping_wrapper():
    metadata = {"deposition_start": [1.0, 2.0, 3.0]}
    assert _start_point_from_metadata(metadata) == (1.0, 2.0, 3.0)


def test_returns_none_when_absent():
    assert _start_point_from_metadata({}) is None


def test_returns_none_on_malformed_point():
    metadata = {"deposition_start": {"point_wcs": ["not", "numeric"]}}
    assert _start_point_from_metadata(metadata) is None
