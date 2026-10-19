"""XYZ point cloud export dialog for the WAAM workbench."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import FreeCAD as App  # type: ignore

from ..core.paths import get_output_dir
from ..core.point_cloud_export import POINT_CLOUD_COLUMNS, POINT_CLOUD_DECIMALS, write_point_cloud_table
from ..core.wcs_resolver import resolve_wcs
from .qt_compat import QtCore, QtWidgets

_POINT_CLOUD_NAME = "WAAM_XYZ_PointCloud"
_POINT_CLOUD_LABEL = "WAAM XYZ Point Cloud"

_MODE_LABELS = {
    "surface": "Surface cloud",
    "volume": "Solid grid (inside + surface)",
    "vertices": "Vertices and edges",
}

_DEFAULT_SPACING_FALLBACK_MM = 4.0
_DEFAULT_SPACING_DIVISIONS = 25.0
_MIN_POINT_SPACING_MM = 0.05
_MAX_POINT_SPACING_MM = 1000.0


def _shape_has_solid(shape) -> bool:
    try:
        solids = getattr(shape, "Solids", None)
        if solids is not None:
            return len(solids) > 0
    except Exception:
        pass
    return True


def _visible_solid_objects(doc) -> list[object]:
    visible_parts = []
    for obj in getattr(doc, "Objects", []):
        if getattr(obj, "Name", "") == _POINT_CLOUD_NAME:
            continue
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
            continue
        if not _shape_has_solid(shape):
            continue
        visible_parts.append(obj)
    return visible_parts


def _vector_key(vector, *, decimals: int = 6) -> tuple[float, float, float]:
    return (
        round(float(vector.x), decimals),
        round(float(vector.y), decimals),
        round(float(vector.z), decimals),
    )


def _object_label(obj) -> str:
    return str(getattr(obj, "Label", None) or getattr(obj, "Name", None) or "part")


def _object_placement(obj):
    try:
        return obj.getGlobalPlacement()
    except Exception:
        pass
    placement = getattr(obj, "Placement", None)
    if placement is not None:
        try:
            return App.Placement(placement)
        except Exception:
            return placement
    return App.Placement()


def _axis_values(min_value: float, max_value: float, spacing: float) -> Iterable[float]:
    spacing = max(float(spacing), 1e-6)
    span = max(0.0, float(max_value) - float(min_value))
    count = int(math.floor(span / spacing))
    for idx in range(count + 1):
        yield float(min_value) + idx * spacing
    if span > 1e-9:
        last = float(min_value) + count * spacing
        if abs(float(max_value) - last) > min(1e-6, spacing * 0.001):
            yield float(max_value)


def _edge_points(edge, spacing: float) -> Iterable[object]:
    try:
        yield from edge.discretize(Distance=float(spacing))
        return
    except Exception:
        pass
    try:
        length = float(getattr(edge, "Length", spacing))
        count = max(2, int(math.ceil(length / max(spacing, 1e-6))) + 1)
        yield from edge.discretize(Number=count)
    except Exception:
        return


def _shape_vertices_and_edges(shape, spacing: float) -> Iterable[object]:
    for vertex in getattr(shape, "Vertexes", []) or []:
        point = getattr(vertex, "Point", None)
        if point is not None:
            yield point
    for edge in getattr(shape, "Edges", []) or []:
        yield from _edge_points(edge, spacing)


def _shape_surface_points(shape, spacing: float, object_placement=None) -> Iterable[object]:
    object_placement = object_placement or App.Placement()
    for point in _shape_vertices_and_edges(shape, spacing):
        try:
            yield object_placement.multVec(App.Vector(point))
        except Exception:
            continue
    try:
        points, _facets = shape.tessellate(float(spacing))
    except Exception:
        return
    for point in points or []:
        try:
            yield object_placement.multVec(App.Vector(point))
        except Exception:
            continue


def _shape_is_inside(shape, point, tol: float = 1e-4) -> bool:
    if not hasattr(shape, "isInside"):
        return False
    try:
        return bool(shape.isInside(point, tol, False))
    except TypeError:
        try:
            return bool(shape.isInside(point, tol))
        except TypeError:
            try:
                return bool(shape.isInside(point))
            except Exception:
                return False


def _bbox_corners(bb) -> list[object]:
    return [
        App.Vector(bb.XMin, bb.YMin, bb.ZMin),
        App.Vector(bb.XMin, bb.YMin, bb.ZMax),
        App.Vector(bb.XMin, bb.YMax, bb.ZMin),
        App.Vector(bb.XMin, bb.YMax, bb.ZMax),
        App.Vector(bb.XMax, bb.YMin, bb.ZMin),
        App.Vector(bb.XMax, bb.YMin, bb.ZMax),
        App.Vector(bb.XMax, bb.YMax, bb.ZMin),
        App.Vector(bb.XMax, bb.YMax, bb.ZMax),
    ]


def _shape_bbox_in_frame(
    shape,
    frame_placement,
    object_placement=None,
) -> tuple[float, float, float, float, float, float]:
    bb = getattr(shape, "BoundBox", None)
    if bb is None:
        raise RuntimeError("Shape has no bounding box.")
    object_placement = object_placement or App.Placement()
    inv = frame_placement.inverse()
    local = [inv.multVec(object_placement.multVec(corner)) for corner in _bbox_corners(bb)]
    xs = [float(point.x) for point in local]
    ys = [float(point.y) for point in local]
    zs = [float(point.z) for point in local]
    return min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)


def _shape_volume_points(
    shape,
    spacing: float,
    frame_placement,
    object_placement=None,
    *,
    max_points: int,
) -> Iterable[object]:
    object_placement = object_placement or App.Placement()
    object_inv = object_placement.inverse()
    xmin, xmax, ymin, ymax, zmin, zmax = _shape_bbox_in_frame(shape, frame_placement, object_placement)
    produced = 0
    for x in _axis_values(xmin, xmax, spacing):
        for y in _axis_values(ymin, ymax, spacing):
            for z in _axis_values(zmin, zmax, spacing):
                world = frame_placement.multVec(App.Vector(x, y, z))
                local = object_inv.multVec(world)
                if _shape_is_inside(shape, local):
                    yield world
                    produced += 1
                    if produced >= max_points:
                        return


def _shape_volume_and_surface_points(
    shape,
    spacing: float,
    frame_placement,
    object_placement=None,
    *,
    max_points: int,
) -> Iterable[object]:
    yield from _shape_volume_points(
        shape,
        spacing,
        frame_placement,
        object_placement,
        max_points=max_points,
    )
    yield from _shape_surface_points(shape, spacing, object_placement)


def _global_bounds(objects: Sequence[object]) -> tuple[float, float, float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for obj in objects:
        shape = getattr(obj, "Shape", None)
        bb = getattr(shape, "BoundBox", None)
        if bb is None:
            continue
        placement = _object_placement(obj)
        for corner in _bbox_corners(bb):
            try:
                world = placement.multVec(corner)
            except Exception:
                world = corner
            xs.append(float(world.x))
            ys.append(float(world.y))
            zs.append(float(world.z))
    if not xs:
        return None
    return min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)


def _global_dimensions(objects: Sequence[object]) -> tuple[float, float, float] | None:
    bounds = _global_bounds(objects)
    if bounds is None:
        return None
    xmin, xmax, ymin, ymax, zmin, zmax = bounds
    return max(0.0, xmax - xmin), max(0.0, ymax - ymin), max(0.0, zmax - zmin)


def _default_spacing_from_dimensions(dimensions: Sequence[float] | None) -> float:
    if not dimensions:
        return _DEFAULT_SPACING_FALLBACK_MM
    finite_dims = [float(value) for value in dimensions if math.isfinite(float(value)) and float(value) > 0.0]
    if not finite_dims:
        return _DEFAULT_SPACING_FALLBACK_MM
    spacing = max(finite_dims) / _DEFAULT_SPACING_DIVISIONS
    return min(_MAX_POINT_SPACING_MM, max(_MIN_POINT_SPACING_MM, spacing))


def _dimensions_text(dimensions: Sequence[float] | None) -> str:
    if not dimensions:
        return "No visible solid detected."
    x_size, y_size, z_size = (float(value) for value in dimensions)
    return f"X {x_size:.3f} mm, Y {y_size:.3f} mm, Z {z_size:.3f} mm"


def _coordinate_frame(doc, frame_mode: str):
    if frame_mode == "world":
        return App.Placement(), "World XYZ", ()
    resolution = resolve_wcs(doc)
    label = resolution.label or resolution.source or "WAAM WCS"
    return resolution.placement, f"WAAM WCS ({label})", tuple(resolution.warnings or ())


def _columns(include_world: bool) -> tuple[str, ...]:
    if include_world:
        return POINT_CLOUD_COLUMNS
    return POINT_CLOUD_COLUMNS[:5]


def _world_vertices_and_edges(shape, spacing: float, object_placement=None) -> Iterable[object]:
    object_placement = object_placement or App.Placement()
    for point in _shape_vertices_and_edges(shape, spacing):
        try:
            yield object_placement.multVec(App.Vector(point))
        except Exception:
            continue


def _collect_rows(
    objects: Sequence[object],
    *,
    placement,
    spacing: float,
    mode: str,
    max_points: int,
    include_world: bool,
) -> tuple[list[dict[str, object]], list[object], bool]:
    inv = placement.inverse()
    rows: list[dict[str, object]] = []
    preview_points = []
    seen = set()
    truncated = False

    for obj in objects:
        shape = getattr(obj, "Shape", None)
        if shape is None:
            continue
        label = _object_label(obj)
        object_placement = _object_placement(obj)
        if mode == "volume":
            point_iter = _shape_volume_and_surface_points(
                shape,
                spacing,
                placement,
                object_placement,
                max_points=max_points,
            )
        elif mode == "vertices":
            point_iter = _world_vertices_and_edges(shape, spacing, object_placement)
        else:
            point_iter = _shape_surface_points(shape, spacing, object_placement)

        for world in point_iter:
            try:
                world_vec = App.Vector(world)
                local = inv.multVec(world_vec)
            except Exception:
                continue
            key = _vector_key(local)
            if key in seen:
                continue
            seen.add(key)
            index = len(rows) + 1
            row = {
                "object": label,
                "index": index,
                "x_mm": round(float(local.x), POINT_CLOUD_DECIMALS),
                "y_mm": round(float(local.y), POINT_CLOUD_DECIMALS),
                "z_mm": round(float(local.z), POINT_CLOUD_DECIMALS),
            }
            if include_world:
                row.update(
                    {
                        "world_x_mm": round(float(world_vec.x), POINT_CLOUD_DECIMALS),
                        "world_y_mm": round(float(world_vec.y), POINT_CLOUD_DECIMALS),
                        "world_z_mm": round(float(world_vec.z), POINT_CLOUD_DECIMALS),
                    }
                )
            rows.append(row)
            preview_points.append(world_vec)
            if len(rows) >= max_points:
                truncated = True
                return rows, preview_points, truncated
    return rows, preview_points, truncated


def _style_preview(obj) -> None:
    try:
        obj.Label = _POINT_CLOUD_LABEL
    except Exception:
        pass
    view = getattr(obj, "ViewObject", None)
    if view is None:
        return
    for attr, value in (
        ("PointSize", 5),
        ("PointColor", (0.0, 0.55, 1.0)),
        ("ShapeColor", (0.0, 0.55, 1.0)),
    ):
        try:
            setattr(view, attr, value)
        except Exception:
            pass


def _create_preview_point_cloud(doc, points: Sequence[object]) -> None:
    existing = doc.getObject(_POINT_CLOUD_NAME) if hasattr(doc, "getObject") else None
    try:
        import Points  # type: ignore

        target = existing if existing is not None and getattr(existing, "TypeId", "") == "Points::Feature" else None
        if target is None and existing is not None and hasattr(doc, "removeObject"):
            try:
                doc.removeObject(getattr(existing, "Name", _POINT_CLOUD_NAME))
            except Exception:
                pass
        if target is None:
            target = doc.addObject("Points::Feature", _POINT_CLOUD_NAME)
        target.Points = Points.Points(list(points))
        _style_preview(target)
        doc.recompute()
        return
    except Exception:
        pass

    import Part  # type: ignore

    target = existing if existing is not None and hasattr(existing, "Shape") else None
    if target is None:
        target = doc.addObject("Part::Feature", _POINT_CLOUD_NAME)
    target.Shape = Part.makeCompound([Part.makePoint(point) for point in points])
    _style_preview(target)
    doc.recompute()


def _bounds_text(rows: Sequence[Mapping[str, object]]) -> str:
    xs = [float(row["x_mm"]) for row in rows]
    ys = [float(row["y_mm"]) for row in rows]
    zs = [float(row["z_mm"]) for row in rows]
    return (
        f"{len(rows)} points\n"
        f"X: {min(xs):.3f} to {max(xs):.3f}\n"
        f"Y: {min(ys):.3f} to {max(ys):.3f}\n"
        f"Z: {min(zs):.3f} to {max(zs):.3f}"
    )


class XyzPointCloudDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("WAAM - XYZ Point Cloud")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.resize(540, 360)

        initial_objects = []
        initial_dimensions = None
        try:
            doc = App.ActiveDocument
            if doc is not None:
                initial_objects = _visible_solid_objects(doc)
                initial_dimensions = _global_dimensions(initial_objects)
        except Exception:
            pass
        default_spacing = _default_spacing_from_dimensions(initial_dimensions)

        self.spacing_spin = QtWidgets.QDoubleSpinBox(self)
        self.spacing_spin.setRange(_MIN_POINT_SPACING_MM, _MAX_POINT_SPACING_MM)
        self.spacing_spin.setDecimals(3)
        self.spacing_spin.setValue(default_spacing)
        self.spacing_spin.setSuffix(" mm")
        self.spacing_spin.setToolTip(
            "Nominal spacing between generated point samples; initial value uses the part size."
        )

        self.max_points_spin = QtWidgets.QSpinBox(self)
        self.max_points_spin.setRange(10, 1000000)
        self.max_points_spin.setValue(50000)
        self.max_points_spin.setSingleStep(1000)
        self.max_points_spin.setToolTip("Stop exporting after this many points.")

        self.mode_combo = QtWidgets.QComboBox(self)
        for key in ("volume", "surface", "vertices"):
            self.mode_combo.addItem(_MODE_LABELS[key], key)
        self.mode_combo.setToolTip("Choose how points are sampled from the visible part.")

        self.frame_combo = QtWidgets.QComboBox(self)
        self.frame_combo.addItem("Active WAAM WCS", "wcs")
        self.frame_combo.addItem("World XYZ", "world")
        self.frame_combo.setToolTip("Coordinates are exported in this axis frame.")

        self.format_combo = QtWidgets.QComboBox(self)
        self.format_combo.addItem("CSV", "csv")
        self.format_combo.addItem("Excel workbook (.xlsx)", "xlsx")
        self.format_combo.currentIndexChanged.connect(self._refresh_default_output_path)

        self.include_world_check = QtWidgets.QCheckBox("Include world XYZ columns", self)
        self.include_world_check.setChecked(True)
        self.include_world_check.setToolTip("Keep world coordinates alongside the selected axis-frame coordinates.")

        self.preview_check = QtWidgets.QCheckBox("Create/update point cloud in document", self)
        self.preview_check.setChecked(True)

        self.output_edit = QtWidgets.QLineEdit(self)
        self.output_edit.setMinimumWidth(0)
        browse_button = QtWidgets.QPushButton("Browse...", self)
        browse_button.clicked.connect(self._browse_output)
        output_row = QtWidgets.QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(browse_button)

        form = QtWidgets.QFormLayout()
        form.addRow("Dot spacing:", self.spacing_spin)
        self.dimensions_label = QtWidgets.QLabel(_dimensions_text(initial_dimensions), self)
        self.dimensions_label.setWordWrap(True)
        form.addRow("Global size:", self.dimensions_label)
        form.addRow("Point mode:", self.mode_combo)
        form.addRow("Axis frame:", self.frame_combo)
        form.addRow("Max points:", self.max_points_spin)
        form.addRow("Output format:", self.format_combo)
        form.addRow("", self.include_world_check)
        form.addRow("", self.preview_check)
        form.addRow("Output file:", output_row)

        self.status_label = QtWidgets.QLabel(self)
        self.status_label.setWordWrap(True)
        self.status_label.setText(
            f"Ready. Default spacing: {default_spacing:.3f} mm from global part size."
            if initial_dimensions
            else f"Ready. Default spacing: {default_spacing:.3f} mm."
        )

        export_button = QtWidgets.QPushButton("Export Point Cloud", self)
        export_button.clicked.connect(self._export)
        close_button = QtWidgets.QPushButton("Close", self)
        close_button.clicked.connect(self.close)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(export_button)
        buttons.addWidget(close_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)

        self._refresh_default_output_path()

    def _selected_format(self) -> str:
        return str(self.format_combo.currentData() or "csv")

    def _default_output_path(self) -> Path:
        suffix = ".xlsx" if self._selected_format() == "xlsx" else ".csv"
        return get_output_dir() / "point_cloud" / f"waam_xyz_point_cloud{suffix}"

    def _refresh_default_output_path(self, *_args) -> None:
        current = Path(self.output_edit.text()).name if self.output_edit.text().strip() else ""
        if not current or current.startswith("waam_xyz_point_cloud"):
            self.output_edit.setText(str(self._default_output_path()))

    def _browse_output(self, *_args) -> None:
        fmt = self._selected_format()
        filter_text = "Excel workbook (*.xlsx)" if fmt == "xlsx" else "CSV files (*.csv)"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save XYZ Point Cloud",
            self.output_edit.text() or str(self._default_output_path()),
            f"{filter_text};;All files (*.*)",
        )
        if path:
            self.output_edit.setText(path)

    def _metadata(
        self,
        *,
        frame_label: str,
        objects: Sequence[object],
        truncated: bool,
    ) -> dict[str, object]:
        metadata = {
            "coordinate_frame": frame_label,
            "spacing_mm": float(self.spacing_spin.value()),
            "point_mode": str(self.mode_combo.currentData() or "volume"),
            "objects": ", ".join(_object_label(obj) for obj in objects),
            "truncated": bool(truncated),
        }
        dimensions = _global_dimensions(objects)
        if dimensions:
            metadata.update(
                {
                    "global_x_size_mm": float(dimensions[0]),
                    "global_y_size_mm": float(dimensions[1]),
                    "global_z_size_mm": float(dimensions[2]),
                }
            )
        return metadata

    def _export(self, *_args) -> None:
        doc = App.ActiveDocument
        if doc is None:
            QtWidgets.QMessageBox.information(self, "WAAM", "Open a document before exporting a point cloud.")
            return

        objects = _visible_solid_objects(doc)
        if not objects:
            QtWidgets.QMessageBox.information(self, "WAAM", "No visible solids found for point cloud export.")
            return

        try:
            placement, frame_label, warnings = _coordinate_frame(doc, str(self.frame_combo.currentData() or "wcs"))
            rows, preview_points, truncated = _collect_rows(
                objects,
                placement=placement,
                spacing=float(self.spacing_spin.value()),
                mode=str(self.mode_combo.currentData() or "volume"),
                max_points=int(self.max_points_spin.value()),
                include_world=bool(self.include_world_check.isChecked()),
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Failed to build point cloud:\n{exc}")
            return

        if not rows:
            QtWidgets.QMessageBox.information(self, "WAAM", "No points were generated for the visible part.")
            return

        if self.preview_check.isChecked():
            try:
                _create_preview_point_cloud(doc, preview_points)
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "WAAM", f"Point file will be written, but preview failed:\n{exc}")

        fmt = self._selected_format()
        target = Path(self.output_edit.text() or self._default_output_path()).expanduser()
        if target.suffix.lower() not in {".csv", ".xlsx"}:
            target = target.with_suffix(".xlsx" if fmt == "xlsx" else ".csv")
        try:
            output = write_point_cloud_table(
                target,
                rows,
                file_format=fmt,
                columns=_columns(bool(self.include_world_check.isChecked())),
                metadata=self._metadata(frame_label=frame_label, objects=objects, truncated=truncated),
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Failed to write point cloud file:\n{exc}")
            return

        warning_text = ""
        if truncated:
            warning_text += "\n\nThe point list was truncated at the configured maximum."
        if warnings:
            warning_text += "\n\nWCS warnings:\n" + "\n".join(f"- {msg}" for msg in warnings)

        summary = _bounds_text(rows)
        self.status_label.setText(f"Exported to {output}\n{summary}")
        App.Console.PrintMessage(f"WAAM: XYZ point cloud exported to {output}\n{summary}\n")
        QtWidgets.QMessageBox.information(self, "WAAM", f"Point cloud exported to:\n{output}\n\n{summary}{warning_text}")


def _parent_window():
    try:
        import FreeCADGui as Gui  # type: ignore

        return Gui.getMainWindow() if hasattr(Gui, "getMainWindow") else None
    except Exception:
        return None


def show_xyz_point_cloud_dialog(parent=None) -> None:
    dialog = XyzPointCloudDialog(parent if parent is not None else _parent_window())
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


__all__ = ["XyzPointCloudDialog", "show_xyz_point_cloud_dialog"]
