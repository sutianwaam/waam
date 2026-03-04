"""Convert OBJ meshes into condensed text blocks for LLM consumption."""

from __future__ import annotations

import argparse
import base64
import math
import struct
import textwrap
import zlib
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


def _quantize(value: float, decimals: int) -> float:
    factor = 10**decimals
    return round(value * factor) / factor


def _format_float(value: float, decimals: int) -> str:
    formatted = f"{value:.{decimals}f}"
    return formatted.rstrip("0").rstrip(".")


def _parse_vertex(parts: Sequence[str], decimals: int) -> Tuple[float, float, float]:
    if len(parts) < 4:
        raise ValueError("Vertex line must provide x, y, z components.")
    x, y, z = (float(parts[1]), float(parts[2]), float(parts[3]))
    return (
        _quantize(x, decimals),
        _quantize(y, decimals),
        _quantize(z, decimals),
    )


def _parse_normal(parts: Sequence[str]) -> Tuple[float, float, float]:
    if len(parts) < 4:
        raise ValueError("Normal line must provide x, y, z components.")
    nx, ny, nz = (float(parts[1]), float(parts[2]), float(parts[3]))
    return (_quantize(nx, 4), _quantize(ny, 4), _quantize(nz, 4))


def _parse_face(parts: Sequence[str]) -> Tuple[int, int, int]:
    if len(parts) < 4:
        raise ValueError("Face line must specify at least 3 indices.")
    indices: List[int] = []
    for token in parts[1:]:
        head = token.split("/")[0]
        if head:
            indices.append(int(head))
    if len(indices) < 3:
        raise ValueError("Face must list three vertices.")
    return indices[0], indices[1], indices[2]


def _parse_face_with_normals(parts: Sequence[str]) -> Tuple[Tuple[int, int, int], Optional[Tuple[int, int, int]]]:
    if len(parts) < 4:
        raise ValueError("Face line must specify at least 3 indices.")

    vertex_indices: List[int] = []
    normal_indices: List[int] = []
    for token in parts[1:]:
        head, *rest = token.split("/")
        if head:
            vertex_indices.append(int(head))
        if len(rest) >= 2 and rest[1]:
            normal_indices.append(int(rest[1]))

    if len(vertex_indices) < 3:
        raise ValueError("Face must list three vertices.")

    normal_tuple: Optional[Tuple[int, int, int]] = None
    if len(normal_indices) >= 3:
        normal_tuple = (normal_indices[0], normal_indices[1], normal_indices[2])

    return (vertex_indices[0], vertex_indices[1], vertex_indices[2]), normal_tuple


def _sub(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(u: Tuple[float, float, float], v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )


def _normalize(vec: Tuple[float, float, float]) -> Tuple[float, float, float]:
    length = math.sqrt(vec[0] ** 2 + vec[1] ** 2 + vec[2] ** 2)
    if length == 0:
        return (0.0, 0.0, 0.0)
    return (vec[0] / length, vec[1] / length, vec[2] / length)


def _compute_vertex_normals(
    vertices: List[Tuple[float, float, float]],
    faces: List[Tuple[int, int, int]],
) -> List[Tuple[float, float, float]]:
    accum = [[0.0, 0.0, 0.0] for _ in vertices]
    for a, b, c in faces:
        try:
            v0 = vertices[a - 1]
            v1 = vertices[b - 1]
            v2 = vertices[c - 1]
        except IndexError:
            continue
        n = _cross(_sub(v1, v0), _sub(v2, v0))
        for idx in (a - 1, b - 1, c - 1):
            for axis in range(3):
                accum[idx][axis] += n[axis]

    return [_normalize(tuple(values)) for values in accum]


def _wrap_base64(value: str, width: int) -> List[str]:
    return textwrap.wrap(value, width=width) if width > 0 else [value]


def _encode_block(label: str, payload: bytes, *, compress: bool, width: int) -> List[str]:
    compressed = zlib.compress(payload, level=9) if compress else payload
    b64 = base64.b64encode(compressed).decode("ascii")
    lines = [f"{label}_B64 {'zlib' if compress else 'plain'} {len(payload)} {len(compressed)}"]
    lines.extend("  " + segment for segment in _wrap_base64(b64, width))
    return lines


def _quantize_vertices_uint16(
    vertices: Sequence[Tuple[float, float, float]],
    bbox_min: Sequence[float],
    bbox_max: Sequence[float],
    bits: int,
) -> List[Tuple[int, int, int]]:
    if bits != 16:
        raise ValueError("Only 16-bit quantization is currently supported.")

    max_val = (1 << bits) - 1
    scale = []
    for idx in range(3):
        span = bbox_max[idx] - bbox_min[idx]
        scale.append(0.0 if span == 0 else max_val / span)

    quantized: List[Tuple[int, int, int]] = []
    for x, y, z in vertices:
        qx = int(round((x - bbox_min[0]) * scale[0])) if scale[0] else 0
        qy = int(round((y - bbox_min[1]) * scale[1])) if scale[1] else 0
        qz = int(round((z - bbox_min[2]) * scale[2])) if scale[2] else 0
        quantized.append(
            (
                max(0, min(max_val, qx)),
                max(0, min(max_val, qy)),
                max(0, min(max_val, qz)),
            )
        )
    return quantized


def _quantize_normals_int16(normals: Sequence[Tuple[float, float, float]]) -> List[Tuple[int, int, int]]:
    scale = 32767.0
    quantized: List[Tuple[int, int, int]] = []
    for nx, ny, nz in normals:
        quantized.append(
            (
                max(-32768, min(32767, int(round(nx * scale)))),
                max(-32768, min(32767, int(round(ny * scale)))),
                max(-32768, min(32767, int(round(nz * scale)))),
            )
        )
    return quantized


def _format_mesh_packed(
    vertices: Iterable[Tuple[float, float, float]],
    faces: Iterable[Tuple[int, int, int]],
    *,
    normals: Optional[Sequence[Tuple[float, float, float]]],
    normal_indices: Optional[Sequence[Tuple[int, int, int]]],
    compress: bool,
    quant_bits: int = 16,
    wrap_width: int = 120,
) -> str:
    vertex_list = list(vertices)
    face_list = list(faces)
    bbox_min = [min(coord[idx] for coord in vertex_list) for idx in range(3)]
    bbox_max = [max(coord[idx] for coord in vertex_list) for idx in range(3)]

    quantized_vertices = _quantize_vertices_uint16(vertex_list, bbox_min, bbox_max, quant_bits)
    flat_vertices: List[int] = [component for triple in quantized_vertices for component in triple]
    vertex_payload = struct.pack(">" + "H" * len(flat_vertices), *flat_vertices)

    face_struct = struct.Struct(">HHH") if len(vertex_list) <= 65535 else struct.Struct(">III")
    face_payload = bytearray()
    for a, b, c in face_list:
        face_payload.extend(face_struct.pack(a - 1, b - 1, c - 1))

    normal_payload: Optional[bytes] = None
    normal_count = 0
    if normals:
        quantized_normals = _quantize_normals_int16(normals)
        flat_normals: List[int] = [component for triple in quantized_normals for component in triple]
        normal_payload = struct.pack(">" + "h" * len(flat_normals), *flat_normals)
        normal_count = len(quantized_normals)

    include_normal_indices = (
        normal_indices is not None
        and normal_payload is not None
        and len(normal_indices) == len(face_list)
        and all(idx is not None for idx in normal_indices)
    )
    normal_index_payload: Optional[bytes] = None
    if include_normal_indices:
        normal_index_struct = struct.Struct(">HHH") if normal_count <= 65535 else struct.Struct(">III")
        buffer = bytearray()
        for trio in normal_indices:  # type: ignore[union-attr]
            assert trio is not None
            buffer.extend(normal_index_struct.pack(trio[0] - 1, trio[1] - 1, trio[2] - 1))
        normal_index_payload = bytes(buffer)

    lines: List[str] = []
    lines.append("WAAM_MESH_FOR_LLM")
    lines.append("FORMAT_VERSION 3.0")
    lines.append("ENCODING PACKED_BASE64")
    lines.append(f"VERTEX_COUNT {len(vertex_list)}")
    lines.append(f"FACE_COUNT {len(face_list)}")
    lines.append(f"VERTEX_BITS {quant_bits}")
    lines.append(f"FACE_INDEX_BITS {16 if len(vertex_list) <= 65535 else 32}")
    lines.append(f"COMPRESSION {'zlib' if compress else 'none'}")
    lines.append(f"BBOX_MIN {bbox_min[0]:.6g} {bbox_min[1]:.6g} {bbox_min[2]:.6g}")
    lines.append(f"BBOX_MAX {bbox_max[0]:.6g} {bbox_max[1]:.6g} {bbox_max[2]:.6g}")
    lines.extend(_encode_block("VERTICES", vertex_payload, compress=compress, width=wrap_width))

    if normal_payload:
        lines.append(f"NORMAL_COUNT {normal_count}")
        lines.extend(_encode_block("NORMALS", normal_payload, compress=compress, width=wrap_width))
    else:
        lines.append("NORMAL_COUNT 0")

    if include_normal_indices and normal_index_payload:
        lines.append("NORMAL_INDICES 1")
        lines.extend(_encode_block("NORMAL_INDEX", normal_index_payload, compress=compress, width=wrap_width))
    else:
        lines.append("NORMAL_INDICES 0")

    lines.extend(_encode_block("FACES", bytes(face_payload), compress=compress, width=wrap_width))
    return "\n".join(lines) + "\n"


def _format_mesh(
    vertices: Iterable[Tuple[float, float, float]],
    faces: Iterable[Tuple[int, int, int]],
    *,
    decimals: int,
    compact: bool,
    include_normals: bool,
) -> str:
    vertex_list = list(vertices)
    face_list = list(faces)
    bbox_min = [min(coord[idx] for coord in vertex_list) for idx in range(3)]
    bbox_max = [max(coord[idx] for coord in vertex_list) for idx in range(3)]
    normals = _compute_vertex_normals(vertex_list, face_list) if include_normals else None

    lines: List[str] = []
    lines.append("WAAM_MESH_FOR_LLM")
    lines.append("FORMAT_VERSION 2.0")
    lines.append(f"TOTAL_VERTICES {len(vertex_list)}")
    lines.append(f"TOTAL_FACES {len(face_list)}")
    lines.append(f"BBOX_MIN {bbox_min[0]:g} {bbox_min[1]:g} {bbox_min[2]:g}")
    lines.append(f"BBOX_MAX {bbox_max[0]:g} {bbox_max[1]:g} {bbox_max[2]:g}")

    lines.append("VERTICES_BEGIN")
    if compact:
        for idx in range(0, len(vertex_list), 5):
            chunk = vertex_list[idx : idx + 5]
            lines.append(
                "  "
                + " ".join(
                    f"{_format_float(x, decimals)},{_format_float(y, decimals)},{_format_float(z, decimals)}"
                    for x, y, z in chunk
                )
            )
    else:
        for x, y, z in vertex_list:
            lines.append(f"  {_format_float(x, decimals)} {_format_float(y, decimals)} {_format_float(z, decimals)}")
    lines.append("VERTICES_END")

    if normals:
        lines.append("NORMALS_BEGIN")
        if compact:
            for idx in range(0, len(normals), 5):
                chunk = normals[idx : idx + 5]
                lines.append("  " + " ".join(f"{nx:.3f},{ny:.3f},{nz:.3f}" for nx, ny, nz in chunk))
        else:
            for nx, ny, nz in normals:
                lines.append(f"  {nx:.3f} {ny:.3f} {nz:.3f}")
        lines.append("NORMALS_END")

    lines.append("FACES_BEGIN")
    if compact:
        for idx in range(0, len(face_list), 10):
            chunk = face_list[idx : idx + 10]
            lines.append("  " + " ".join(f"{a},{b},{c}" for a, b, c in chunk))
    else:
        for a, b, c in face_list:
            lines.append(f"  {a} {b} {c}")
    lines.append("FACES_END")

    return "\n".join(lines) + "\n"


def convert_obj_to_llm_text(
    obj_path: Path | str,
    *,
    out_path: Path | str | None = None,
    decimals: int = 2,
    compact: bool = False,
    include_normals: bool = False,
    mode: str = "text",
    compress: bool = True,
    quant_bits: int = 16,
    wrap_width: int = 120,
) -> Path:
    """
    Convert an OBJ mesh into a compact text summary for LLM ingestion.

    Args:
        obj_path: OBJ file path.
        out_path: Optional destination path (defaults to `*.mesh_for_llm.txt`).
        decimals: Coordinate precision for vertices.
        compact: Emit multiple vertices/faces per line to save tokens.
        include_normals: Compute vertex normals to highlight local surface cues.
        mode: "text" (existing human-readable) or "packed" (binary-to-base64, smaller).
        compress: Apply zlib before base64 in packed mode to shrink tokens further.
        quant_bits: Vertex quantization bits when packing (currently only 16 is supported).
        wrap_width: Maximum characters per base64 line (packed mode).
    """

    source = Path(obj_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"OBJ file not found: {source}")

    vertices: List[Tuple[float, float, float]] = []
    normals_from_obj: List[Tuple[float, float, float]] = []
    faces: List[Tuple[int, int, int]] = []
    face_normal_indices: List[Optional[Tuple[int, int, int]]] = []

    with source.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            parts = line.strip().split()
            if not parts:
                continue
            prefix = parts[0]
            if prefix == "v":
                try:
                    vertices.append(_parse_vertex(parts, decimals))
                except ValueError:
                    continue
            elif prefix == "vn":
                try:
                    normals_from_obj.append(_parse_normal(parts))
                except ValueError:
                    continue
            elif prefix == "f":
                try:
                    face, normal_indices = _parse_face_with_normals(parts)
                    faces.append(face)
                    face_normal_indices.append(normal_indices)
                except ValueError:
                    continue

    if not vertices:
        raise ValueError(f"No vertices parsed from OBJ: {source}")
    if not faces:
        raise ValueError(f"No faces parsed from OBJ: {source}")

    normal_source: Optional[List[Tuple[float, float, float]]] = None
    if include_normals:
        if normals_from_obj:
            normal_source = normals_from_obj
        else:
            normal_source = _compute_vertex_normals(vertices, faces)

    normal_indices_for_output: Optional[List[Tuple[int, int, int]]] = None
    if normal_source and face_normal_indices and all(item is not None for item in face_normal_indices):
        normal_indices_for_output = [idx for idx in face_normal_indices if idx is not None]

    if mode == "packed":
        text = _format_mesh_packed(
            vertices,
            faces,
            normals=normal_source,
            normal_indices=normal_indices_for_output,
            compress=compress,
            quant_bits=quant_bits,
            wrap_width=wrap_width,
        )
    else:
        text = _format_mesh(vertices, faces, decimals=decimals, compact=compact, include_normals=include_normals)

    target = Path(out_path).expanduser().resolve() if out_path else source.with_suffix(".mesh_for_llm.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert OBJ meshes into LLM-friendly text.")
    parser.add_argument("input", type=Path, help="Input OBJ path.")
    parser.add_argument("-o", "--output", type=Path, help="Output file path")
    parser.add_argument("-d", "--decimals", type=int, default=2, help="Coordinate precision (default: 2)")
    parser.add_argument("--compact", action="store_true", help="Compact mode (multiple entries per line)")
    parser.add_argument("--normals", action="store_true", help="Include vertex normals")
    parser.add_argument("--mode", choices=("text", "packed"), default="text", help="Output encoding (default: text)")
    parser.add_argument("--no-compress", action="store_true", help="Disable zlib compression in packed mode")
    parser.add_argument(
        "--quant-bits",
        type=int,
        default=16,
        help="Quantization bits for packed coordinates (currently only 16 is supported)",
    )
    parser.add_argument("--wrap", type=int, default=120, help="Base64 wrap width for packed payloads")
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = _build_cli()
    args = parser.parse_args(argv)
    convert_obj_to_llm_text(
        args.input,
        out_path=args.output,
        decimals=args.decimals,
        compact=args.compact,
        include_normals=args.normals,
        mode=args.mode,
        compress=not args.no_compress,
        quant_bits=args.quant_bits,
        wrap_width=args.wrap,
    )
    return 0


__all__ = ["convert_obj_to_llm_text"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
