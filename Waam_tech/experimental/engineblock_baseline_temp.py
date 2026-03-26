"""
Temporary EngineBlock-specific WAAM baseline planner skeleton.

IMPORTANT:
- This is intentionally temporary and part-specific.
- It is designed only for the current EngineBlock-style geometry discussed in Discord.
- It is NOT a general WAAM planner.
- It is NOT machine-ready process code.
- It should be replaced later by a proper geometry-aware planner integrated into the main WAAM pipeline.

Purpose:
- Preserve the two base holes/windows early.
- Enforce bottom-up planning.
- Separate planning into 3 zones for this part.
- Provide a clean place to implement and test the logic before folding it into production code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple, Optional, Dict, Any
from pathlib import Path
import shutil


Point2D = Tuple[float, float]
Polygon2D = List[Point2D]


class PlanningError(RuntimeError):
    """Raised when the temporary planner detects invalid topology or unsafe assumptions."""


class Zone(str, Enum):
    ZONE_1 = "zone_1_base_with_holes_protected"
    ZONE_2 = "zone_2_transition_with_explicit_holes"
    ZONE_3 = "zone_3_stable_outer_plus_two_holes"


@dataclass
class PlannerParams:
    # Temporary defaults for this specific EngineBlock part only.
    z_min: float = 0.0
    z_max: float = 85.0
    layer_height: float = 1.2
    bead_width: float = 4.0
    min_fill_width: float = 8.0
    min_support_ratio: float = 0.65
    zone_1_max_z: float = 30.0
    zone_2_max_z: float = 55.0
    preserve_hole_future_layers: int = 3
    preserve_hole_past_layers: int = 2
    opening_mask_safety_factor: float = 0.5  # * bead_width


@dataclass
class PathSegment:
    kind: str  # contour | fill | travel | finish_contour
    points: List[Point2D]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LoopRecord:
    polygon: Polygon2D
    signed_area: float
    centroid: Point2D
    bbox: Tuple[float, float, float, float]
    loop_id: Optional[str] = None


@dataclass
class LayerRecord:
    index: int
    z: float
    raw_loops: List[LoopRecord] = field(default_factory=list)
    outer_loops: List[LoopRecord] = field(default_factory=list)
    inner_loops: List[LoopRecord] = field(default_factory=list)
    projected_void_masks: List[Polygon2D] = field(default_factory=list)
    material_regions: List[Polygon2D] = field(default_factory=list)
    paths: List[PathSegment] = field(default_factory=list)
    zone: Optional[Zone] = None
    topology_signature: Dict[str, Any] = field(default_factory=dict)


def _parse_layer_z_from_header(line: str) -> Optional[float]:
    import re

    match = re.search(r"Z=([0-9.]+)", line)
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


class EngineBlockBaselineTempPlanner:
    """
    Temporary planner for the current EngineBlock part.

    Scope limitations:
    - Assumes the part has a bottom-up build direction from z=0 to z=85.
    - Assumes two major hole/window regions must be preserved early.
    - Assumes the part evolves through the three known zones.
    - Assumes output is a planning skeleton, not final machine-certified G-code.
    """

    def __init__(self, params: Optional[PlannerParams] = None) -> None:
        self.params = params or PlannerParams()

    # ---------------------------------------------------------------------
    # Public entry point
    # ---------------------------------------------------------------------
    def generate_plan(self, part_solid: Any) -> List[LayerRecord]:
        """
        Build a temporary, EngineBlock-specific WAAM plan.

        part_solid:
            Placeholder for the FreeCAD/OpenCascade solid object.
        """
        layers = self._build_empty_layers()
        self._slice_part(part_solid, layers)
        self._classify_loops(layers)
        topology_graph = self._build_vertical_topology_graph(layers)
        persistent_holes = self._infer_persistent_holes(layers, topology_graph)
        self._build_projected_void_masks(layers, persistent_holes)
        self._build_material_regions(layers)
        self._assign_zones(layers)
        self._plan_paths(layers)
        self._validate_plan(layers)
        return layers

    # ---------------------------------------------------------------------
    # Layer generation
    # ---------------------------------------------------------------------
    def _build_empty_layers(self) -> List[LayerRecord]:
        layers: List[LayerRecord] = []
        z = self.params.z_min
        idx = 0
        while z <= self.params.z_max + 1e-9:
            layers.append(LayerRecord(index=idx, z=round(z, 6)))
            z += self.params.layer_height
            idx += 1
        return layers

    def _slice_part(self, part_solid: Any, layers: List[LayerRecord]) -> None:
        """
        Placeholder for true BRep slicing.

        Replace with:
        - FreeCAD Part sectioning or OCC section algorithms
        - extraction of closed loops per Z plane
        - conversion into LoopRecord items
        """
        for layer in layers:
            layer.raw_loops = self._slice_brep_at_z(part_solid, layer.z)
            layer.topology_signature["raw_loop_count"] = len(layer.raw_loops)

    def _slice_brep_at_z(self, part_solid: Any, z: float) -> List[LoopRecord]:
        # TODO: integrate with FreeCAD/OCC section API.
        # Temporary stub returns nothing until implemented.
        return []

    # ---------------------------------------------------------------------
    # Loop classification
    # ---------------------------------------------------------------------
    def _classify_loops(self, layers: List[LayerRecord]) -> None:
        for layer in layers:
            outer: List[LoopRecord] = []
            inner: List[LoopRecord] = []
            for loop in layer.raw_loops:
                if self._is_outer_loop(loop):
                    outer.append(loop)
                else:
                    inner.append(loop)
            layer.outer_loops = outer
            layer.inner_loops = inner
            layer.topology_signature.update(
                {
                    "outer_count": len(outer),
                    "inner_count": len(inner),
                }
            )

    def _is_outer_loop(self, loop: LoopRecord) -> bool:
        # Placeholder rule. Replace with containment + winding logic.
        return loop.signed_area >= 0

    # ---------------------------------------------------------------------
    # Topology continuity / persistent hole inference
    # ---------------------------------------------------------------------
    def _build_vertical_topology_graph(self, layers: List[LayerRecord]) -> Dict[int, Dict[str, Any]]:
        """
        Build a temporary adjacency/continuity structure across layers.
        In production this should track loop overlap, centroid drift, and containment continuity.
        """
        graph: Dict[int, Dict[str, Any]] = {}
        for i, layer in enumerate(layers):
            graph[i] = {
                "prev": i - 1 if i > 0 else None,
                "next": i + 1 if i < len(layers) - 1 else None,
            }
        return graph

    def _infer_persistent_holes(
        self,
        layers: List[LayerRecord],
        topology_graph: Dict[int, Dict[str, Any]],
    ) -> Dict[int, List[Polygon2D]]:
        """
        Temporary EngineBlock-specific hole persistence inference.

        Intended behavior:
        - If holes are visible in neighboring layers, preserve them early.
        - Avoid the old planner defect where the base gets filled in too aggressively.
        """
        persistent: Dict[int, List[Polygon2D]] = {}
        for layer in layers:
            persistent[layer.index] = []

            # If explicit inner loops already exist, preserve them directly.
            for hole in layer.inner_loops:
                persistent[layer.index].append(hole.polygon)

            # Temporary heuristic hook for this EngineBlock part:
            # when low layers are still topology-ambiguous, infer the two hole masks
            # from future or neighboring layers.
            inferred = self._infer_engineblock_windows_from_neighbors(layer, layers)
            persistent[layer.index].extend(inferred)

        return persistent

    def _infer_engineblock_windows_from_neighbors(
        self,
        layer: LayerRecord,
        layers: List[LayerRecord],
    ) -> List[Polygon2D]:
        """
        Temporary placeholder for the two-hole preservation logic.

        For this part only, this method should eventually:
        - look ahead a few layers into Zone 2 / Zone 3
        - find the two stable openings/windows
        - project them downward into lower ambiguous layers
        - return those projected polygons as protected no-deposit masks
        """
        # TODO: implement real projected hole inference for the EngineBlock part.
        return []

    # ---------------------------------------------------------------------
    # Material region construction
    # ---------------------------------------------------------------------
    def _build_projected_void_masks(
        self,
        layers: List[LayerRecord],
        persistent_holes: Dict[int, List[Polygon2D]],
    ) -> None:
        for layer in layers:
            layer.projected_void_masks = persistent_holes.get(layer.index, [])

    def _build_material_regions(self, layers: List[LayerRecord]) -> None:
        for layer in layers:
            # TODO: polygon boolean ops:
            # material = union(outer_loops) - union(inner_loops) - union(projected_void_masks)
            layer.material_regions = self._subtract_voids_for_layer(layer)
            layer.topology_signature["material_region_count"] = len(layer.material_regions)

    def _subtract_voids_for_layer(self, layer: LayerRecord) -> List[Polygon2D]:
        # Placeholder.
        # Replace with shapely / pyclipper / OCC-based 2D boolean region operations.
        return [loop.polygon for loop in layer.outer_loops]

    # ---------------------------------------------------------------------
    # Zone assignment
    # ---------------------------------------------------------------------
    def _assign_zones(self, layers: List[LayerRecord]) -> None:
        for layer in layers:
            if layer.z < self.params.zone_1_max_z:
                layer.zone = Zone.ZONE_1
            elif layer.z < self.params.zone_2_max_z:
                layer.zone = Zone.ZONE_2
            else:
                layer.zone = Zone.ZONE_3

    # ---------------------------------------------------------------------
    # Path planning
    # ---------------------------------------------------------------------
    def _plan_paths(self, layers: List[LayerRecord]) -> None:
        for layer in layers:
            if layer.zone == Zone.ZONE_1:
                layer.paths = self._plan_zone_1(layer)
            elif layer.zone == Zone.ZONE_2:
                layer.paths = self._plan_zone_2(layer)
            elif layer.zone == Zone.ZONE_3:
                layer.paths = self._plan_zone_3(layer)
            else:
                raise PlanningError(f"Layer {layer.index} has no assigned zone")

    def _plan_zone_1(self, layer: LayerRecord) -> List[PathSegment]:
        """
        Zone 1: protect the two holes early, no blanket fill.
        """
        paths: List[PathSegment] = []
        for region in layer.material_regions:
            paths.append(PathSegment(kind="contour", points=region, metadata={"zone": "zone_1"}))

            if self._region_supports_second_contour(region):
                offset = self._offset_polygon(region, -self.params.bead_width)
                if offset:
                    paths.append(
                        PathSegment(kind="contour", points=offset, metadata={"zone": "zone_1", "role": "stabilizer"})
                    )

        # Intentionally no monolithic fill in Zone 1.
        return paths

    def _plan_zone_2(self, layer: LayerRecord) -> List[PathSegment]:
        """
        Zone 2: explicit outer + inner loop logic, local fill only.
        """
        paths: List[PathSegment] = []

        for outer in layer.outer_loops:
            paths.append(PathSegment(kind="contour", points=outer.polygon, metadata={"zone": "zone_2", "role": "outer"}))

        hole_loops = self._collect_active_hole_loops(layer)
        for hole in hole_loops:
            paths.append(PathSegment(kind="contour", points=hole, metadata={"zone": "zone_2", "role": "hole"}))

        fill_regions = self._partition_fill_regions(layer)
        for region in fill_regions:
            if self._region_can_accept_local_fill(region):
                paths.append(PathSegment(kind="fill", points=region, metadata={"zone": "zone_2", "pattern": "local"}))

        return paths

    def _plan_zone_3(self, layer: LayerRecord) -> List[PathSegment]:
        """
        Zone 3: stable 1 outer + 2 holes + partitioned fill.
        """
        paths: List[PathSegment] = []

        for outer in layer.outer_loops:
            paths.append(PathSegment(kind="contour", points=outer.polygon, metadata={"zone": "zone_3", "role": "outer"}))

        hole_loops = self._collect_active_hole_loops(layer)
        for hole in hole_loops:
            paths.append(PathSegment(kind="contour", points=hole, metadata={"zone": "zone_3", "role": "hole"}))
            paths.append(PathSegment(kind="finish_contour", points=hole, metadata={"zone": "zone_3", "role": "hole_finish"}))

        fill_regions = self._partition_fill_regions(layer)
        for region in fill_regions:
            paths.append(PathSegment(kind="fill", points=region, metadata={"zone": "zone_3", "pattern": "partitioned"}))

        return paths

    # ---------------------------------------------------------------------
    # Helper planning logic
    # ---------------------------------------------------------------------
    def _collect_active_hole_loops(self, layer: LayerRecord) -> List[Polygon2D]:
        holes = [loop.polygon for loop in layer.inner_loops]
        holes.extend(layer.projected_void_masks)
        return holes

    def _partition_fill_regions(self, layer: LayerRecord) -> List[Polygon2D]:
        """
        Placeholder for topology-aware partitioned fill regions.
        Must avoid monolithic fill across the two holes.
        """
        # TODO: compute material subregions between outer boundary and the two holes.
        return []

    def _region_supports_second_contour(self, region: Polygon2D) -> bool:
        # TODO: estimate local wall thickness / offset survivability.
        return True

    def _region_can_accept_local_fill(self, region: Polygon2D) -> bool:
        # TODO: check ligament width, thermal constraint, and support ratio.
        return True

    def _offset_polygon(self, polygon: Polygon2D, offset_mm: float) -> Optional[Polygon2D]:
        # TODO: replace with a real polygon offset routine.
        return polygon if polygon else None

    # ---------------------------------------------------------------------
    # Validation
    # ---------------------------------------------------------------------
    def _validate_plan(self, layers: List[LayerRecord]) -> None:
        self._validate_bottom_up(layers)
        self._validate_hole_preservation(layers)
        self._validate_zone_expectations(layers)

    def _validate_bottom_up(self, layers: List[LayerRecord]) -> None:
        for i in range(len(layers) - 1):
            if not layers[i + 1].z > layers[i].z:
                raise PlanningError("Layer heights are not strictly bottom-up increasing")

    def _validate_hole_preservation(self, layers: List[LayerRecord]) -> None:
        """
        Temporary check for this part:
        if Zone 2/3 has stable holes but Zone 1 gets blanket-filled, abort.
        """
        for layer in layers:
            if layer.zone == Zone.ZONE_1:
                if self._zone_1_illegally_fills_hole_masks(layer):
                    raise PlanningError(
                        f"Layer {layer.index} violates projected hole masks in Zone 1"
                    )

    def _zone_1_illegally_fills_hole_masks(self, layer: LayerRecord) -> bool:
        # TODO: check overlap between any fill path and projected_void_masks.
        return False

    def _validate_zone_expectations(self, layers: List[LayerRecord]) -> None:
        for layer in layers:
            if layer.zone == Zone.ZONE_3:
                # Soft expectation for this temporary part-specific planner.
                if len(self._collect_active_hole_loops(layer)) < 2:
                    # keep as warning-worthy logic; could later become hard error
                    pass


# -------------------------------------------------------------------------
# Optional adapter hooks
# -------------------------------------------------------------------------

def generate_engineblock_temp_plan(part_solid: Any, params: Optional[PlannerParams] = None) -> List[LayerRecord]:
    """Convenience wrapper for temporary EngineBlock planning."""
    planner = EngineBlockBaselineTempPlanner(params=params)
    return planner.generate_plan(part_solid)


def export_engineblock_experimental_nc(output_dir: Path | str) -> Path:
    """
    Temporary experimental export hook for the current EngineBlock part.

    Current behavior:
    - does NOT replace the standard pipeline
    - does NOT overwrite waam_baseline.nc
    - creates a separate experimental file in nc_files/
    - produces content that is intentionally DIFFERENT from the baseline/improved NC
      by suppressing early spiral/fill-style paths in the low-Z region to better
      respect the two base openings.

    This is still a heuristic prototype, not the final geometry-aware planner.
    """
    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "waam_baseline_engineblock_exp.nc"

    candidate_sources = [
        Path("/Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/nc_files/waam_baseline_improved.nc"),
        Path("/Users/sustian/.openclaw/workspace/waam_baseline_improved_prototype.nc"),
        Path("/Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/nc_files/waam_baseline.nc"),
    ]

    source = None
    for src in candidate_sources:
        if src.is_file():
            source = src
            break

    if source is None:
        raise PlanningError(
            "No source NC found for temporary EngineBlock experimental export. "
            "Generate a baseline/prototype NC first."
        )

    text = source.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    new_lines: List[str] = []
    current_z: Optional[float] = None
    skip_path = False

    for line in lines:
        if line.startswith("(PROGRAM: "):
            new_lines.append("(PROGRAM: WAAM_BASELINE_ENGINEBLOCK_EXP.NC)")
            continue

        if line.startswith("(OPTIMIZATIONS:"):
            new_lines.append(
                "(OPTIMIZATIONS: ENGINEBLOCK EXPERIMENTAL, BOTTOM-UP, BASE HOLE MASK ON EARLY LAYERS, PROTOTYPE ONLY)"
            )
            continue

        if line.startswith("(WARNING: ENGINEERING PROTOTYPE ONLY"):
            new_lines.append(line)
            new_lines.append(
                "(EXPERIMENTAL DIFFERENCE: early broad spiral/infill paths are selectively suppressed to reduce deposition across the two base openings.)"
            )
            continue

        if line.startswith("(--- LAYER "):
            current_z = _parse_layer_z_from_header(line)
            skip_path = False
            new_lines.append(line)
            continue

        if line.startswith("(Path "):
            path_name = line[6:-1]
            if current_z is not None and current_z <= 35.0 and "SPIRAL" in path_name.upper():
                skip_path = True
                new_lines.append(
                    f"(SKIPPED {path_name} IN ENGINEBLOCK EXP: suppressed early spiral/fill to avoid sealing base holes)"
                )
                continue
            skip_path = False
            new_lines.append(line)
            continue

        if skip_path:
            continue

        new_lines.append(line)

    target.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    print(
        "This is a temporary EngineBlock-specific WAAM planner skeleton. "
        "Integrate it from FreeCAD/WAAM code, not as a standalone final tool."
    )
