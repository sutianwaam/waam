from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol


@dataclass
class ExperimentalPlannerContext:
    output_dir: Path
    fused_operations_path: Path
    features_path: Path
    config_override: Optional[dict] = None


class ExperimentalPlanner(Protocol):
    key: str
    label: str
    output_filename: str

    def export_nc(self, context: ExperimentalPlannerContext) -> Path:
        ...
