"""Minimal WAAM workbench package.

This simplified build keeps only the core features required for setting a work
coordinate system, extracting geometry features, generating deterministic WAAM
operations, and offering a lightweight assistant stub. The historical pipeline
APIs are retained as stubs so existing scripts receive a clear error instead of
failing with an ImportError.
"""

from __future__ import annotations

__all__ = ["__version__", "generate_plan_and_nc", "run_pipeline_stepwise"]

__version__ = "0.2.0"


def generate_plan_and_nc(*_args, **_kwargs):
    """Stub kept for backwards compatibility with the legacy pipeline entry."""
    raise RuntimeError(
        "The simplified WAAM workbench no longer includes the automated pipeline. "
        "Use the dedicated commands to extract features and plan operations."
    )


def run_pipeline_stepwise(*args, **kwargs):
    """Alias maintained for callers that previously invoked run_pipeline_stepwise."""
    return generate_plan_and_nc(*args, **kwargs)
