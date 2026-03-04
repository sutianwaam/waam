"""Generate NC from WAAM DSL (.waam / .llm_waam) without calling an LLM."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..core.config_loader import load_standard_config
from ..core.paths import get_output_dir


Point = Tuple[float, float]


@dataclass
class WaamContour:
    points: List[Point]
    is_outer: bool


@dataclass
class WaamLayer:
    index: int
    z: Optional[float]
    contours: List[WaamContour]


@dataclass
class WaamDslDocument:
    part_name: str
    units: str
    layer_height: Optional[float]
    bead_width: Optional[float]
    layers: List[WaamLayer]


class WaamDslParseError(RuntimeError):
    """Raised when the WAAM DSL text cannot be parsed."""


_POINT_RE = re.compile(r"\(\s*([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)\s*\)")


def _parse_point_list(text: str) -> List[Point]:
    matches = _POINT_RE.findall(text)
    return [(float(x), float(y)) for x, y in matches]


def _default_output_for(dsl_path: Path) -> Path:
    root = get_output_dir()
    if dsl_path.parent.name == "dsl_waam":
        root = dsl_path.parent.parent
    return root / "nc_files" / f"{dsl_path.stem}_WAAM.nc"


def parse_waam_dsl(path: Path | str) -> WaamDslDocument:
    """Parse standard WAAM DSL or PATHLIB-based .llm_waam text into layers/contours."""
    dsl_path = Path(path).expanduser().resolve()
    if not dsl_path.is_file():
        raise FileNotFoundError(f"WAAM DSL file not found: {dsl_path}")
    lines = [ln.strip() for ln in dsl_path.read_text(encoding="utf-8").splitlines()]

    part_name = "WAAM_PART"
    units = "mm"
    layer_height: Optional[float] = None
    bead_width: Optional[float] = None

    pathlib: Dict[str, Dict[str, object]] = {}
    in_pathlib = False
    current_path_id: Optional[str] = None
    current_path_pts: List[Point] = []
    current_path_role: Optional[str] = None

    layers: List[WaamLayer] = []
    current_layer: Optional[WaamLayer] = None
    pending_points: List[Point] = []
    pending_is_outer = True

    def _flush_contour() -> None:
        nonlocal pending_points
        if current_layer is None or not pending_points:
            pending_points = []
            return
        current_layer.contours.append(WaamContour(points=list(pending_points), is_outer=pending_is_outer))
        pending_points = []

    def _flush_layer() -> None:
        nonlocal current_layer
        _flush_contour()
        if current_layer is not None:
            layers.append(current_layer)
        current_layer = None

    for raw in lines:
        if not raw:
            continue
        # Header
        if raw.startswith("WAAMPART"):
            part_name = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else part_name
            continue
        if raw.startswith("UNITS"):
            units = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else units
            continue
        if raw.startswith("LAYER_HEIGHT"):
            try:
                layer_height = float(raw.split()[1])
            except Exception:
                pass
            continue
        if raw.startswith("BEAD_WIDTH"):
            try:
                bead_width = float(raw.split()[1])
            except Exception:
                pass
            continue

        # PATHLIB handling for .llm_waam
        if raw == "PATHLIB_BEGIN":
            in_pathlib = True
            continue
        if raw == "PATHLIB_END":
            in_pathlib = False
            continue
        if in_pathlib:
            if raw.startswith("PATH"):
                _flush_contour()
                parts = dict(item.split("=", 1) for item in raw.split()[1:] if "=" in item)
                current_path_id = parts.get("ID")
                current_path_role = parts.get("ROLE_HINT")
                current_path_pts = []
                continue
            if raw.startswith("END_PATH"):
                if current_path_id:
                    pathlib[current_path_id] = {
                        "points": list(current_path_pts),
                        "role": current_path_role,
                    }
                current_path_id = None
                current_path_pts = []
                current_path_role = None
                continue
            pts = _parse_point_list(raw)
            if current_path_id and pts:
                current_path_pts.extend(pts)
            continue

        # Layer boundaries
        if raw.startswith("LAYER"):
            _flush_layer()
            parts = raw.split()
            try:
                idx = int(parts[1])
            except Exception as exc:
                raise WaamDslParseError(f"Malformed LAYER line: {raw}") from exc
            z_val: Optional[float] = None
            for token in parts[2:]:
                if token.startswith("Z="):
                    try:
                        z_val = float(token.split("=", 1)[1])
                    except Exception:
                        z_val = None
            current_layer = WaamLayer(index=idx, z=z_val, contours=[])
            pending_points = []
            continue

        if raw.startswith("END_LAYER"):
            _flush_layer()
            continue

        # Contours: standard form
        if raw in {"OUTER_CONTOUR", "INNER_CONTOUR"}:
            _flush_contour()
            pending_is_outer = raw.startswith("OUTER")
            continue

        # Contours: USE_PATH references
        if raw.startswith("USE_PATH"):
            if current_layer is None:
                raise WaamDslParseError("USE_PATH encountered outside of a LAYER.")
            tokens = dict(item.split("=", 1) for item in raw.split()[1:] if "=" in item)
            path_id = tokens.get("ID")
            role = tokens.get("AS")
            if not path_id or path_id not in pathlib:
                raise WaamDslParseError(f"Unknown PATH ID referenced: {path_id}")
            path_rec = pathlib[path_id]
            pts = list(path_rec.get("points") or [])
            if not pts:
                raise WaamDslParseError(f"PATH {path_id} has no points.")
            is_outer = (role or "").upper().startswith("OUTER") or (str(path_rec.get("role") or "").upper() == "OUTER")
            current_layer.contours.append(WaamContour(points=pts, is_outer=is_outer))
            continue

        # Points for the current contour (standard DSL)
        if "(" in raw and current_layer is not None:
            pts = _parse_point_list(raw)
            if pts:
                pending_points.extend(pts)
            continue

    _flush_layer()

    if not layers:
        raise WaamDslParseError("No layers were found in the WAAM DSL file.")

    return WaamDslDocument(
        part_name=part_name,
        units=units,
        layer_height=layer_height,
        bead_width=bead_width,
        layers=layers,
    )


def generate_nc_from_dsl(
    dsl_path: Path | str,
    *,
    output_path: Path | str | None = None,
    config_path: Optional[str] = None,
) -> Path:
    """Convert WAAM DSL geometry into a basic NC program."""
    doc = parse_waam_dsl(dsl_path)
    cfg = load_standard_config(config_path)

    heights = cfg.get("heights", {}) if isinstance(cfg, dict) else {}
    clearance = float(heights.get("clearance") or 15.0)
    rapid_z = float(heights.get("rapidZ") or clearance)

    proc = (cfg.get("process") or {}).get("deposition", {}) if isinstance(cfg, dict) else {}
    interpass = (cfg.get("process") or {}).get("interpass", {}) if isinstance(cfg, dict) else {}
    defaults = (cfg.get("defaults") or {}) if isinstance(cfg, dict) else {}

    bead_width = float(doc.bead_width or proc.get("bead_width_mm") or defaults.get("deposition", {}).get("bead_width", 6.0))
    travel_speed = float(proc.get("travel_speed_mm_min") or defaults.get("deposition", {}).get("travel_speed", 300.0))
    wire_feed = float(proc.get("wire_feed_mm_min") or defaults.get("deposition", {}).get("wire_feed", 3200.0))
    z_feed = float(defaults.get("roughing", {}).get("feed_z") or defaults.get("finishing", {}).get("feed_z") or 200.0)
    dwell_layer = float(proc.get("dwell_per_layer_s") or 1.5)
    cooling_time = float(interpass.get("max_dwell_s") or 45.0)
    arc_start_delay = float((cfg.get("post") or {}).get("arc_start_delay_s") or 0.4)
    arc_stop_delay = float((cfg.get("post") or {}).get("arc_stop_delay_s") or 0.2)
    gas_preflow = float((cfg.get("post") or {}).get("gas_preflow_s") or 1.0)
    gas_postflow = float((cfg.get("post") or {}).get("gas_postflow_s") or 2.0)

    post = cfg.get("post") or {}
    torch_on = post.get("torch_on", "M62 P0")
    torch_off = post.get("torch_off", "M63 P0")
    gas_on = post.get("gas_on", "M64 P1")
    gas_off = post.get("gas_off", "M65 P1")
    wire_feed_cmd = (post.get("wire_feed") or "M68 E0 Q{wire_feed:.0f}").format(wire_feed=wire_feed)

    target_path = Path(output_path).expanduser().resolve() if output_path else _default_output_for(Path(dsl_path))
    target_path.parent.mkdir(parents=True, exist_ok=True)

    preamble = [
        "%",
        f"O0001 (WAAM {doc.part_name} DSL Program)",
        f"(Source: {Path(dsl_path).name})",
        f"(UNITS: {doc.units})",
        f"(BEAD_WIDTH: {bead_width:.3f}  LAYER_HEIGHT: {(doc.layer_height or 0):.3f})",
        "G21 G90 G94",
        "G17",
        "G40 G49 G80",
        f"G0 Z{clearance:.3f}  (SAFE HEIGHT)",
        "",
    ]
    footer = [
        torch_off,
        gas_off,
        f"G0 Z{clearance:.3f}",
        "G0 X0.0 Y0.0",
        "M30",
        "%",
    ]

    lines = list(preamble)
    layers_sorted = sorted(doc.layers, key=lambda l: (l.z if l.z is not None else (l.index * (doc.layer_height or 1.0)), l.index))
    layer_height = doc.layer_height or 1.0
    z_shift = 0.0
    z_values = [layer.z for layer in layers_sorted if layer.z is not None]
    if z_values:
        min_z = min(z_values)
        if abs(min_z) > 1e-6:
            z_shift = -min_z
    for idx, layer in enumerate(layers_sorted, start=1):
        z_val = layer.z if layer.z is not None else (layer.index if layer.index > 0 else idx) * layer_height
        z_val += z_shift
        lines.append(f"(--- LAYER {layer.index} Z={z_val:.3f} ---)")
        for contour_num, contour in enumerate(layer.contours, start=1):
            pts = contour.points
            if len(pts) < 2:
                continue
            start_pt = pts[0]
            lines.append(f"(Contour {contour_num} {'OUTER' if contour.is_outer else 'INNER'})")
            lines.append(f"G0 X{start_pt[0]:.3f} Y{start_pt[1]:.3f} Z{clearance:.3f}")
            lines.append(gas_on)
            lines.append(f"G4 P{gas_preflow:.1f}")
            lines.append(wire_feed_cmd)
            lines.append(torch_on)
            lines.append(f"G4 P{arc_start_delay:.1f}")
            lines.append(f"G1 Z{z_val:.3f} F{z_feed:.1f}")
            lines.append(f"F{travel_speed:.0f}")
            for (x, y) in pts[1:]:
                lines.append(f"G1 X{x:.3f} Y{y:.3f}")
            lines.append(f"G4 P{dwell_layer:.1f}")
            lines.append(torch_off)
            lines.append(f"G4 P{arc_stop_delay:.1f}")
            lines.append(gas_off)
            lines.append(f"G4 P{gas_postflow:.1f}")
            lines.append(f"G0 Z{clearance:.3f}")
            lines.append(f"G0 X{pts[-1][0]:.3f} Y{pts[-1][1]:.3f} Z{rapid_z:.3f}")
            lines.append(f"G4 P{cooling_time:.1f}")
            lines.append("")

    lines.extend(footer)
    target_path.write_text("\n".join(lines), encoding="utf-8")
    return target_path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Convert a WAAM DSL (.waam / .llm_waam) file to NC.")
    parser.add_argument("input", help="Input WAAM DSL file.")
    parser.add_argument("-o", "--output", help="Output NC path. Defaults next to WAAM output/nc_files/<name>_WAAM.nc")
    parser.add_argument("--config", help="Path to standard_waam.json override.")
    args = parser.parse_args(argv)

    try:
        target = generate_nc_from_dsl(
            args.input,
            output_path=args.output,
            config_path=args.config,
        )
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1
    print(f"[INFO] NC written to: {target}")
    return 0


__all__ = [
    "WaamDslParseError",
    "WaamContour",
    "WaamLayer",
    "WaamDslDocument",
    "parse_waam_dsl",
    "generate_nc_from_dsl",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
