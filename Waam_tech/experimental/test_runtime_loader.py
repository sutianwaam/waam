from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from Waam_tech.experimental.base import ExperimentalPlannerContext
from Waam_tech.experimental.registry import select_experimental_planner
from Waam_tech.experimental.runtime.loader import (
    RuntimeActiveExperimentalPlanner,
    ensure_active_runtime,
    infer_part_family_from_features_doc,
    load_active_manifest,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _make_context(root: Path, part_name: str, *, features_payload: dict | None = None) -> ExperimentalPlannerContext:
    output_dir = root / "output"
    features_path = output_dir / "features" / "features.json"
    fused_path = output_dir / "operations_mesh" / "waam_operations_with_mesh.json"
    baseline_nc = output_dir / "nc_files" / "waam_baseline.nc"
    step_path = root / f"{part_name}.step"
    _write_json(
        features_path,
        features_payload
        or {
            "metadata": {"work_obj": part_name},
            "features": [],
        },
    )
    _write_json(fused_path, {"layers": []})
    baseline_nc.parent.mkdir(parents=True, exist_ok=True)
    baseline_nc.write_text("(baseline)\n", encoding="utf-8")
    step_path.write_text("STEP", encoding="utf-8")
    return ExperimentalPlannerContext(
        output_dir=output_dir,
        fused_operations_path=fused_path,
        features_path=features_path,
        part_key="",
        part_label="",
        step_path=step_path,
        baseline_nc_path=baseline_nc,
        run_id="test-run",
    )


def test_ensure_active_runtime_initializes_sandbox() -> None:
    with TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        experimental_root = root / "experimental"
        experimental_root.mkdir(parents=True, exist_ok=True)
        (experimental_root / "CONTRACT.md").write_text("contract", encoding="utf-8")
        ctx = _make_context(root, "StudyCadCam 78")

        manifest = ensure_active_runtime(ctx, experimental_root=experimental_root)

        assert manifest["part_key"] == "studycadcam-78"
        assert manifest["part_family"] == "bulk_fill"
        assert manifest["runtime_planner_path"] == "runtime/active/planner.py"
        assert (experimental_root / "runtime" / "active" / "planner.py").is_file()
        active_part = json.loads((experimental_root / "ACTIVE_PART.json").read_text(encoding="utf-8"))
        assert active_part["runtime_planner_path"] == "runtime/active/planner.py"
        assert active_part["active_part_key"] == "studycadcam-78"


def test_ensure_active_runtime_archives_previous_part() -> None:
    with TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        experimental_root = root / "experimental"
        experimental_root.mkdir(parents=True, exist_ok=True)
        (experimental_root / "CONTRACT.md").write_text("contract", encoding="utf-8")

        first = _make_context(root / "one", "EngineBlock")
        second = _make_context(root / "two", "Pieza Upna")

        ensure_active_runtime(first, experimental_root=experimental_root)
        planner_path = experimental_root / "runtime" / "active" / "planner.py"
        planner_path.write_text("# archived marker\n", encoding="utf-8")

        ensure_active_runtime(second, experimental_root=experimental_root)

        archive_root = experimental_root / "runtime" / "archive" / "engineblock"
        archived_planners = list(archive_root.glob("*/planner.py"))
        assert archived_planners
        assert archived_planners[0].read_text(encoding="utf-8") == "# archived marker\n"
        manifest = load_active_manifest(experimental_root=experimental_root)
        assert manifest["part_key"] == "pieza-upna"


def test_runtime_active_planner_loads_active_sandbox() -> None:
    with TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        experimental_root = root / "experimental"
        experimental_root.mkdir(parents=True, exist_ok=True)
        (experimental_root / "CONTRACT.md").write_text("contract", encoding="utf-8")
        ctx = _make_context(root, "StudyCadCam 78")
        ensure_active_runtime(ctx, experimental_root=experimental_root)

        planner_path = experimental_root / "runtime" / "active" / "planner.py"
        helpers_path = experimental_root / "runtime" / "active" / "helpers.py"
        helpers_path.write_text(
            "from pathlib import Path\n"
            "def load_manifest():\n"
            "    return {'goal': 'test'}\n"
            "def append_iteration_note(context, *, heading, lines):\n"
            "    return None\n"
            "def copy_baseline_to_experimental(context):\n"
            "    target = context.output_dir / 'nc_files' / 'waam_baseline_experimental.nc'\n"
            "    target.parent.mkdir(parents=True, exist_ok=True)\n"
            "    target.write_text('experimental\\n', encoding='utf-8')\n"
            "    return target\n",
            encoding="utf-8",
        )
        planner_path.write_text(
            "from .helpers import append_iteration_note, copy_baseline_to_experimental, load_manifest\n"
            "class RuntimeActivePlanner:\n"
            "    key = 'runtime_active'\n"
            "    label = 'Runtime test planner'\n"
            "    output_filename = 'waam_baseline_experimental.nc'\n"
            "    def export_nc(self, context):\n"
            "        append_iteration_note(context, heading='test', lines=['- ok'])\n"
            "        return copy_baseline_to_experimental(context)\n",
            encoding="utf-8",
        )

        planner = RuntimeActiveExperimentalPlanner(experimental_root=experimental_root)
        target = planner.export_nc(ctx)

        assert target.is_file()
        assert target.read_text(encoding="utf-8") == "experimental\n"


def test_part_family_inference_prefers_topology_transition_void_protection() -> None:
    family, signals = infer_part_family_from_features_doc(
        {
            "metadata": {"work_obj": "StudyCadCam 78"},
            "features": [
                {"type": "through_hole"},
                {"type": "cylindrical_hole"},
                {"type": "planar_pocket_floor"},
            ],
        }
    )

    assert family == "topology_transition_void_protection"
    assert "hole_features" in signals
    assert "pocket_features" in signals


def test_auto_selection_uses_runtime_active_planner() -> None:
    planner = select_experimental_planner()
    assert planner is not None
    assert planner.key == "runtime_active"
