from __future__ import annotations

from pathlib import Path

from Waam_tech.experimental.base import ExperimentalPlannerContext
from Waam_tech.experimental import layer_by_layer_scaffold
from Waam_tech.experimental.runtime import loader


def _context(tmp_path: Path) -> ExperimentalPlannerContext:
    return ExperimentalPlannerContext(
        output_dir=tmp_path / "output",
        plan_path=tmp_path / "waam_slice_plan.json",
        features_path=tmp_path / "features.json",
    )


def _workspace_paths(tmp_path: Path):
    paths = loader._paths(tmp_path / "experimental")
    return loader._workspace_paths(paths, "test-part", "abc123")


def test_runtime_manifest_defaults_to_iterating_review_checkpoint(tmp_path):
    manifest = loader._build_manifest(
        _context(tmp_path),
        part_label="Test Part",
        part_key="test-part",
        part_family="bulk_fill",
        part_family_signals=[],
        goal="Try a better path order.",
        workspace_key="abc123",
        workspace_paths=_workspace_paths(tmp_path),
        seed_source="clean_template",
        seed_archive_path="",
        iteration=1,
        current_manifest={},
    )

    assert manifest["status"] == "iterating"
    assert manifest["status_reason"]
    assert manifest["review"]["status"] == "iterating"
    assert manifest["review"]["allowed_statuses"] == ["iterating", "validated", "rejected"]
    assert manifest["review"]["criteria"]
    assert manifest["review"]["next_action"]
    assert manifest["archive_policy"]["keep_last_per_part"] == 5
    assert manifest["archive_policy"]["keep_validated"] is True
    assert manifest["archive_policy"]["keep_promoted_evidence"] is True


def test_runtime_manifest_preserves_validated_review_checkpoint(tmp_path):
    current_manifest = {
        "status": "iterating",
        "review": {
            "status": "validated",
            "reason": "plan_regress and waam_regress matched the accepted reference",
            "evidence": ["Waam_tech/experimental/runtime/archive/test-part/20260408T120000Z"],
            "next_action": "Promote the lesson.",
            "reviewed_at": "2026-04-08T12:05:00Z",
        },
    }

    manifest = loader._build_manifest(
        _context(tmp_path),
        part_label="Test Part",
        part_key="test-part",
        part_family="bulk_fill",
        part_family_signals=[],
        goal="Try a better path order.",
        workspace_key="abc123",
        workspace_paths=_workspace_paths(tmp_path),
        seed_source="clean_template",
        seed_archive_path="",
        iteration=1,
        current_manifest=current_manifest,
    )

    assert manifest["status"] == "validated"
    assert manifest["status_reason"] == "plan_regress and waam_regress matched the accepted reference"
    assert manifest["review"]["evidence"] == [
        "Waam_tech/experimental/runtime/archive/test-part/20260408T120000Z"
    ]
    assert manifest["review"]["next_action"] == "Promote the lesson."


def test_legacy_manifest_review_defaults_to_iterating():
    manifest = loader._with_review_defaults({"status": "archived"})

    assert manifest["status"] == "iterating"
    assert manifest["review"]["status"] == "iterating"
    assert manifest["archive_policy"]["mode"] == "manual_reviewed_retention"


def test_scaffold_lesson_review_block_uses_manifest_review():
    block = layer_by_layer_scaffold._review_lesson_block(
        {
            "review": {
                "status": "rejected",
                "reason": "deposited across a protected void",
                "evidence": ["Waam_tech/experimental/runtime/archive/pieza2/20260408T134126Z"],
                "next_action": "Do not reuse without fixing hole splitting.",
            }
        }
    )

    assert "- status: rejected" in block
    assert "- reason: deposited across a protected void" in block
    assert "`Waam_tech/experimental/runtime/archive/pieza2/20260408T134126Z`" in block
    assert "- next action: Do not reuse without fixing hole splitting." in block
    assert "<iterating" not in block


def test_promoted_strategy_docs_carry_concrete_evidence(repo_root):
    strategies_root = (
        repo_root / "Waam_tech" / "experimental" / "knowledge" / "part-lessons" / "promoted" / "strategies"
    )
    strategy_paths = [
        strategies_root / "solid-section-contour-then-hatch.md",
        strategies_root / "voided-section-hole-safe-fill.md",
    ]

    for path in strategy_paths:
        text = path.read_text(encoding="utf-8")
        evidence = text.split("## Evidence", 1)[1].split("## Related Code", 1)[0]
        assert "Waam_tech/experimental/runtime/archive/" in evidence or "tests/fixtures/golden/" in evidence
        assert "observed notes:" in evidence or "generated layers:" in evidence
        assert "promotion status:" in evidence
        assert "<part>" not in evidence
        assert "<timestamp>" not in evidence
