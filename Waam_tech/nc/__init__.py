"""NC helpers for the WAAM workbench."""

from __future__ import annotations

from .fuse_ops_mesh import FusionError, fuse_operations_with_mesh
from .nc_generator import NcGenerationError, generate_baseline_nc
from .waam_dsl_nc import (
    WaamContour,
    WaamDslDocument,
    WaamDslParseError,
    WaamLayer,
    generate_nc_from_dsl,
    parse_waam_dsl,
)

__all__ = [
    "FusionError",
    "fuse_operations_with_mesh",
    "NcGenerationError",
    "generate_baseline_nc",
    "WaamContour",
    "WaamDslDocument",
    "WaamDslParseError",
    "WaamLayer",
    "generate_nc_from_dsl",
    "parse_waam_dsl",
]
