"""Headless entry points for running WAAM pipelines from the FreeCAD console."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import FreeCAD as App  # type: ignore

from .core.config_loader import load_validated_config
from .core.paths import (
    get_features_path,
    get_operations_path,
    get_output_dir,
    _output_root_for,
)
from .operations import plan_ops
from .part_features import extract_features_wcs
from .ui.wcs_marker import get_wcs_label


def _load_document(path: Optional[str]) -> Optional["App.Document"]:
    if not path:
        return App.ActiveDocument
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"FreeCAD document not found: {resolved}")
    return App.openDocument(str(resolved))


def run_pipeline(
    *,
    document: Optional["App.Document"],
    config_path: Optional[str] = None,
    output_dir: Optional[str] = None,
    wcs_name: Optional[str] = None,
    features_only: bool = False,
    plan_only: bool = False,
) -> Path:
    if document is None:
        raise RuntimeError("No active FreeCAD document available.")

    validated = load_validated_config(config_path)
    cfg = validated.raw
    stored_label = get_wcs_label(document) if document else None
    wcs_label = wcs_name or stored_label or validated.wcs

    out_dir = Path(output_dir).expanduser() if output_dir else get_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    features_path = get_features_path(out_dir, prefer_existing=False, create=True)
    ops_path = get_operations_path(out_dir, prefer_existing=False, create=True)

    if not plan_only:
        extract_features_wcs(
            document,
            wcs_name=wcs_label,
            out_path=str(features_path),
            force_start_to_wcs_origin=True,
        )
        document.recompute()
    elif not features_path.is_file():
        raise FileNotFoundError(
            f"features.json not found at {features_path}; run without --plan-only first."
        )

    if not features_only:
        with features_path.open("r", encoding="utf-8") as handle:
            features_json = json.load(handle)
        operations = plan_ops(features_json, config=cfg)
        with ops_path.open("w", encoding="utf-8") as handle:
            json.dump(operations, handle, indent=2)
        document.recompute()

    return _output_root_for(features_path)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run WAAM feature extraction and planning headlessly.")
    parser.add_argument("document", nargs="?", help="Path to a FreeCAD document (.FCStd). Uses the active document when omitted.")
    parser.add_argument("--config", help="Override path to WAAM configuration JSON.")
    parser.add_argument("--output-dir", help="Base directory where artefacts (features/, operations/, operations_mesh/, nc_files/) will be written.")
    parser.add_argument("--wcs-name", help="Override the WCS label stored in the artefacts.")
    parser.add_argument("--features-only", action="store_true", help="Stop after extracting features.json.")
    parser.add_argument("--plan-only", action="store_true", help="Skip extraction and only regenerate waam_operations.json.")
    parser.add_argument("--keep-open", action="store_true", help="Leave newly opened documents loaded in FreeCAD after completion.")

    args = parser.parse_args(argv)

    if args.features_only and args.plan_only:
        raise SystemExit("--features-only and --plan-only are mutually exclusive.")

    original_doc = App.ActiveDocument
    doc_was_loaded = False
    document = _load_document(args.document)
    if document is None:
        raise SystemExit("No FreeCAD document open and none supplied.")
    if document is not original_doc:
        doc_was_loaded = True
        App.setActiveDocument(document.Name)

    try:
        out_dir = run_pipeline(
            document=document,
            config_path=args.config,
            output_dir=args.output_dir,
            wcs_name=args.wcs_name,
            features_only=args.features_only,
            plan_only=args.plan_only,
        )
        try:
            App.Console.PrintMessage(f"[WAAM] Artefacts written to {out_dir}\n")
        except Exception:
            pass
    finally:
        if doc_was_loaded and not args.keep_open:
            App.closeDocument(document.Name)
            if original_doc is not None:
                App.setActiveDocument(original_doc.Name)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
