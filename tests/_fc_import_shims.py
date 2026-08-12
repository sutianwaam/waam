from __future__ import annotations

import importlib
import math
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SLICING_ROOT = _REPO_ROOT / "Waam_tech" / "slicing"


class FakeVector:
    def __init__(self, x: object = 0.0, y: object = 0.0, z: object = 0.0):
        if isinstance(x, FakeVector):
            self.x = float(x.x)
            self.y = float(x.y)
            self.z = float(x.z)
            return
        if all(hasattr(x, attr) for attr in ("x", "y", "z")):
            self.x = float(getattr(x, "x"))
            self.y = float(getattr(x, "y"))
            self.z = float(getattr(x, "z"))
            return
        if y == 0.0 and z == 0.0 and isinstance(x, (list, tuple)) and len(x) >= 3:
            self.x = float(x[0])
            self.y = float(x[1])
            self.z = float(x[2])
            return
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    @property
    def Length(self) -> float:
        return math.sqrt((self.x * self.x) + (self.y * self.y) + (self.z * self.z))


class FakeRotation:
    Angle = 0.0

    def multVec(self, vec: object) -> FakeVector:
        return FakeVector(vec)


class FakePlacement:
    def __init__(self, other: object | None = None):
        self.Base = FakeVector(getattr(other, "Base", FakeVector()))
        self.Rotation = getattr(other, "Rotation", FakeRotation())
        self.Matrix = SimpleNamespace()

    def inverse(self) -> "FakePlacement":
        return self

    def multiply(self, _other: object) -> "FakePlacement":
        return self

    def multVec(self, vec: object) -> FakeVector:
        return FakeVector(vec)

    def toMatrix(self) -> object:
        return self.Matrix


def _fake_package(package_name: str) -> None:
    package = types.ModuleType(package_name)
    package.__path__ = [str(_SLICING_ROOT)]
    sys.modules.setdefault(package_name, package)


def _install_freecad_stubs() -> None:
    freecad = types.ModuleType("FreeCAD")
    freecad.Vector = FakeVector
    freecad.Rotation = FakeRotation
    freecad.Placement = FakePlacement
    freecad.Document = object
    freecad.DocumentObject = object
    freecad.Console = SimpleNamespace(
        PrintWarning=lambda *_args, **_kwargs: None,
        PrintMessage=lambda *_args, **_kwargs: None,
        PrintError=lambda *_args, **_kwargs: None,
    )
    sys.modules.setdefault("FreeCAD", freecad)

    part = types.ModuleType("Part")
    part.makeCompound = lambda shapes: SimpleNamespace(Shapes=list(shapes))
    sys.modules.setdefault("Part", part)

    path_job = types.ModuleType("PathScripts.PathJob")
    path_job.Create = lambda *_args, **_kwargs: SimpleNamespace(
        SetupSheet=SimpleNamespace(Placement=FakePlacement()),
        Stock=None,
    )
    path_scripts = types.ModuleType("PathScripts")
    path_scripts.PathJob = path_job
    sys.modules.setdefault("PathScripts", path_scripts)
    sys.modules.setdefault("PathScripts.PathJob", path_job)


def load_step_to_waam_dsl() -> Any:
    _install_freecad_stubs()
    package_name = "Waam_tech._slicing_for_tests"
    _fake_package(package_name)
    return importlib.import_module(f"{package_name}.step_to_waam_dsl")


def load_slice_plan() -> Any:
    _install_freecad_stubs()
    package_name = "Waam_tech._slice_plan_for_tests"
    _fake_package(package_name)
    return importlib.import_module(f"{package_name}.slice_plan")
