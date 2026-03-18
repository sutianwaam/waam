from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..nc import parser as nc_parser
_DEFAULT_MAX_SEGMENTS = 12_000
_DEPOSITION_CMAP = "plasma"


def _same_point(pt_a: Sequence[float], pt_b: Sequence[float], tol: float = 1e-6) -> bool:
    return all(abs(float(a) - float(b)) <= tol for a, b in zip(pt_a, pt_b))


def _ensure_closed_loop(polygon: Sequence[Sequence[float]]) -> List[Tuple[float, float]]:
    pts = [(float(x), float(y)) for x, y in polygon]
    if not pts:
        return []
    if len(pts) == 1:
        return pts * 2
    if not _same_point(pts[0], pts[-1]):
        pts.append(pts[0])
    return pts


def parse_waam_plan_positions(plan_path: os.PathLike[str] | str) -> Tuple[np.ndarray, np.ndarray]:
    """
    Convert a WAAM plan JSON file into a synthetic toolpath suitable for the
    torch viewer. Each contour loop is emitted as a bead-level deposition path,
    followed by zig-zag infill passes derived from the per-layer polygons.
    """
    plan_path = Path(plan_path)
    if not plan_path.exists():
        raise FileNotFoundError(str(plan_path))

    with plan_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    slicing = data.get("slicing", {})
    layers = slicing.get("layers") or []
    if not layers:
        raise ValueError("WAAM plan has no slicing layers to visualise.")

    layer_height = float(
        slicing.get("layer_height_mm")
        or data.get("metadata", {}).get("layer_height_mm")
        or 1.0
    )
    clearance_offset = max(layer_height, 4.0)

    points: List[Tuple[float, float, float]] = []
    deposition_segments: List[bool] = []

    def add_point(pt: Tuple[float, float, float], is_deposition: bool) -> None:
        px, py, pz = float(pt[0]), float(pt[1]), float(pt[2])
        candidate = (px, py, pz)
        if points and _same_point(points[-1], candidate):
            return
        if points:
            deposition_segments.append(is_deposition)
        points.append(candidate)

    def raise_to(z_target: float) -> None:
        if not points:
            return
        last = points[-1]
        if last[2] < z_target - 1e-6:
            add_point((last[0], last[1], z_target), False)

    def move_to(x: float, y: float, safe_z: float, work_z: float) -> None:
        if not points:
            add_point((x, y, safe_z), False)
            if abs(work_z - safe_z) > 1e-6:
                add_point((x, y, work_z), False)
            return
        last = points[-1]
        if last[2] < safe_z - 1e-6:
            add_point((last[0], last[1], safe_z), False)
        if not _same_point((last[0], last[1]), (x, y)):
            add_point((x, y, safe_z), False)
        if abs(work_z - safe_z) > 1e-6 or points[-1][2] != work_z:
            add_point((x, y, work_z), False)

    def emit_contour(contour: Dict, work_z: float, safe_z: float) -> None:
        polygon = contour.get("polygon")
        if not polygon:
            return
        loop = _ensure_closed_loop(polygon)
        if not loop:
            return
        start_x, start_y = loop[0]
        move_to(start_x, start_y, safe_z, work_z)
        for x, y in loop[1:]:
            add_point((x, y, work_z), True)

    def emit_infill(layer: Dict, work_z: float, safe_z: float) -> None:
        infill = layer.get("infill") or {}
        stripes = infill.get("polygons") or []
        if not stripes:
            return
        angle = float(infill.get("angle_deg", 0.0))
        along_x = abs(math.cos(math.radians(angle))) >= abs(math.sin(math.radians(angle)))
        for idx, stripe in enumerate(stripes):
            xs = [float(p[0]) for p in stripe]
            ys = [float(p[1]) for p in stripe]
            if not xs or not ys:
                continue
            xmin, xmax = min(xs), max(xs)
            ymin, ymax = min(ys), max(ys)
            if along_x:
                y_mid = 0.5 * (ymin + ymax)
                start = (xmin, y_mid)
                end = (xmax, y_mid)
            else:
                x_mid = 0.5 * (xmin + xmax)
                start = (x_mid, ymin)
                end = (x_mid, ymax)
            if idx % 2 == 1:
                start, end = end, start
            move_to(start[0], start[1], safe_z, work_z)
            add_point((end[0], end[1], work_z), True)

    sorted_layers = sorted(layers, key=lambda l: (
        float(l.get("z_top_mm") or l.get("z_mid_mm") or l.get("z_bottom_mm") or 0.0),
        l.get("index", 0)
    ))
    for layer in sorted_layers:
        z_top = float(layer.get("z_top_mm") or layer.get("z_mid_mm") or layer.get("z_bottom_mm") or 0.0)
        work_z = z_top
        safe_z = work_z + clearance_offset
        contours = layer.get("contours") or []
        contour_map = {c.get("id"): c for c in contours if c.get("polygon")}
        thermal = layer.get("thermal") or {}
        sequence = thermal.get("sequence") or [c.get("id") for c in contours]
        emitted_ids: set[str] = set()
        for cid in sequence:
            contour = contour_map.get(cid)
            if contour:
                emit_contour(contour, work_z, safe_z)
                if cid:
                    emitted_ids.add(cid)
        for contour in contours:
            cid = contour.get("id")
            if cid and cid in emitted_ids:
                continue
            emit_contour(contour, work_z, safe_z)
        emit_infill(layer, work_z, safe_z)
        raise_to(safe_z)

    if len(points) < 2:
        raise ValueError("WAAM plan did not produce enough path points for visualisation.")

    return np.array(points, dtype=float), np.array(deposition_segments, dtype=bool)


def _create_cylinder(
    center_x: float,
    center_y: float,
    center_z: float,
    color: str,
    radius: float = 1.6,
    height: float = 12.0,
    resolution: int = 10,
):
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    theta = np.linspace(0.0, 2.0 * math.pi, resolution)
    x_circle = radius * np.cos(theta)
    y_circle = radius * np.sin(theta)
    z_bottom = np.zeros_like(theta)
    z_top = np.full_like(theta, height)

    verts = []
    for i in range(resolution - 1):
        verts.append([
            [center_x + x_circle[i], center_y + y_circle[i], center_z + z_bottom[i]],
            [center_x + x_circle[i + 1], center_y + y_circle[i + 1], center_z + z_bottom[i + 1]],
            [center_x + x_circle[i + 1], center_y + y_circle[i + 1], center_z + z_top[i + 1]],
            [center_x + x_circle[i], center_y + y_circle[i], center_z + z_top[i]],
        ])

    verts.append([[center_x + x_circle[i], center_y + y_circle[i], center_z + z_bottom[i]] for i in range(resolution)])
    verts.append([[center_x + x_circle[i], center_y + y_circle[i], center_z + z_top[i]] for i in range(resolution)])

    return Poly3DCollection(verts, facecolors=color, linewidths=0.5, alpha=0.3)


def _load_torch_path(
    path: os.PathLike[str] | str,
    torch_on_codes: Optional[Sequence[str]] = None,
    torch_off_codes: Optional[Sequence[str]] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    candidate = Path(path)
    if candidate.suffix.lower() == ".json":
        return parse_waam_plan_positions(candidate)
    return nc_parser.parse_nc_file(
        candidate,
        torch_on_codes=torch_on_codes,
        torch_off_codes=torch_off_codes,
        allow_config=False,
    )


def _thin_path(
    points: np.ndarray,
    deposition_segments: np.ndarray,
    max_segments: int = _DEFAULT_MAX_SEGMENTS,
    z_jump_tol: float = 0.05,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Down-sample long toolpaths to keep the viewer responsive. We collapse runs
    of segments into a single segment and mark it as deposition if any segment
    in the run was deposition.
    """
    total_segments = len(deposition_segments)
    if max_segments <= 0 or total_segments <= max_segments:
        return points, deposition_segments

    stride = int(math.ceil(total_segments / float(max_segments)))
    keep_mask = np.zeros(len(points), dtype=bool)
    keep_mask[0] = True
    keep_mask[-1] = True
    keep_mask[::stride] = True

    # Preserve deposition transitions so we do not bridge torch on/off ranges.
    if total_segments > 1:
        dep_changes = np.where(deposition_segments[:-1] != deposition_segments[1:])[0] + 1
        keep_mask[dep_changes] = True

    # Preserve Z jumps so layer changes do not collapse into vertical spikes.
    if z_jump_tol > 0.0:
        z_jump_indices = np.where(
            np.abs(points[1:, 2] - points[:-1, 2]) > z_jump_tol
        )[0]
        keep_mask[z_jump_indices] = True
        keep_mask[z_jump_indices + 1] = True

    keep_indices = np.nonzero(keep_mask)[0]

    thinned_points = points[keep_indices]
    thinned_segments: List[bool] = []
    for start, end in zip(keep_indices[:-1], keep_indices[1:]):
        segment_slice = deposition_segments[start:end]
        thinned_segments.append(bool(np.any(segment_slice)))

    return thinned_points, np.array(thinned_segments, dtype=bool)


def show_torch_path_viewer(
    path: os.PathLike[str] | str,
    *,
    torch_on_codes: Optional[Iterable[str]] = None,
    torch_off_codes: Optional[Iterable[str]] = None,
    use_config: bool = True,
) -> None:
    """Visualise a torch path from an NC file or WAAM plan JSON."""
    import matplotlib.pyplot as plt
    from matplotlib import colors as mcolors
    from matplotlib.lines import Line2D
    from matplotlib.widgets import Button, Slider
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
    from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection

    resolved_on, resolved_off = nc_parser.resolve_torch_codes(
        torch_on_codes, torch_off_codes, allow_config=use_config
    )

    points, deposition_segments = _load_torch_path(
        path, torch_on_codes=resolved_on, torch_off_codes=resolved_off
    )
    points, deposition_segments = _thin_path(
        points, deposition_segments, max_segments=_DEFAULT_MAX_SEGMENTS
    )

    segment_count = len(deposition_segments)
    deposition_samples = np.concatenate([[False], deposition_segments])

    # Precompute segment styling: color by Z height for deposition, gray for travel.
    z_vals = 0.5 * (points[:-1, 2] + points[1:, 2]) if segment_count else np.array([0.0])
    z_min, z_max = float(z_vals.min()), float(z_vals.max())
    if math.isclose(z_min, z_max):
        z_max = z_min + 1.0
    norm = mcolors.Normalize(vmin=z_min, vmax=z_max)
    cmap = plt.get_cmap(_DEPOSITION_CMAP)
    deposition_colors = cmap(norm(z_vals))
    travel_color = np.array([0.35, 0.35, 0.35, 0.6])
    travel_colors = np.tile(travel_color, (segment_count, 1))
    segment_colors = np.where(deposition_segments[:, None], deposition_colors, travel_colors)
    segment_widths = np.where(deposition_segments, 2.5, 1.0)

    min_bounds = points.min(axis=0)
    max_bounds = points.max(axis=0)
    span = np.maximum(max_bounds - min_bounds, 1.0)
    padding = span * 0.1

    plt.style.use("fast")
    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection="3d")
    plt.subplots_adjust(left=0.05, right=0.95, top=0.94, bottom=0.18)

    ground_z = min_bounds[2]
    plane_x = [min_bounds[0] - padding[0], max_bounds[0] + padding[0]]
    plane_y = [min_bounds[1] - padding[1], max_bounds[1] + padding[1]]
    plane_vertices = [[
        [plane_x[0], plane_y[0], ground_z],
        [plane_x[1], plane_y[0], ground_z],
        [plane_x[1], plane_y[1], ground_z],
        [plane_x[0], plane_y[1], ground_z],
    ]]
    substrate = Poly3DCollection(plane_vertices, facecolor="lightgray", linewidths=0.8, edgecolor="dimgray", alpha=0.2)
    ax.add_collection3d(substrate)

    dummy_segment = [[
        [points[0, 0], points[0, 1], points[0, 2]],
        [points[0, 0] + 1e-6, points[0, 1], points[0, 2]],
    ]]
    line_collection = Line3DCollection(dummy_segment, colors=["none"], linewidths=[0.0])
    ax.add_collection3d(line_collection)

    torch_marker = _create_cylinder(points[0, 0], points[0, 1], points[0, 2], "orange")
    ax.add_collection3d(torch_marker)

    ax.set_title(f"Torch Path Viewer\n{Path(path).name}")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")
    ax.set_xlim(plane_x[0], plane_x[1])
    ax.set_ylim(plane_y[0], plane_y[1])
    ax.set_zlim(ground_z - padding[2], max_bounds[2] + padding[2])
    
    # Enforce 1:1:1 physical aspect ratio to avoid distortion
    x_span = plane_x[1] - plane_x[0]
    y_span = plane_y[1] - plane_y[0]
    z_span = (max_bounds[2] + padding[2]) - (ground_z - padding[2])
    
    # Check if the Matplotlib version supports set_box_aspect for 3D axes
    if hasattr(ax, 'set_box_aspect'):
        ax.set_box_aspect((x_span, y_span, z_span))

    ax_slider = plt.axes([0.18, 0.10, 0.65, 0.04])
    slider = Slider(
        ax_slider,
        "Step",
        0,
        segment_count,
        valinit=0,
        valstep=1,
    )

    ax_button = plt.axes([0.40, 0.02, 0.20, 0.05])
    button = Button(ax_button, "Play")

    legend_elements = [
        Line2D([0], [0], color=cmap(norm(z_max)), lw=3, label="Deposition (height)"),
        Line2D([0], [0], color=travel_color, lw=1.5, label="Travel"),
    ]
    ax.legend(handles=legend_elements, loc="upper right")
    ax.scatter(
        [points[0, 0]],
        [points[0, 1]],
        [points[0, 2]],
        color="lime",
        s=30,
        depthshade=False,
        label="Start",
    )

    def update(idx: int) -> None:
        nonlocal torch_marker
        segments = []

        if idx > 0:
            for i in range(idx):
                segments.append([points[i].tolist(), points[i + 1].tolist()])

        if segments:
            line_collection.set_segments(segments)
            line_collection.set_color(segment_colors[:idx])
            line_collection.set_linewidth(segment_widths[:idx].tolist())
        else:
            line_collection.set_segments(dummy_segment)
            line_collection.set_color(["none"])
            line_collection.set_linewidth([0.0])

        torch_marker.remove()
        torch_marker = _create_cylinder(
            points[idx, 0],
            points[idx, 1],
            points[idx, 2],
            "orange" if deposition_samples[idx] else "gray",
        )
        ax.add_collection3d(torch_marker)
        fig.canvas.draw_idle()

    def on_slider_change(val: float) -> None:
        update(int(val))

    slider.on_changed(on_slider_change)

    animating = False
    timer = fig.canvas.new_timer(interval=150)
    fig._torch_timer = timer  # persist timer with the figure

    def set_play_state(playing: bool) -> None:
        nonlocal animating
        animating = playing
        if animating:
            timer.start()
            button.label.set_text("Pause")
        else:
            timer.stop()
            button.label.set_text("Play")
        fig.canvas.draw_idle()

    def advance(_=None) -> None:
        if not animating:
            return
        current = int(slider.val)
        next_val = current + 1
        if next_val > segment_count:
            next_val = 0
        slider.set_val(next_val)

    timer.add_callback(advance, None)

    def toggle(_):
        set_play_state(not animating)

    button.on_clicked(toggle)

    def on_key(event) -> None:
        if event.key in (" ", "space", "spacebar", "p"):
            set_play_state(not animating)

    def on_close(_):
        set_play_state(False)

    fig.canvas.mpl_connect("key_press_event", on_key)
    fig.canvas.mpl_connect("close_event", on_close)

    update(0)
    if segment_count > 0:
        slider.set_val(1)
    plt.show()


# Backwards compatibility for callers using the old function name
show_nc_path_viewer = show_torch_path_viewer
