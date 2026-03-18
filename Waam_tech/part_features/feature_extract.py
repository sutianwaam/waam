import json
import math
import FreeCAD as App
from ..core.wcs_resolver import resolve_wcs
from ..ui.wcs_marker import get_deposition_start
from ..analysis.mesh_features import analyze_mesh_features

print("Loading feature_extract.py...")

def face_kind(face):
    s = face.Surface
    n = s.__class__.__name__
    if "Plane" in n:
        return "plane"
    if "Cylinder" in n:
        return "cylinder"
    if any(k in n for k in ("BSpline", "Bezier", "Nurbs")):
        return "nurbs"
    return "other"


def _to_vector(candidate):
    if candidate is None:
        return None
    if isinstance(candidate, App.Vector):
        return App.Vector(candidate)
    for attr_set in (("x", "y", "z"), ("X", "Y", "Z")):
        if all(hasattr(candidate, attr) for attr in attr_set):
            return App.Vector(*(float(getattr(candidate, attr)) for attr in attr_set))
    if isinstance(candidate, (list, tuple)) and len(candidate) == 3:
        return App.Vector(*[float(v) for v in candidate])
    return None


def _normalized(vec, tol=1e-9):
    if vec is None:
        return None
    v = App.Vector(vec)
    if v.Length <= tol:
        return None
    v.normalize()
    return v


def _vec_list(vec: App.Vector):
    return [float(vec.x), float(vec.y), float(vec.z)]


def _polygon_area(points):
    if len(points) < 3:
        return 0.0
    acc = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]):
        acc += x0 * y1 - x1 * y0
    return abs(acc) * 0.5


def _circumcircle_radius(p0, p1, p2):
    x0, y0 = p0
    x1, y1 = p1
    x2, y2 = p2
    a = math.hypot(x1 - x0, y1 - y0)
    b = math.hypot(x2 - x1, y2 - y1)
    c = math.hypot(x0 - x2, y0 - y2)
    s = (a + b + c) * 0.5
    area_sq = s * (s - a) * (s - b) * (s - c)
    if area_sq <= 1e-9:
        return None
    area = math.sqrt(area_sq)
    denom = 4.0 * area
    if denom == 0.0:
        return None
    return (a * b * c) / denom


def _estimate_min_radius(polyline):
    if len(polyline) < 3:
        return None
    min_radius = None
    pts = list(polyline)
    for a, b, c in zip(pts, pts[1:], pts[2:] + pts[:2]):
        r = _circumcircle_radius(a, b, c)
        if r is None:
            continue
        if min_radius is None or r < min_radius:
            min_radius = r
    return min_radius


def _prepare_metadata(doc, work, wcs_name, force_start_to_wcs_origin):
    resolution = resolve_wcs(doc)
    wcs = resolution.placement
    
    # Helpers needed locally
    def to_local(p: App.Vector):
        return wcs.inverse().multVec(p)

    def world_to_wcs_dir(v: App.Vector):
        # Rotate direction into WCS (ignore translation)
        return wcs.Rotation.inverted().multVec(v)

    shape = work.Shape

    # 1) WCS bbox
    xs, ys, zs = [], [], []
    for v in shape.Vertexes:
        q = to_local(v.Point)
        xs.append(q.x)
        ys.append(q.y)
        zs.append(q.z)
    
    bbox_wcs = {
        "xmin": min(xs),
        "xmax": max(xs),
        "ymin": min(ys),
        "ymax": max(ys),
        "zmin": min(zs),
        "zmax": max(zs),
    }

    start_point = None
    start_source = None
    if force_start_to_wcs_origin:
        start_point = [0.0, 0.0, 0.0]
        start_source = "wcs_origin_forced"
    else:
        stored_start = get_deposition_start(doc)
        if stored_start is not None:
            try:
                start_local = to_local(stored_start)
                start_point = [float(start_local.x), float(start_local.y), float(start_local.z)]
                start_source = "wcs_marker"
            except Exception:
                start_point = None
                start_source = None

    data = {
        "metadata": {
            "units": "mm",
            "wcs": {
                "name": wcs_name,
                "origin": [0, 0, 0],
                "axes": {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]},
                "placement": {
                    "origin_world": _vec_list(wcs.Base),
                    "axes_world": {
                        "x": _vec_list(wcs.Rotation.multVec(App.Vector(1, 0, 0))),
                        "y": _vec_list(wcs.Rotation.multVec(App.Vector(0, 1, 0))),
                        "z": _vec_list(wcs.Rotation.multVec(App.Vector(0, 0, 1))),
                    },
                },
            },
            "work_obj": work.Name,
            "bbox": bbox_wcs,
        },
        "features": [],
    }
    if start_point is not None:
        data["metadata"]["deposition_start"] = {
            "point_wcs": start_point,
            "source": start_source or "wcs_marker",
        }
        
    return data, to_local, world_to_wcs_dir


def _extract_holes(shape, owner_name, to_local, world_to_wcs_dir, mesh_analysis):
    features = []
    # --- HOLES / CYLINDERS (general axis in WCS) ---
    for idx, face in enumerate(shape.Faces, start=1):
        if face_kind(face) != "cylinder":
            continue

        # --- NUEVA LÓGICA: Detección de Agujero vs Pilar (Boss) ---
        try:
            # Evaluar un punto y la normal en el centro paramétrico de la cara
            u_min, u_max, v_min, v_max = face.ParameterRange
            u_mid, v_mid = (u_min + u_max) / 2.0, (v_min + v_max) / 2.0
            
            pt_on_surf = face.valueAt(u_mid, v_mid)
            normal_world = face.normalAt(u_mid, v_mid)
            
            # Obtener el centro del eje del cilindro (Position) o el centro de masa como respaldo
            axis_base = getattr(face.Surface, "Position", face.CenterOfMass)
            
            # Vector radial desde el eje a la superficie
            radial_vec = pt_on_surf.sub(axis_base)
            
            # En un sólido B-rep, la normal siempre apunta hacia afuera del material.
            # Si el producto punto es positivo, la normal apunta alejándose del eje.
            # Esto significa que el material está DENTRO del cilindro -> Es un Pilar (Boss).
            if normal_world.dot(radial_vec) > 0:
                continue  # Lo ignoramos, no es un agujero a taladrar
        except Exception:
            # Si falla la evaluación paramétrica (geometría corrupta), continuamos
            pass
        # ----------------------------------------------------------

        # Get cylinder axis in WORLD, then rotate it to WCS
        ax = getattr(face.Surface, "Axis", None)
        direction = None
        if ax is not None:
            d = getattr(ax, "Direction", None)
            direction = d() if callable(d) else (d if d is not None else ax)
        axis_w = _to_vector(direction)
        axis_wcs = _normalized(world_to_wcs_dir(axis_w)) if axis_w else None
        if axis_wcs is None:
            continue

        # Compute WCS center/top/bottom by transforming world bbox corners
        bb = face.BoundBox
        cxw = 0.5 * (bb.XMin + bb.XMax)
        cyw = 0.5 * (bb.YMin + bb.YMax)
        ztopw, zbotw = bb.ZMax, bb.ZMin
        p_top = to_local(App.Vector(cxw, cyw, ztopw))
        p_bot = to_local(App.Vector(cxw, cyw, zbotw))
        dz = abs(p_top.z - p_bot.z)
        try:
            r = float(face.Surface.Radius())
        except Exception:
            r = float(face.Surface.Radius)

        axis_vec = [axis_wcs.x, axis_wcs.y, axis_wcs.z]
        vertical_like = abs(abs(axis_wcs.z) - 1.0) <= 1e-3
        body_bb = shape.BoundBox
        body_zmin = to_local(App.Vector(body_bb.XMin, body_bb.YMin, body_bb.ZMin)).z
        body_zmax = to_local(App.Vector(body_bb.XMax, body_bb.YMax, body_bb.ZMax)).z
        z_top = max(p_top.z, p_bot.z)
        z_bot = min(p_top.z, p_bot.z)
        tol = 1e-3
        is_through = vertical_like and (abs(z_top - body_zmax) <= tol or abs(z_bot - body_zmin) <= tol)
        hole_type = "through_hole" if is_through else "cylindrical_hole"

        risk_tags = []
        if mesh_analysis:
            try:
                if vertical_like and mesh_analysis.get("overhangs", {}).get("samples"):
                    xmin = min(p_top.x, p_bot.x) - r
                    xmax = max(p_top.x, p_bot.x) + r
                    ymin = min(p_top.y, p_bot.y) - r
                    ymax = max(p_top.y, p_bot.y) + r
                    for samp in mesh_analysis["overhangs"]["samples"]:
                        sx, sy, sz = samp["center"]
                        sp = to_local(App.Vector(sx, sy, sz))
                        if xmin <= sp.x <= xmax and ymin <= sp.y <= ymax:
                            risk_tags.append("overhang_neighbor")
                            break
                if mesh_analysis.get("thin_walls", {}).get("samples"):
                    risk_tags.append("thin_wall_neighbor")
            except Exception:
                pass

        features.append(
            {
                "id": f"hole_{len(features)}", # Note: index is local here, readjust in caller or use a counter
                "type": hole_type,
                "owner": owner_name,
                "face": f"Face{idx}",
                "center": [p_top.x, p_top.y, p_top.z],
                "diameter": 2.0 * r,
                "depth": dz,
                "z_top": z_top,
                "axis_wcs": axis_vec,
                "vertical_like": vertical_like,
                "risk_tags": list(set(risk_tags)) if risk_tags else None,
            }
        )
    return features


def _extract_pockets(shape, owner_name, to_local, world_to_wcs_dir, mesh_analysis):
    features = []
    
    # --- NUEVA LÓGICA: Mapa de adyacencia de Aristas a Caras ---
    # Para saber si una cara plana es el fondo de un pocket o la cima de la pieza
    edge_to_faces = {}
    for f in shape.Faces:
        for e in f.Edges:
            # Usamos el CenterOfMass.Length como una aproximación de hash rápida
            key = round(e.CenterOfMass.Length, 4) 
            edge_to_faces.setdefault(key, []).append(f)
    # ---------------------------------------------------------

    # --- PLANAR POCKET FLOORS (horizontal in WCS) ---
    for idx, face in enumerate(shape.Faces, start=1):
        if face_kind(face) != "plane":
            continue
        # face normal in WORLD -> rotate into WCS
        n_world = face.normalAt(0.5, 0.5)
        n_wcs = _normalized(world_to_wcs_dir(n_world))
        if n_wcs is None or abs(n_wcs.z) <= 0.95:  # not horizontal in WCS
            continue

        verts_local = [to_local(v.Point) for v in face.Vertexes]
        z_vals = [p.z for p in verts_local]
        if max(z_vals) - min(z_vals) > 1e-5:
            # Face is planar in OCC terms but not coplanar in the current WCS
            # (likely due to modelling tolerances or warped patches). Skip it
            # so Path Pocket does not raise "shape is not planar".
            continue

        # --- NUEVA LÓGICA: Comprobación de paredes adyacentes ---
        face_z = max(z_vals)
        is_true_pocket = False
        
        outer_wire = getattr(face, "OuterWire", None)
        if outer_wire is not None:
            for edge in outer_wire.Edges:
                key = round(edge.CenterOfMass.Length, 4)
                adjacent_faces = edge_to_faces.get(key, [])
                
                for adj_f in adjacent_faces:
                    if adj_f.isSame(face):
                        continue
                    
                    # Convertimos el bounding box de la cara adyacente a WCS local
                    adj_bb = adj_f.BoundBox
                    adj_zmax = to_local(App.Vector(adj_bb.XMax, adj_bb.YMax, adj_bb.ZMax)).z
                    
                    # Si la cara adyacente sube por encima de nuestro suelo, ES un pocket
                    if adj_zmax > face_z + 0.1: # 0.1mm de tolerancia
                        is_true_pocket = True
                        break
                
                if is_true_pocket:
                    break
        
        if not is_true_pocket:
            continue # Es una cara superior (isla o techo), no una cavidad. La saltamos.
        # ---------------------------------------------------------

        bb = face.BoundBox
        pmin = to_local(App.Vector(bb.XMin, bb.YMin, bb.ZMin))
        pmax = to_local(App.Vector(bb.XMax, bb.YMax, bb.ZMax))

        outer_loop = []
        inner_loops = []
        try:
            if outer_wire is not None:
                pts = [to_local(p) for p in outer_wire.discretize(Number=128)]
                outer_loop = [[float(p.x), float(p.y)] for p in pts]
            inners = getattr(face, "InnerWires", []) or []
            for iw in inners:
                pts = [to_local(p) for p in iw.discretize(Number=64)]
                inner_loops.append([[float(p.x), float(p.y)] for p in pts])
        except Exception:
            outer_loop = []
            inner_loops = []

        area_mm2 = 0.0
        min_radius_mm = None
        if outer_loop:
            area_mm2 = _polygon_area(outer_loop)
            min_radius_mm = _estimate_min_radius(outer_loop)

        risk_tags = []
        if mesh_analysis:
            try:
                xmin = min(pmin.x, pmax.x)
                xmax = max(pmin.x, pmax.x)
                ymin = min(pmin.y, pmax.y)
                ymax = max(pmin.y, pmax.y)
                if mesh_analysis.get("overhangs", {}).get("samples"):
                    for samp in mesh_analysis["overhangs"]["samples"]:
                        sx, sy, sz = samp["center"]
                        sp = to_local(App.Vector(sx, sy, sz))
                        if xmin <= sp.x <= xmax and ymin <= sp.y <= ymax:
                            risk_tags.append("overhang_region")
                            break
                if mesh_analysis.get("thin_walls", {}).get("samples"):
                    risk_tags.append("thin_wall_neighbor")
            except Exception:
                pass

        features.append(
            {
                "id": f"pocket_{len(features)}", # Local index
                "type": "planar_pocket_floor",
                "owner": owner_name,
                "face": f"Face{idx}",
                "z": 0.5 * (pmin.z + pmax.z),  # WCS Z of floor
                "bbox": [pmin.x, pmax.x, pmin.y, pmax.y],
                "outer_loop": outer_loop or None,
                "inner_loops": inner_loops or None,
                "area_mm2": area_mm2,
                "min_radius_mm": min_radius_mm,
                "risk_tags": list(set(risk_tags)) if risk_tags else None,
            }
        )
    return features


def _extract_freeform(shape, owner_name, to_local):
    features = []
    # --- FREEFORM (coarse region bbox in WCS) ---
    ffs = [f for f in shape.Faces if face_kind(f) in ("nurbs", "other")]
    if ffs:
        acc = None
        for f in ffs:
            bb = f.BoundBox
            pmin = to_local(App.Vector(bb.XMin, bb.YMin, bb.ZMin))
            pmax = to_local(App.Vector(bb.XMax, bb.YMax, bb.ZMax))
            cur = [pmin.x, pmax.x, pmin.y, pmax.y, pmin.z, pmax.z]
            if acc is None:
                acc = cur
            else:
                acc = [
                    min(acc[0], cur[0]),
                    max(acc[1], cur[1]),
                    min(acc[2], cur[2]),
                    max(acc[3], cur[3]),
                    min(acc[4], cur[4]),
                    max(acc[5], cur[5]),
                ]
        features.append({"id": "freeform_0", "type": "freeform_area", "owner": owner_name, "bbox": acc})
    return features


def extract_features_wcs(doc, wcs_name="G54", out_path=None, *, force_start_to_wcs_origin=False):
    # 0) Get solid + ensure Job exists
    work = next((o for o in doc.Objects if hasattr(o, "Shape") and o.Shape and o.Shape.Solids), None)
    if work is None:
        raise RuntimeError("No solid found")

    data, to_local, world_to_wcs_dir = _prepare_metadata(doc, work, wcs_name, force_start_to_wcs_origin)
    shape = work.Shape

    # Precompute mesh-based analysis for risk tagging + downstream planning
    mesh_analysis = None
    try:
        mesh_analysis = analyze_mesh_features(shape, build_axis=App.Vector(0, 0, 1))
    except Exception:
        mesh_analysis = None
    else:
        data["metadata"]["mesh_analysis"] = mesh_analysis
    
    # --- HOLES / CYLINDERS ---
    hole_features = _extract_holes(shape, work.Name, to_local, world_to_wcs_dir, mesh_analysis)
    # Fix IDs to be global
    for i, f in enumerate(hole_features):
        f["id"] = f"hole_{len(data['features']) + i}"
    data["features"].extend(hole_features)

    # --- PLANAR POCKET FLOORS ---
    pocket_features = _extract_pockets(shape, work.Name, to_local, world_to_wcs_dir, mesh_analysis)
    for i, f in enumerate(pocket_features):
        f["id"] = f"pocket_{len(data['features']) + i}"
    data["features"].extend(pocket_features)

    # --- FREEFORM ---
    freeform_features = _extract_freeform(shape, work.Name, to_local)
    for i, f in enumerate(freeform_features):
        f["id"] = f"freeform_{i}" # Should only be 1 currently, but generic loop is fine
    data["features"].extend(freeform_features)

    if out_path:
        with open(out_path, "w") as f:
            json.dump(data, f, indent=2)
    return data
