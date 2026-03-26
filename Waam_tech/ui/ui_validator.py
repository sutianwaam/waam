
"""
Validator UI component for WAAM DSL files.
Provides layer analysis, statistics, and 2D contour visualization.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .mpl_compat import get_matplotlib_qt
from .qt_compat import QtCore, QtGui, QtWidgets

# Optional matplotlib integration
try:
    _, Figure, FigureCanvas = get_matplotlib_qt()
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


import re

def _parse_waam_dsl(content: str) -> Tuple[List[Dict], Dict]:
    """
    Parse text-based WAAM DSL content to extract layers and global stats.
    Returns: (layers_list, stats_dict)
    """
    lines = content.splitlines()
    layers = []
    current_layer = None
    
    # Simple state machine parser
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if line.startswith("LAYER"):
            # Start new layer: LAYER <index> Z=<z>
            parts = line.split()
            z_val = 0.0
            idx = 0
            for p in parts:
                if p.startswith("Z="):
                    try:
                        z_val = float(p.split("=")[1])
                    except ValueError:
                        pass
                elif p.isdigit():
                   idx = int(p)
            
            current_layer = {
                "index": idx,
                "z": z_val,
                "path_ids": [], # We will simulate path IDs for visualization
                "contours": []
            }
            layers.append(current_layer)
            
        elif line.startswith("END_LAYER"):
            current_layer = None
            
        elif "CONTOUR" in line and current_layer is not None:
             # Capture contour type
             current_layer["contours"].append({"type": line, "points": []})
             
        elif "->" in line and current_layer is not None:
            # Coordinate line: (x,y) -> (x,y)
            # Add to last contour
            if current_layer["contours"]:
                pts_str = line.split("->")
                points = []
                for pt in pts_str:
                    pt = pt.strip().strip("()")
                    if "," in pt:
                        try:
                            x, y = map(float, pt.split(","))
                            points.append((x, y))
                        except ValueError:
                            pass
                current_layer["contours"][-1]["points"] = points

    # Post-process to match expected structure for plotting
    # The existing plot_layer expects 'path_ids' and looks up in 'paths_data'
    # We will generate synthetic path IDs for this visualization
    paths_data = {}
    path_counter = 0
    
    for layer in layers:
        layer_path_ids = []
        for contour in layer["contours"]:
            pid = f"path_{path_counter}"
            path_counter += 1
            paths_data[pid] = {
                "points": contour["points"],
                "type": contour["type"]
            }
            layer_path_ids.append(pid)
        layer["path_ids"] = layer_path_ids

    # Compute stats
    total_layers = len(layers)
    total_paths = path_counter
    min_z = float(layers[0]["z"]) if layers else 0.0
    max_z = float(layers[-1]["z"]) if layers else 0.0
    
    return layers, {
        "total_layers": total_layers,
        "total_paths": total_paths,
        "z_range": (min_z, max_z),
        "paths_data": paths_data
    }


class ValidationWidget(QtWidgets.QWidget):
    """
    Widget containing the validation UI: stats, layer slider, and plot.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_content = ""
        self.layers = []
        self.paths_data = {}
        self.stats = {}
        
        self.init_ui()

    def init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        
        # 1. Stats Header
        self.stats_group = QtWidgets.QGroupBox("WAAM DSL Statistics")
        stats_layout = QtWidgets.QHBoxLayout(self.stats_group)
        self.lbl_layer_count = QtWidgets.QLabel("Layers: 0")
        self.lbl_z_range = QtWidgets.QLabel("Z Range: 0.0 - 0.0 mm")
        self.lbl_path_count = QtWidgets.QLabel("Total Paths: 0")
        stats_layout.addWidget(self.lbl_layer_count)
        stats_layout.addWidget(self.lbl_z_range)
        stats_layout.addWidget(self.lbl_path_count)
        layout.addWidget(self.stats_group)

        # 2. Layer Control
        control_layout = QtWidgets.QHBoxLayout()
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.valueChanged.connect(self.on_layer_changed)
        self.lbl_current_layer = QtWidgets.QLabel("Layer: -")
        control_layout.addWidget(QtWidgets.QLabel("Browse Layers:"))
        control_layout.addWidget(self.slider)
        control_layout.addWidget(self.lbl_current_layer)
        layout.addLayout(control_layout)

        # 3. Visualization Area
        self.plot_container = QtWidgets.QWidget()
        self.plot_layout = QtWidgets.QVBoxLayout(self.plot_container)
        
        if HAS_MPL:
            self.figure = Figure(figsize=(5, 4), dpi=100)
            self.canvas = FigureCanvas(self.figure)
            self.plot_layout.addWidget(self.canvas)
            self.ax = self.figure.add_subplot(111)
            self.ax.set_title("Layer Preview")
            self.ax.set_aspect('equal')
            self.ax.grid(True)
        else:
            lbl = QtWidgets.QLabel("Matplotlib not installed. Visualization disabled.")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            self.plot_layout.addWidget(lbl)
            self.canvas = None

        layout.addWidget(self.plot_container, stretch=1)

    def load_dsl(self, content: str):
        """Load and parse WAAM DSL content."""
        self.current_content = content
        self.layers, self.stats = _parse_waam_dsl(content)
        
        if "error" in self.stats:
            self.lbl_layer_count.setText(f"Error: {self.stats['error']}")
            return

        self.paths_data = self.stats.get("paths_data", {})
        
        # Update Stats
        self.lbl_layer_count.setText(f"Layers: {self.stats['total_layers']}")
        z_min, z_max = self.stats.get("z_range", (0, 0))
        self.lbl_z_range.setText(f"Z Range: {z_min:.2f} - {z_max:.2f} mm")
        self.lbl_path_count.setText(f"Total Paths: {self.stats['total_paths']}")

        # Setup Slider
        if self.layers:
            self.slider.setRange(0, len(self.layers) - 1)
            self.slider.setValue(0)
            self.on_layer_changed(0)
        else:
            self.slider.setRange(0, 0)
            self.clear_plot()

    def on_layer_changed(self, index: int):
        if not self.layers or index >= len(self.layers):
            return

        layer = self.layers[index]
        z_height = layer.get("z", 0.0)
        path_ids = layer.get("path_ids", [])
        
        self.lbl_current_layer.setText(f"Layer {index + 1}/{len(self.layers)} (Z={z_height:.2f}mm)")
        
        self.plot_layer(path_ids)

    def plot_layer(self, path_ids: List[str]):
        if not HAS_MPL or not self.canvas:
            return

        self.ax.clear()
        self.ax.set_title(f"Layer View")
        self.ax.set_xlabel("X (mm)")
        self.ax.set_ylabel("Y (mm)")
        self.ax.grid(True)

        if not path_ids:
            self.canvas.draw()
            return

        all_x = []
        all_y = []

        for pid in path_ids:
            path_data = self.paths_data.get(str(pid))
            if not path_data:
                continue
            
            # Points might be "points" (2D) or "points_xyz" (3D)
            pts = path_data.get("points") or path_data.get("points_xyz")
            if not pts:
                continue

            # Extract X, Y
            try:
                xs = [float(p[0]) for p in pts]
                ys = [float(p[1]) for p in pts]
                self.ax.plot(xs, ys, 'b-', linewidth=1, alpha=0.7)
                all_x.extend(xs)
                all_y.extend(ys)
            except (ValueError, IndexError):
                continue

        if all_x and all_y:
            # Add some margin
            margin_x = (max(all_x) - min(all_x)) * 0.1 if len(all_x) > 1 else 1.0
            margin_y = (max(all_y) - min(all_y)) * 0.1 if len(all_y) > 1 else 1.0
            self.ax.set_xlim(min(all_x) - margin_x, max(all_x) + margin_x)
            self.ax.set_ylim(min(all_y) - margin_y, max(all_y) + margin_y)

        self.ax.set_aspect('equal', 'box')
        self.canvas.draw()

    def clear_plot(self):
        if HAS_MPL and self.canvas:
            self.ax.clear()
            self.canvas.draw()


def parse_waam_dsl_file(path) -> Tuple[List[Dict], Dict]:
    """Wrapper to parse a file path directly."""
    try:
        content = Path(path).read_text(encoding="utf-8")
        return _parse_waam_dsl(content)
    except Exception as e:
        return [], {"error": str(e)}


def generate_layer_summary(stats: Dict) -> str:
    """Generate a text summary of the WAAM DSL stats."""
    if "error" in stats:
        return f"Error: {stats['error']}"
    
    z_min, z_max = stats.get("z_range", (0, 0))
    return (
        f"WAAM DSL Summary:\n"
        f"- Total Layers: {stats.get('total_layers', 0)}\n"
        f"- Total Paths: {stats.get('total_paths', 0)}\n"
        f"- Z Range: {z_min:.2f} mm to {z_max:.2f} mm\n"
    )


def generate_contour_ascii_preview(layers: List[Dict]) -> str:
    """
    Generate a simple ASCII preview of the first few layers.
    (Placeholder implementation for now)
    """
    if not layers:
        return "No layers to preview."
    
    # Just show info for the first 3 layers
    preview = ["Layer Preview (First 3):"]
    for i, layer in enumerate(layers[:3]):
        z = layer.get("z", 0)
        paths = layer.get("path_ids", [])
        preview.append(f"Layer {i+1}: Z={z:.2f}mm, {len(paths)} paths")
    
    if len(layers) > 3:
        preview.append(f"... and {len(layers) - 3} more layers.")
        
    return "\n".join(preview)
