"""Utility script to render OBJ meshes (WAAM exports) into PNG snapshots."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import trimesh

import imageio.v2 as imageio

try:  # pragma: no cover - optional dependency in FreeCAD env
    import pyrender
except Exception as exc:  # pragma: no cover
    pyrender = None  # type: ignore
    _PYRENDER_IMPORT_ERROR = exc
else:
    _PYRENDER_IMPORT_ERROR = None


def _to_rgba(values: Iterable[int]) -> list[int]:
    rgba = [int(v) for v in values]
    if len(rgba) == 3:
        rgba.append(255)
    if len(rgba) != 4:
        raise ValueError("Background color must have 3 or 4 integer components.")
    return [max(0, min(255, v)) for v in rgba]


def _look_at_matrix(eye: np.ndarray, target: np.ndarray, up: np.ndarray) -> np.ndarray:
    forward = target - eye
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)  
    matrix = np.eye(4)
    matrix[0:3, 0] = right
    matrix[0:3, 1] = up
    matrix[0:3, 2] = -forward  # pyrender cameras look down -Z
    matrix[0:3, 3] = eye
    return matrix


def render_obj_to_png(
    obj_path: Path,
    *,
    out_path: Path,
    img_size: Sequence[int] = (800, 600),
    background: Sequence[int] = (255, 255, 255, 255),
) -> Path:
    """
    Render an OBJ mesh to a PNG using trimesh + pyrender.
    
    Args:
        obj_path: Path to the OBJ file to load.
        out_path: Destination PNG path.
        img_size: (width, height) tuple.
        background: Sequence of 3/4 ints describing RGB(A).
    """
    if pyrender is None:
        raise RuntimeError(
            "pyrender is not available. Install pyrender (and its dependencies) to enable rendering."
        ) from _PYRENDER_IMPORT_ERROR

    # 1. Load Mesh - with robust handling for Scenes/Lists
    mesh = trimesh.load(obj_path, force="mesh")
    
    # Ensure we have a single Trimesh object
    if isinstance(mesh, list):
        # Filter only Trimesh objects
        meshes = [m for m in mesh if isinstance(m, trimesh.Trimesh)]
        if not meshes:
             # Fallback if no Trimesh found (e.g. only paths), though pyrender might fail
             if len(mesh) > 0:
                 # Try to force concatenation anyway if strict filtering fails? 
                 # Or just take the first one? Let's try concatenation.
                 mesh = trimesh.util.concatenate(mesh)
             else:
                 raise ValueError("Loaded OBJ list is empty.")
        else:
            mesh = trimesh.util.concatenate(meshes)
            
    elif isinstance(mesh, trimesh.Scene):
        # Extract geometry from scene
        geoms = list(mesh.geometry.values())
        meshes = [g for g in geoms if isinstance(g, trimesh.Trimesh)]
        if not meshes:
             # Try fallback
             if geoms:
                 mesh = trimesh.util.concatenate(geoms)
             else:
                 raise ValueError("Loaded OBJ scene contains no geometry.")
        else:
            mesh = trimesh.util.concatenate(meshes)

    # Final check
    if not isinstance(mesh, trimesh.Trimesh):
        # If still not a trimesh (e.g. Path3D), this will likely fail in pyrender
        # But let's try to proceed or error out clearly.
        # However, the user wants "what worked before". 
        # Before, we just did force='mesh' and handled Scene. 
        # The 'list' return is the edge case.
        pass

    render_mesh = pyrender.Mesh.from_trimesh(mesh, smooth=False)
    scene = pyrender.Scene(bg_color=_to_rgba(background), ambient_light=[0.2, 0.2, 0.2])
    scene.add(render_mesh)

    bbox = mesh.bounds
    center = (bbox[0] + bbox[1]) / 2.0
    size = bbox[1] - bbox[0]
    radius = float(np.linalg.norm(size) / 2.0 or 1.0)

    # Single Isometric View
    camera = pyrender.PerspectiveCamera(yfov=np.pi / 4.0)
    eye = center + np.array([0.0, -2.5 * radius, 1.5 * radius])
    cam_pose = _look_at_matrix(eye, center, np.array([0.0, 0.0, 1.0]))
    scene.add(camera, pose=cam_pose)

    # Lighting
    light = pyrender.DirectionalLight(color=np.ones(3), intensity=2.0)
    scene.add(light, pose=cam_pose)

    light2_pose = np.eye(4)
    light2_pose[0:3, 3] = center + np.array([2.0 * radius, 2.0 * radius, 2.0 * radius])
    light2 = pyrender.DirectionalLight(color=np.ones(3), intensity=1.0)
    scene.add(light2, pose=light2_pose)

    renderer = pyrender.OffscreenRenderer(viewport_width=int(img_size[0]), viewport_height=int(img_size[1]))
    color, _ = renderer.render(scene)
    renderer.delete()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    imageio.imwrite(out_path, color)
    print(f"WAAM: Rendered single view to {out_path}")
    return out_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render WAAM OBJ mesh exports to PNG snapshots.")
    parser.add_argument(
        "obj",
        type=Path,
        help="Path to the OBJ file (e.g. output/mesh_exports/foo.obj).",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=Path("render.png"),
        help="Destination PNG path (default: render.png).",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=800,
        help="Rendered image width in pixels (default: 800).",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=600,
        help="Rendered image height in pixels (default: 600).",
    )
    parser.add_argument(
        "--background",
        type=int,
        nargs="+",
        default=(255, 255, 255, 255),
        help="Background RGB or RGBA values (default: 255 255 255 255).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if pyrender is None:
        raise SystemExit(
            "pyrender is not available in this environment. Install it (pip install pyrender) to render meshes."
        )
    png_path = render_obj_to_png(
        args.obj,
        out_path=args.out,
        img_size=(args.width, args.height),
        background=args.background,
    )
    print(f"Saved render to: {png_path}")


if __name__ == "__main__":
    main()


PYRENDER_AVAILABLE = pyrender is not None

__all__ = ["render_obj_to_png", "PYRENDER_AVAILABLE"]
