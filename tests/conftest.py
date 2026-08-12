from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "golden"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def golden_dir() -> Path:
    return GOLDEN_DIR


@pytest.fixture(scope="session")
def golden_plan_path() -> Path:
    return GOLDEN_DIR / "waam_slice_plan.json"


@pytest.fixture(scope="session")
def golden_baseline_nc_path() -> Path:
    return GOLDEN_DIR / "waam_baseline.nc"


@pytest.fixture(scope="session")
def golden_experimental_nc_path() -> Path:
    return GOLDEN_DIR / "waam_baseline_experimental.nc"
