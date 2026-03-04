"""Mesh-based feature analysis for WAAM planning."""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Tuple

import FreeCAD as App  # type: ignore


try:  # pragma: no cover - MeshPart only exists inside FreeCAD
    import MeshPart  # type: ignore
except ImportError:  # pragma: no cover
    MeshPart = None  # type: ignore


def _safe_vector(data: Iterable[float]) -> List[float]:
    return [float(v) for v in data]


def _vector_from(candidate) -> App.Vector:
    if isinstance(candidate, App.Vector):
        return App.Vector(candidate)
    if isinstance(candidate, (list, tuple)) and len(candidate) >= 3:
        return App.Vector(float(candidate[0]), float(candidate[1]), float(candidate[2]))
    raise TypeError("Candidate cannot be converted to App.Vector")


def _facet_centroid(points: Iterable[App.Vector]) -> List[float]:
    pts = [App.Vector(p) if isinstance(p, App.Vector) else _vector_from(p) for p in points]
    count = len(pts) or 1
    acc = App.Vector(0.0, 0.0, 0.0)
    for pt in pts:
        acc += pt
    acc.multiply(1.0 / count)
    return _safe_vector((acc.x, acc.y, acc.z))


def _matrix_to_rows(matrix: App.Matrix) -> List[List[float]]:
    return [
        [float(matrix.A11), float(matrix.A12), float(matrix.A13)],
        [float(matrix.A21), float(matrix.A22), float(matrix.A23)],
        [float(matrix.A31), float(matrix.A32), float(matrix.A33)],
    ]


def _jacobi_eigen_symmetric(
    matrix: List[List[float]],
    iterations: int = 64,
    tol: float = 1e-8,
) -> List[Tuple[float, List[float]]]:
    """Basic Jacobi eigen solver for symmetric 3x3 matrices."""
    a = [[float(v) for v in row] for row in matrix]
    v = [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ]

    def max_offdiag() -> Tuple[int, int, float]:
        idx_p, idx_q = 0, 1
        val = 0.0
        for i in range(3):
            for j in range(i + 1, 3):
                if abs(a[i][j]) > abs(val):
                    val = a[i][j]
                    idx_p, idx_q = i, j
        return idx_p, idx_q, val

    for _ in range(iterations):
        p, q, value = max_offdiag()
        if abs(value) < tol:
            break
        app = a[p][p]
        aqq = a[q][q]
        phi = 0.5 * math.atan2(2.0 * value, app - aqq)
        c = math.cos(phi)
        s = math.sin(phi)

        for j in range(3):
            apj = a[p][j]
            aqj = a[q][j]
            a[p][j] = c * apj - s * aqj
            a[q][j] = s * apj + c * aqj
        for i in range(3):
            aip = a[i][p]
            aiq = a[i][q]
            a[i][p] = c * aip - s * aiq
            a[i][q] = s * aip + c * aiq

        a[p][p] = c * c * app - 2.0 * s * c * value + s * s * aqq
        a[q][q] = s * s * app + 2.0 * s * c * value + c * c * aqq
        a[p][q] = 0.0
        a[q][p] = 0.0

        for i in range(3):
            vip = v[i][p]
            viq = v[i][q]
            v[i][p] = c * vip - s * viq
            v[i][q] = s * vip + c * viq

    eigen_pairs: List[Tuple[float, List[float]]] = []
    for i in range(3):
        eigen_pairs.append((float(a[i][i]), [float(v[j][i]) for j in range(3)]))
    eigen_pairs.sort(key=lambda item: item[0], reverse=True)
    return eigen_pairs


def _generate_mesh_from_shape(
    shape: App.Shape,
    linear_deflection: float = 0.4,
    angular_deflection: float = 0.5235987756,
) -> Optional["Mesh.Mesh"]:
    if MeshPart is None:
        return None
    return MeshPart.meshFromShape(
        Shape=shape,
        LinearDeflection=linear_deflection,
        AngularDeflection=angular_deflection,
        Relative=False,
    )


def _surface_breakdown(shape: App.Shape, build_axis: App.Vector) -> Dict[str, float]:
    top = bottom = side = 0.0
    axis = App.Vector(build_axis)
    axis.normalize()
    for face in shape.Faces:
        try:
            normal = face.normalAt(0.5, 0.5)
        except Exception:
            continue
        if normal is None:
            continue
        n = App.Vector(normal)
        if n.Length == 0:
            continue
        n.normalize()
        area = float(face.Area)
        dot = n.dot(axis)
        if dot >= 0.7071:
            top += area
        elif dot <= -0.7071:
            bottom += area
        else:
            side += area
    return {
        "top_area": top,
        "bottom_area": bottom,
        "side_area": side,
    }


def analyze_mesh_features(
    shape: App.Shape,
    *,
    mesh: Optional["Mesh.Mesh"] = None,
    build_axis: Optional[App.Vector] = None,
    material_density_kg_m3: Optional[float] = None,
    overhang_angle_deg: float = 45.0,
    thin_wall_threshold_mm: float = 6.0,
    mesh_linear_deflection: float = 0.4,
    mesh_angular_deflection: float = 0.5235987756,
    max_overhang_samples: int = 200,
    max_thin_wall_samples: int = 200,
) -> Dict:
    """
    Analyse the supplied part shape/mesh and return WAAM-centric geometric traits.
    """
    build_axis = App.Vector(0, 0, 1) if build_axis is None else App.Vector(build_axis)
    if build_axis.Length == 0:
        build_axis = App.Vector(0, 0, 1)
    build_axis.normalize()

    if mesh is None:
        mesh = _generate_mesh_from_shape(
            shape,
            linear_deflection=mesh_linear_deflection,
            angular_deflection=mesh_angular_deflection,
        )

    bbox = shape.BoundBox
    volume = float(getattr(shape, "Volume", 0.0))
    surface_area = float(getattr(shape, "Area", 0.0))
    bbox_volume = float(bbox.XLength * bbox.YLength * bbox.ZLength) if all(
        dim > 0.0 for dim in (bbox.XLength, bbox.YLength, bbox.ZLength)
    ) else 0.0
    compactness = (volume / bbox_volume) if bbox_volume else None

    mass = None
    if material_density_kg_m3 and material_density_kg_m3 > 0:
        mass = volume * material_density_kg_m3 / 1_000_000_000.0  # mm^3 -> m^3

    inertia = getattr(shape, "MatrixOfInertia", None)
    eigen_axes: List[Dict[str, object]] = []
    if inertia is not None:
        matrix = _matrix_to_rows(inertia)
        eigen_pairs = _jacobi_eigen_symmetric(matrix)
        for eigenvalue, eigenvector in eigen_pairs:
            vec = App.Vector(eigenvector)
            length = vec.Length
            if length:
                vec.normalize()
            eigen_axes.append(
                {
                    "moment": eigenvalue,
                    "axis": _safe_vector(vec),
                }
            )

    surface_split = _surface_breakdown(shape, build_axis)

    overhang_summary = {}
    thin_wall_summary = {}
    if mesh is not None:
        total_mesh_area = 0.0
        overhang_facets: List[Dict[str, object]] = []
        thin_wall_facets: List[Dict[str, object]] = []

        for facet in mesh.Facets:
            area = float(getattr(facet, "Area", 0.0))
            total_mesh_area += area
            normal = App.Vector(facet.Normal)
            normal_len = normal.Length
            if not normal_len:
                continue
            normal.normalize()
            angle_deg = float(
                math.degrees(
                    math.acos(
                        max(-1.0, min(1.0, normal.dot(build_axis)))
                    )
                )
            )
            if angle_deg > overhang_angle_deg:
                overhang_facets.append(
                    {
                        "center": _facet_centroid(facet.Points),
                        "normal": _safe_vector(normal),
                        "area": area,
                        "angle_deg": angle_deg,
                    }
                )

            points = [_vector_from(p) for p in facet.Points]
            if len(points) >= 2:
                edges = [
                    points[i].distanceToPoint(points[(i + 1) % len(points)])
                    for i in range(len(points))
                ]
                min_edge = min(edges) if edges else 0.0
                if min_edge > 0.0 and min_edge < thin_wall_threshold_mm:
                    thin_wall_facets.append(
                        {
                            "center": _facet_centroid(points),
                            "min_edge": float(min_edge),
                            "average_edge": float(sum(edges) / len(edges)),
                            "area": area,
                        }
                    )

        overhang_area = float(sum(item["area"] for item in overhang_facets))
        thin_wall_area = float(sum(item["area"] for item in thin_wall_facets))
        overhang_summary = {
            "threshold_deg": overhang_angle_deg,
            "facet_count": len(overhang_facets),
            "area_mm2": overhang_area,
            "coverage_pct": (overhang_area / total_mesh_area * 100.0)
            if total_mesh_area
            else 0.0,
            "samples": overhang_facets[:max_overhang_samples],
        }
        thin_wall_summary = {
            "threshold_mm": thin_wall_threshold_mm,
            "facet_count": len(thin_wall_facets),
            "area_mm2": thin_wall_area,
            "samples": thin_wall_facets[:max_thin_wall_samples],
        }

    curvature_samples: List[Dict[str, float]] = []
    for face_index, face in enumerate(shape.Faces, start=1):
        try:
            params = face.ParameterRange
            if callable(params):
                params = params()
        except Exception:
            continue
        if not isinstance(params, (tuple, list)) or len(params) != 4:
            continue
        umin, umax, vmin, vmax = [float(v) for v in params]
        u_values = [umin, 0.5 * (umin + umax), umax]
        v_values = [vmin, 0.5 * (vmin + vmax), vmax]
        for u in u_values:
            for v in v_values:
                try:
                    mean_curv, gaussian_curv = face.curvature(u, v)  # type: ignore[attr-defined]
                except Exception:
                    mean_curv = gaussian_curv = None
                try:
                    k1, k2 = face.curvatureAt(u, v)  # type: ignore[attr-defined]
                except Exception:
                    k1 = k2 = None
                if mean_curv is None and k1 is None:
                    continue
                curvature_samples.append(
                    {
                        "face": face_index,
                        "u": float(u),
                        "v": float(v),
                        "mean": float(mean_curv) if mean_curv is not None else None,
                        "gaussian": float(gaussian_curv)
                        if gaussian_curv is not None
                        else None,
                        "k1": float(k1) if k1 is not None else None,
                        "k2": float(k2) if k2 is not None else None,
                    }
                )

    mean_values = [sample["mean"] for sample in curvature_samples if sample["mean"] is not None]
    gaussian_values = [sample["gaussian"] for sample in curvature_samples if sample["gaussian"] is not None]
    curvature_summary = {
        "sample_count": len(curvature_samples),
        "mean_min": min(mean_values) if mean_values else None,
        "mean_max": max(mean_values) if mean_values else None,
        "mean_avg": (sum(mean_values) / len(mean_values)) if mean_values else None,
        "gaussian_min": min(gaussian_values) if gaussian_values else None,
        "gaussian_max": max(gaussian_values) if gaussian_values else None,
        "gaussian_avg": (sum(gaussian_values) / len(gaussian_values)) if gaussian_values else None,
    }

    return {
        "metadata": {
            "mesh_generation": {
                "linear_deflection": mesh_linear_deflection,
                "angular_deflection": mesh_angular_deflection,
            },
            "build_axis": _safe_vector(build_axis),
            "overhang_angle_deg": overhang_angle_deg,
            "thin_wall_threshold_mm": thin_wall_threshold_mm,
            "material_density_kg_m3": material_density_kg_m3,
        },
        "global": {
            "bbox_min": [float(bbox.XMin), float(bbox.YMin), float(bbox.ZMin)],
            "bbox_max": [float(bbox.XMax), float(bbox.YMax), float(bbox.ZMax)],
            "bbox_dimensions": [
                float(bbox.XLength),
                float(bbox.YLength),
                float(bbox.ZLength),
            ],
            "volume_mm3": volume,
            "surface_area_mm2": surface_area,
            "mass_estimate_kg": mass,
            "compactness": compactness,
            "principal_axes": eigen_axes,
            **surface_split,
        },
        "overhangs": overhang_summary,
        "thin_walls": thin_wall_summary,
        "curvature": {
            "summary": curvature_summary,
            "samples": curvature_samples,
        },
    }


__all__ = ["analyze_mesh_features"]
