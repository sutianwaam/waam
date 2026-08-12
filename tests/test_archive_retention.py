from __future__ import annotations

import json
from pathlib import Path

from Waam_tech.experimental.runtime import loader


def _write_manifest(snapshot: Path, status: str, *, nested_review: bool = True) -> None:
    snapshot.mkdir(parents=True, exist_ok=True)
    payload = {"part_key": "pieza2"}
    if nested_review:
        payload["review"] = {"status": status}
    else:
        payload["status"] = status
    (snapshot / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")


def test_prune_archive_snapshots_dry_run_keeps_newest_and_validated(tmp_path):
    paths = loader._paths(tmp_path / "experimental")
    part_root = paths["archive_root"] / "pieza2"

    (part_root / "20260401T000000Z").mkdir(parents=True)
    (part_root / "20260402T000000Z").mkdir(parents=True)
    (part_root / "20260402T000000Z" / "manifest.json").write_text("{bad json", encoding="utf-8")
    _write_manifest(part_root / "20260403T000000Z", "validated")
    _write_manifest(part_root / "20260404T000000Z", "rejected")
    _write_manifest(part_root / "20260405T000000Z", "iterating")
    _write_manifest(part_root / "20260406T000000Z", "iterating")
    _write_manifest(part_root / "20260407T000000Z", "iterating")
    _write_manifest(part_root / "20260408T000000Z", "iterating")
    _write_manifest(part_root / "20260409T000000Z", "iterating")

    candidates = loader._prune_archive_snapshots(paths, part_key="pieza2")

    assert [path.name for path in candidates] == [
        "20260401T000000Z",
        "20260402T000000Z",
        "20260404T000000Z",
    ]
    assert all(path.exists() for path in candidates)
    assert (part_root / "20260403T000000Z").exists()
    assert (part_root / "20260405T000000Z").exists()


def test_prune_archive_snapshots_deletes_candidates_only_when_requested(tmp_path):
    paths = loader._paths(tmp_path / "experimental")
    part_root = paths["archive_root"] / "pieza2"

    for day in range(1, 8):
        _write_manifest(part_root / f"2026040{day}T000000Z", "iterating", nested_review=False)
    _write_manifest(part_root / "20260402T000000Z", "validated")

    candidates = loader._prune_archive_snapshots(paths, part_key="pieza2", dry_run=False)

    assert [path.name for path in candidates] == ["20260401T000000Z"]
    assert not (part_root / "20260401T000000Z").exists()
    assert (part_root / "20260402T000000Z").exists()
    assert (part_root / "20260403T000000Z").exists()
    assert (part_root / "20260407T000000Z").exists()


def test_prune_archive_snapshots_ignores_missing_part_archive(tmp_path):
    paths = loader._paths(tmp_path / "experimental")

    assert loader._prune_archive_snapshots(paths, part_key="missing") == []
    assert loader._prune_archive_snapshots(paths, part_key="") == []
