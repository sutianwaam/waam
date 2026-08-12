from __future__ import annotations

from types import SimpleNamespace

from _fc_import_shims import load_step_to_waam_dsl


dsl = load_step_to_waam_dsl()


def test_slice_shape_at_z_records_section_and_slice_exceptions(monkeypatch):
    class FakeShape:
        def section(self, _face):
            raise RuntimeError("section failed")

        def slice(self, _axis, _z):
            raise RuntimeError("slice failed")

    fake_part = SimpleNamespace(
        Face=lambda _wire: object(),
        makePolygon=lambda _points: object(),
        sortEdges=lambda _edges: [],
    )
    fake_app = SimpleNamespace(Vector=lambda *args: args)

    monkeypatch.setattr(dsl, "_require_fc", lambda: None)
    monkeypatch.setattr(dsl, "compute_bounding_box", lambda _shape: (0.0, 0.0, 0.0, 10.0, 10.0, 5.0))
    monkeypatch.setattr(dsl, "Part", fake_part)
    monkeypatch.setattr(dsl, "App", fake_app)

    diagnostics = []
    assert dsl.slice_shape_at_z(FakeShape(), 1.5, diagnostics=diagnostics, layer_index=3) == []

    assert diagnostics == [
        {
            "z": 1.5,
            "reason": "slice_exception",
            "layer_index": 3,
            "errors": [
                {"stage": "section", "error_type": "RuntimeError", "message": "section failed"},
                {"stage": "slice", "error_type": "RuntimeError", "message": "slice failed"},
            ],
        }
    ]


def test_generate_layer_slices_records_empty_candidate_z_planes(monkeypatch):
    monkeypatch.setattr(dsl, "_require_fc", lambda: None)
    monkeypatch.setattr(dsl, "compute_bounding_box", lambda _shape: (0.0, 0.0, 0.0, 10.0, 10.0, 3.0))

    def fake_slice_shape_at_z(_shape, z, *, diagnostics=None, layer_index=None):
        if layer_index == 1:
            return []
        return [object()]

    def fake_classify_outer_inner(wires, *, area_threshold, samples_per_edge):
        if len(wires) == 1:
            return [dsl.Contour(points=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)], is_outer=True)]
        return []

    monkeypatch.setattr(dsl, "slice_shape_at_z", fake_slice_shape_at_z)
    monkeypatch.setattr(dsl, "classify_outer_inner", fake_classify_outer_inner)

    diagnostics = []
    layers = dsl.generate_layer_slices(
        object(),
        layer_height=1.0,
        z_offset=0.5,
        max_layers=3,
        diagnostics=diagnostics,
    )

    assert [layer.index for layer in layers] == [0, 2]
    assert diagnostics == [
        {
            "z": 1.5,
            "reason": "no_wires",
            "layer_index": 1,
            "wire_count": 0,
        }
    ]


def test_generate_layer_slices_records_classification_skips(monkeypatch):
    monkeypatch.setattr(dsl, "_require_fc", lambda: None)
    monkeypatch.setattr(dsl, "compute_bounding_box", lambda _shape: (0.0, 0.0, 0.0, 10.0, 10.0, 1.0))
    monkeypatch.setattr(dsl, "slice_shape_at_z", lambda _shape, z, **_kwargs: [object()])
    monkeypatch.setattr(dsl, "classify_outer_inner", lambda *_args, **_kwargs: [])

    diagnostics = []
    layers = dsl.generate_layer_slices(
        object(),
        layer_height=1.0,
        z_offset=0.5,
        max_layers=1,
        diagnostics=diagnostics,
    )

    assert layers == []
    assert diagnostics == [
        {
            "z": 0.5,
            "reason": "no_classified_contours",
            "layer_index": 0,
            "wire_count": 1,
            "contour_count": 0,
        }
    ]
