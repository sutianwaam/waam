"""Utilities to bundle WAAM geometry/context for LLM consumption."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

try:  # Optional dependency: trimesh helps compute mesh stats.
    import trimesh  # type: ignore
except Exception:  # pragma: no cover
    trimesh = None  # type: ignore


def _load_json_if_exists(path: Optional[Path]) -> Optional[Any]:
    if not path:
        return None
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _mesh_stats(path: Path) -> Optional[Dict[str, Any]]:
    if trimesh is None:  # pragma: no cover - optional dependency
        return None
    mesh = trimesh.load(path, force="mesh")
    if isinstance(mesh, trimesh.Scene):
        mesh = trimesh.util.concatenate(tuple(mesh.dump().values()))
    bounds = mesh.bounds
    bbox_min = bounds[0].tolist()
    bbox_max = bounds[1].tolist()
    size = (bounds[1] - bounds[0]).tolist()
    return {
        "bbox_min": bbox_min,
        "bbox_max": bbox_max,
        "bbox_size": size,
        "surface_area": float(mesh.area),
        "volume": float(mesh.volume) if getattr(mesh, "volume", None) is not None else None,
        "triangles": int(len(mesh.faces)),
        "vertices": int(len(mesh.vertices)),
    }


def build_llm_manifest(
    mesh_path: Path,
    *,
    output_path: Optional[Path] = None,
    render_path: Optional[Path] = None,
    features_path: Optional[Path] = None,
    operations_path: Optional[Path] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    notes: Optional[str] = None,
) -> Path:
    """
    Build a JSON manifest that groups geometry + WAAM context for LLM consumption.
    """
    mesh_path = Path(mesh_path)
    if not mesh_path.is_file():
        raise FileNotFoundError(f"Mesh OBJ not found: {mesh_path}")

    render_path = Path(render_path) if render_path and Path(render_path).is_file() else None
    features_path = Path(features_path) if features_path and Path(features_path).is_file() else None
    operations_path = Path(operations_path) if operations_path and Path(operations_path).is_file() else None

    manifest: Dict[str, Any] = {
        "schema": "waam.llm.bundle.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "units": "mm",
        "files": {
            "mesh_obj": str(mesh_path),
        },
        "metadata": dict(metadata or {}),
    }
    if render_path:
        manifest["files"]["render_png"] = str(render_path)
    if features_path:
        manifest["files"]["features_json"] = str(features_path)
    if operations_path:
        manifest["files"]["operations_json"] = str(operations_path)
    if notes:
        manifest["notes"] = notes

    mesh_info = _mesh_stats(mesh_path)
    if mesh_info:
        manifest["mesh_stats"] = mesh_info

    features_content = _load_json_if_exists(features_path)
    if features_content is not None:
        manifest["features"] = features_content

    operations_content = _load_json_if_exists(operations_path)
    if operations_content is not None:
        manifest["operations"] = operations_content

    out_path = Path(output_path) if output_path else mesh_path.with_suffix(".llm.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    return out_path


__all__ = ["build_llm_manifest"]
