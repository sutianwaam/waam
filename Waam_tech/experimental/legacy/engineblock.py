from __future__ import annotations

from pathlib import Path

from ..base import ExperimentalPlannerContext
from .engineblock_baseline_temp import export_engineblock_experimental_nc


class EngineBlockExperimentalPlanner:
    key = "engineblock"
    label = "EngineBlock experimental NC"
    output_filename = "waam_baseline_engineblock_exp.nc"

    def export_nc(self, context: ExperimentalPlannerContext) -> Path:
        return export_engineblock_experimental_nc(context.output_dir / "nc_files")
