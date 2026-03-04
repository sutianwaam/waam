"""Convert torch coverage grids into a compact RLE JSON for LLM input."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


AGENT_README_RLE = (
    "This JSON stores 3D voxel grids encoded with run-length encoding (RLE). "
    "Grid keys (for example, material_grid or torch_grid) hold objects with "
    "encoding='rle', order='xyz', dims=[nx, ny, nz], total_voxels, and runs. "
    "The runs list is [value, count] pairs that expand into a flat list of "
    "voxel values in x-major order (iterate x, then y, then z). "
    "To decode, expand runs into total_voxels values and reshape to [nx][ny][nz]. "
    "Values are 0 or 1 (empty/material or empty/torch)."
)


def _rle_encode_grid(grid: Sequence[Sequence[Sequence[int]]]) -> Tuple[List[List[int]], Tuple[int, int, int]]:
    if not grid:
        raise ValueError("Grid is empty.")

    runs: List[List[int]] = []
    last_value: Optional[int] = None
    count = 0

    nx = len(grid)
    ny: Optional[int] = None
    nz: Optional[int] = None

    for x_plane in grid:
        if ny is None:
            ny = len(x_plane)
            if ny == 0:
                raise ValueError("Grid has empty Y dimension.")
        elif len(x_plane) != ny:
            raise ValueError("Grid Y dimension is not consistent.")

        for row in x_plane:
            if nz is None:
                nz = len(row)
                if nz == 0:
                    raise ValueError("Grid has empty Z dimension.")
            elif len(row) != nz:
                raise ValueError("Grid Z dimension is not consistent.")

            for value in row:
                val = int(value)
                if last_value is None:
                    last_value = val
                    count = 1
                elif val == last_value:
                    count += 1
                else:
                    runs.append([last_value, count])
                    last_value = val
                    count = 1

    if last_value is None:
        raise ValueError("Grid contains no values.")

    runs.append([last_value, count])
    return runs, (nx, int(ny), int(nz))


def _dims_from_metadata(meta: object) -> Optional[Tuple[int, int, int]]:
    if not isinstance(meta, dict):
        return None
    dims = meta.get("dims")
    if not isinstance(dims, list) or len(dims) != 3:
        return None
    try:
        return (int(dims[0]), int(dims[1]), int(dims[2]))
    except (TypeError, ValueError):
        return None


def _rle_block(grid: Sequence[Sequence[Sequence[int]]], order: str, dims_hint: Optional[Tuple[int, int, int]]) -> dict:
    runs, dims = _rle_encode_grid(grid)
    if dims_hint and dims_hint != dims:
        raise ValueError(f"Grid dims {dims} do not match metadata dims {dims_hint}.")

    total = dims[0] * dims[1] * dims[2]
    total_runs = sum(run[1] for run in runs)
    if total_runs != total:
        raise ValueError(f"RLE run total {total_runs} does not match grid size {total}.")

    return {
        "encoding": "rle",
        "order": order,
        "dims": [dims[0], dims[1], dims[2]],
        "total_voxels": total,
        "runs": runs,
    }


def convert_torch_coverage_to_rle(
    source: Path | str,
    *,
    output_path: Optional[Path | str] = None,
    grids: Sequence[str] = ("material_grid", "torch_grid"),
    keep_dense: bool = False,
    indent: Optional[int] = None,
) -> Path:
    src = Path(source)
    if not src.is_file():
        raise FileNotFoundError(f"Coverage JSON not found: {src}")

    with src.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    order = "xyz"
    if isinstance(data.get("metadata"), dict):
        order = str(data["metadata"].get("order") or order)

    dims_hint = _dims_from_metadata(data.get("metadata"))
    output = dict(data) if keep_dense else {k: v for k, v in data.items() if k not in grids}

    output["agent_readme"] = AGENT_README_RLE

    for grid_key in grids:
        if grid_key not in data:
            continue
        block = _rle_block(data[grid_key], order, dims_hint)
        if keep_dense:
            output[f"{grid_key}_dense"] = data[grid_key]
        output[grid_key] = block

    if not any(key in output for key in grids):
        raise ValueError("No matching grids were found in the source JSON.")

    target = Path(output_path).expanduser().resolve() if output_path else src.with_suffix(".rle.json")
    target.parent.mkdir(parents=True, exist_ok=True)

    dump_kwargs = {"ensure_ascii": True}
    if indent is None:
        dump_kwargs["separators"] = (",", ":")
    else:
        dump_kwargs["indent"] = indent
    with target.open("w", encoding="utf-8") as handle:
        json.dump(output, handle, **dump_kwargs)

    return target


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert torch coverage JSON grids into RLE for LLM consumption."
    )
    parser.add_argument("input", type=Path, help="Input torch coverage JSON path.")
    parser.add_argument("-o", "--output", type=Path, help="Output JSON path.")
    parser.add_argument(
        "--grids",
        default="material_grid,torch_grid",
        help="Comma-separated grid keys to encode (default: material_grid,torch_grid).",
    )
    parser.add_argument(
        "--keep-dense",
        action="store_true",
        help="Preserve the original dense grids under *_dense keys.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=None,
        help="Indent level for pretty JSON (omit for compact output).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_cli()
    args = parser.parse_args(argv)

    grids = [item.strip() for item in args.grids.split(",") if item.strip()]
    if not grids:
        raise SystemExit("No grid keys supplied; use --grids to specify at least one.")

    convert_torch_coverage_to_rle(
        args.input,
        output_path=args.output,
        grids=grids,
        keep_dense=args.keep_dense,
        indent=args.indent,
    )
    return 0


__all__ = ["convert_torch_coverage_to_rle"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
