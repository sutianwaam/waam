from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol, Sequence


@dataclass
class ExperimentalPlannerContext:
    output_dir: Path
    plan_path: Path
    features_path: Path
    config_override: Optional[dict] = None
    config: Optional[dict] = None
    config_path: Optional[Path] = None
    doc: Any = None
    objects: Optional[Sequence[Any]] = None
    part_key: str = ""
    part_label: str = ""
    part_family: str = ""
    part_family_signals: Optional[list[str]] = None
    step_path: Optional[Path] = None
    baseline_nc_path: Optional[Path] = None
    run_id: str = ""

    @property
    def fused_operations_path(self) -> Path:
        """Backward-compatible alias for older experimental helpers."""
        return self.plan_path


class ExperimentalPlanner(Protocol):
    key: str
    label: str
    output_filename: str

    def export_nc(self, context: ExperimentalPlannerContext) -> Path:
        ...
