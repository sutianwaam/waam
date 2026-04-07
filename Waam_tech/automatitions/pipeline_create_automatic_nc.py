from pathlib import Path
import json
import shutil
import subprocess
import sys
import threading
import traceback

import FreeCAD as App

from Waam_tech.core.config_loader import resolve_standard_config_path
from Waam_tech.core.paths import (
    get_features_path,
    get_nc_output_path,
    get_operations_path,
    get_operations_with_mesh_path,
    get_output_dir,
    get_workspace_root,
)
from Waam_tech.llm_agent.step_to_waam_dsl import convert_step_to_waam, default_dsl_output_path
from Waam_tech.mesh_export import convert_obj_to_llm_text, export_objects_to_obj
from Waam_tech.nc import FusionError, fuse_operations_with_mesh
from Waam_tech.operations import plan_ops
from Waam_tech.part_features import extract_features_wcs
from Waam_tech.waamgen import generate_nc as waamgen_generate


STEP_RELATIVE_PATH = Path("Waam_tech/input/EngineBlock.step")
NC_STALL_TIMEOUT_S = 20.0
NC_SUBPROCESS_TIMEOUT_S = 180.0


def repo_root_candidates():
    candidates = []

    get_user_app_data_dir = getattr(App, "getUserAppDataDir", None)
    if callable(get_user_app_data_dir):
        candidates.append(Path(get_user_app_data_dir()) / "Mod/WAAM")

    candidates.append(Path.cwd())
    return tuple(candidates)


def resolve_step_path(step_path=None):
    requested = Path(step_path).expanduser() if step_path else STEP_RELATIVE_PATH

    if requested.is_absolute():
        if requested.is_file():
            return requested
        raise FileNotFoundError(f"Could not find STEP file: {requested}")

    candidates = []
    for root in repo_root_candidates():
        if step_path:
            candidates.append(root / requested)
            candidates.append(root / "Waam_tech" / "input" / requested)
        else:
            candidates.append(root / STEP_RELATIVE_PATH)

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(
        "Could not find STEP file. Tried: "
        + ", ".join(str(candidate) for candidate in candidates)
    )


def repo_root():
    return Path(__file__).resolve().parents[2]


def ensure_gui_document():
    import FreeCADGui as Gui

    doc = App.ActiveDocument
    if doc is None:
        raise RuntimeError("No active FreeCAD document after STEP import.")

    try:
        gui_doc = Gui.getDocument(doc.Name)
    except Exception as exc:
        raise RuntimeError(f"Could not resolve GUI document for '{doc.Name}': {exc}") from exc

    if gui_doc is None:
        raise RuntimeError(f"GUI document for '{doc.Name}' is not available.")

    try:
        Gui.ActiveDocument = gui_doc
    except Exception:
        pass


def ensure_headless_document(doc_name="WAAMHeadless"):
    doc = App.ActiveDocument
    if doc is not None:
        return doc
    return App.newDocument(doc_name)


def load_step_headless(step_file, doc_name="WAAMHeadless"):
    import Import

    step_file = Path(step_file).expanduser().resolve()
    if not step_file.is_file():
        raise FileNotFoundError(f"Could not find STEP file: {step_file}")

    doc = ensure_headless_document(doc_name=doc_name)
    Import.insert(str(step_file), doc.Name)
    doc.recompute()
    return doc, get_first_solid()


def get_first_solid():
    doc = App.ActiveDocument
    if doc is None:
        return None

    for obj in doc.Objects:
        shape = getattr(obj, "Shape", None)
        if shape is None:
            continue
        try:
            if shape.Solids:
                return obj
        except Exception:
            continue
    return None


def get_visible_parts():
    doc = App.ActiveDocument
    if doc is None:
        return []

    visible_parts = []
    for obj in doc.Objects:
        try:
            view = obj.ViewObject
            if hasattr(view, "Visibility") and not bool(view.Visibility):
                continue
        except Exception:
            pass
        shape = getattr(obj, "Shape", None)
        if shape is None:
            continue
        try:
            if shape.isNull():
                continue
        except Exception:
            pass
        visible_parts.append(obj)
    return visible_parts


def fit_and_set_camera():
    import FreeCADGui as Gui

    active_view = Gui.activeDocument().activeView() if Gui.activeDocument() else None
    if active_view is None:
        raise RuntimeError("No active 3D view is available.")

    Gui.SendMsgToActiveView("ViewFit")
    Gui.SendMsgToActiveView("ViewFit")
    Gui.runCommand("Std_OrthographicCamera", 1)


def activate_waam_workbench():
    import FreeCADGui as Gui

    available = Gui.listWorkbenches()
    workbench_name = "WaamWorkbench" if "WaamWorkbench" in available else "WAAM" if "WAAM" in available else None
    if workbench_name is None:
        raise RuntimeError("WAAM workbench is not registered in this FreeCAD session.")
    Gui.activateWorkbench(workbench_name)


def apply_top_front_left_wcs(label="G54"):
    from Waam_tech.core.path_compat import ensure_setup_sheet_placement
    from Waam_tech.ui.wcs_marker import ensure_tool_wcs_marker

    doc = App.ActiveDocument
    if doc is None:
        raise RuntimeError("No active FreeCAD document is available for WCS placement.")

    obj = get_first_solid()
    if obj is None:
        raise RuntimeError("No solid object was found in the active document.")

    bb = obj.Shape.BoundBox
    origin = App.Vector(bb.XMin, bb.YMin, bb.ZMin)
    placement = App.Placement(origin, App.Rotation(App.Vector(0, 0, 1), 0))

    ensure_tool_wcs_marker(doc, placement, label=label, clear_start=True)

    for candidate in doc.Objects:
        type_id = str(getattr(candidate, "TypeId", ""))
        if not type_id.startswith("Path::Feature"):
            continue
        try:
            ensure_setup_sheet_placement(candidate)
            candidate.SetupSheet.Placement = placement
        except Exception as exc:
            App.Console.PrintWarning(f"WAAM: Could not apply WCS to Path job: {exc}\n")
        break

    doc.recompute()
    App.Console.PrintMessage(f'WAAM: Applied Top-Front-Left preset as WCS "{label}".\n')


def get_dialog_parent():
    try:
        import FreeCADGui as Gui
        return Gui.getMainWindow() if hasattr(Gui, "getMainWindow") else None
    except Exception:
        return None


def set_status(message):
    App.Console.PrintMessage(f"WAAM Auto NC: {message}\n")


def _artifact_mtime(path):
    candidate = Path(path)
    if not candidate.is_file():
        return None
    try:
        return candidate.stat().st_mtime
    except OSError:
        return None


def _is_artifact_current(target_path, *, reference_path):
    target_mtime = _artifact_mtime(target_path)
    reference_mtime = _artifact_mtime(reference_path)
    if target_mtime is None:
        return False
    if reference_mtime is None:
        return True
    return target_mtime >= reference_mtime


def _python_fallback_executable():
    for candidate in (shutil.which("python3"), shutil.which("python"), sys.executable):
        if candidate:
            return candidate
    raise RuntimeError("No Python interpreter found for NC fallback.")


def _run_nc_subprocess_fallback(fused_path, nc_out):
    fused_path = Path(fused_path).expanduser().resolve()
    nc_out = Path(nc_out).expanduser().resolve()
    python_exe = _python_fallback_executable()
    cmd = [
        python_exe,
        "-m",
        "Waam_tech.waamgen.pipeline",
        "generate",
        "--input",
        str(fused_path),
        "--out",
        str(nc_out),
        "--max-iters",
        "1",
        "--geom-tol",
        "0.8",
    ]
    set_status("NC fallback: running isolated generator...")
    completed = subprocess.run(
        cmd,
        cwd=str(repo_root()),
        capture_output=True,
        text=True,
        timeout=NC_SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    if completed.stdout:
        App.Console.PrintMessage(completed.stdout if completed.stdout.endswith("\n") else completed.stdout + "\n")
    if completed.stderr:
        App.Console.PrintWarning(completed.stderr if completed.stderr.endswith("\n") else completed.stderr + "\n")
    if completed.returncode != 0:
        raise RuntimeError(f"NC fallback failed with exit code {completed.returncode}.")
    if not _is_artifact_current(nc_out, reference_path=fused_path):
        raise RuntimeError("NC fallback finished, but waam_baseline.nc was not refreshed.")
    return nc_out


def _refresh_ops_with_llm_paths(ops_path):
    ops_path = Path(ops_path).expanduser().resolve()
    try:
        data = json.loads(ops_path.read_text(encoding="utf-8"))
    except Exception:
        return ops_path

    sources = data.get("sources") or (data.get("llm_context") or {}).get("sources") or {}
    ops_src = sources.get("operations_path")
    mesh_src = sources.get("mesh_context_path")
    if not ops_src or not mesh_src:
        return ops_path

    return fuse_operations_with_mesh(
        ops_path=Path(ops_src),
        mesh_path=Path(mesh_src),
        output_path=ops_path,
        config_path=sources.get("config_path"),
        features_path=sources.get("features_path"),
        llm_waam_path=sources.get("llm_waam_path"),
    )


def export_visible_step():
    import ImportGui

    visible_parts = get_visible_parts()
    if not visible_parts:
        raise RuntimeError("No visible solids to export.")

    set_status("Exporting visible STEP (Alt+2 equivalent)...")
    step_dir = get_workspace_root() / "input_step"
    step_dir.mkdir(parents=True, exist_ok=True)
    step_path = step_dir / "part.step"
    ImportGui.export(visible_parts, str(step_path))
    set_status(f"Exported STEP to {step_path}")
    return step_path, visible_parts


def extract_features():
    doc = App.ActiveDocument
    if doc is None:
        raise RuntimeError("No active document for feature extraction.")

    from Waam_tech.ui.wcs_marker import get_wcs_label

    wcs_label = get_wcs_label(doc) or "G54"
    features_path = get_features_path(create=True, prefer_existing=False)
    extract_features_wcs(
        doc,
        wcs_name=wcs_label,
        out_path=str(features_path),
        force_start_to_wcs_origin=True,
    )
    set_status(f"Feature extraction done (WCS: {wcs_label})")
    return features_path


def plan_operations(features_path):
    config_path = resolve_standard_config_path()
    ops_path = get_operations_path(create=True, prefer_existing=False)

    with features_path.open("r", encoding="utf-8") as handle:
        features_json = json.load(handle)
    with config_path.open("r", encoding="utf-8") as cfg_handle:
        cfg = json.load(cfg_handle)

    operations = plan_ops(features_json, config=cfg)
    with ops_path.open("w", encoding="utf-8") as handle:
        json.dump(operations, handle, indent=2)

    set_status(f"Operations planned to {ops_path.name}")
    return ops_path, config_path


def generate_mesh_and_llm(step_path, visible_parts):
    set_status("Generating mesh context and LLM WAAM DSL...")
    output_dir = get_output_dir()
    mesh_obj_path = export_objects_to_obj(
        visible_parts,
        mesh_linear_deflection=0.25,
        mesh_angular_deflection=0.349,
        filename="part_visible.obj",
    )
    mesh_text_path = convert_obj_to_llm_text(
        mesh_obj_path,
        out_path=output_dir / "mesh_exports" / "part.mesh_for_llm",
        decimals=2,
    )

    waam_out = default_dsl_output_path(str(step_path))
    llm_waam_out = waam_out.with_suffix(".llm_waam")
    convert_step_to_waam(
        step_path=str(step_path),
        out_path=waam_out,
        layer_height=1.2,
        bead_width=6.0,
        samples_per_edge=24,
        z_offset=-1.2,
        min_contour_area=1.0,
        max_layers=None,
        stitch_close_tol=0.5,
        emit_llm=True,
        llm_output_path=llm_waam_out,
        units="mm",
    )
    set_status(f"Mesh context saved to {mesh_text_path}")
    set_status(f"LLM WAAM saved to {llm_waam_out}")
    return Path(mesh_text_path), llm_waam_out


def fuse_operations(ops_path, config_path, features_path, mesh_path, llm_waam_path):
    set_status("Fusing operations with mesh and WAAM DSL...")
    fused_path = get_operations_with_mesh_path(create=True, prefer_existing=False)
    fuse_operations_with_mesh(
        ops_path=ops_path,
        mesh_path=Path(mesh_path),
        output_path=fused_path,
        config_path=config_path,
        config_override=None,
        features_path=features_path,
        llm_waam_path=Path(llm_waam_path),
    )
    set_status(f"Fused operations saved to {fused_path}")
    return fused_path


def generate_baseline_nc(fused_path):
    set_status("Generating baseline NC (Alt+7 equivalent)...")
    fused_path = Path(fused_path).expanduser().resolve()
    nc_out = get_nc_output_path(create=True, prefer_existing=False).resolve()
    chunk_dir = (nc_out.parent / "chunks").resolve()
    chunk_dir.mkdir(parents=True, exist_ok=True)

    try:
        fused_path = Path(_refresh_ops_with_llm_paths(fused_path)).expanduser().resolve()
        set_status(f"Refreshed fused operations for NC: {fused_path}")
    except FusionError as exc:
        App.Console.PrintWarning(f"WAAM Auto NC: Could not refresh fused ops before NC generation: {exc}\n")
    except Exception as exc:
        App.Console.PrintWarning(f"WAAM Auto NC: Unexpected fused-ops refresh error: {exc}\n")

    result = {}

    def _worker():
        try:
            result["path"] = waamgen_generate(
                fused_path,
                output_path=nc_out,
                max_iters=1,
                geom_tol=0.8,
                attempt_dir=chunk_dir,
                plot=False,
            )
        except Exception as exc:
            result["error"] = exc
            result["traceback"] = traceback.format_exc()

    worker = threading.Thread(target=_worker, name="waam-auto-nc", daemon=True)
    worker.start()
    worker.join(NC_STALL_TIMEOUT_S)

    if worker.is_alive():
        App.Console.PrintWarning(
            f"WAAM Auto NC: NC generation appears stalled after {NC_STALL_TIMEOUT_S:.0f}s. "
            "Trying isolated fallback.\n"
        )
        nc_out = _run_nc_subprocess_fallback(fused_path, nc_out)
        set_status(f"Baseline NC saved to {nc_out} (fallback)")
        return nc_out

    if "error" in result:
        App.Console.PrintWarning("WAAM Auto NC: In-process NC generation failed. Trying isolated fallback.\n")
        tb = result.get("traceback")
        if tb:
            App.Console.PrintWarning(tb if tb.endswith("\n") else tb + "\n")
        nc_out = _run_nc_subprocess_fallback(fused_path, nc_out)
        set_status(f"Baseline NC saved to {nc_out} (fallback)")
        return nc_out

    resolved_out = Path(result.get("path") or nc_out).expanduser().resolve()
    if not _is_artifact_current(resolved_out, reference_path=fused_path):
        App.Console.PrintWarning(
            "WAAM Auto NC: NC generation returned without refreshing waam_baseline.nc. "
            "Trying isolated fallback.\n"
        )
        nc_out = _run_nc_subprocess_fallback(fused_path, resolved_out)
        set_status(f"Baseline NC saved to {nc_out} (fallback)")
        return nc_out

    set_status(f"Baseline NC saved to {resolved_out}")
    return resolved_out


def run_full_pipeline_automatically(step_path, visible_parts):
    App.Console.PrintMessage("WAAM: Running full pipeline automatically.\n")
    features_path = extract_features()
    ops_path, config_path = plan_operations(features_path)
    mesh_path, llm_waam_path = generate_mesh_and_llm(step_path, visible_parts)
    fused_path = fuse_operations(ops_path, config_path, features_path, mesh_path, llm_waam_path)
    generate_baseline_nc(fused_path)
    App.Console.PrintMessage("WAAM: Automatic NC automation finished.\n")


def main_headless(step_path=None, label="G54"):
    try:
        step_file = resolve_step_path(step_path)
        load_step_headless(step_file)
        apply_top_front_left_wcs(label=label)
        visible_parts = get_visible_parts()
        if not visible_parts:
            raise RuntimeError("No visible parts found after headless STEP import.")
        run_full_pipeline_automatically(step_file, visible_parts)
    except Exception:
        App.Console.PrintError("WAAM Auto NC headless: Automation failed.\n")
        App.Console.PrintError(traceback.format_exc() + "\n")
        raise


def print_manual_followup():
    message = """
Manual follow-up inside FreeCAD:

1) Check that the WCS marker is in the expected Top-Front-Left corner of the part

2) If you want the built-in pipeline UI, open WAAM:Set WCS
   Alt+1

3) Go to the Run Pipeline tab and click Run Full Pipeline
"""
    App.Console.PrintMessage(message + "\n")
    print(message)


def main(auto_run_pipeline=True, step_path=None):
    try:
        import ImportGui

        step_file = resolve_step_path(step_path)
        ImportGui.open(str(step_file))
        ensure_gui_document()
        fit_and_set_camera()
        activate_waam_workbench()
        apply_top_front_left_wcs()
        if auto_run_pipeline:
            exported_step_path, visible_parts = export_visible_step()
            run_full_pipeline_automatically(exported_step_path, visible_parts)
        else:
            print_manual_followup()
    except Exception:
        App.Console.PrintError("WAAM Auto NC: Automation failed.\n")
        App.Console.PrintError(traceback.format_exc() + "\n")
        raise


if __name__ == "__main__":
    main()
