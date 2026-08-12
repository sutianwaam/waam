"""Small deterministic hashing helpers shared by pipeline checkpoints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path | str) -> str:
    return sha256_bytes(Path(path).read_bytes())


def sha256_json(obj: Any) -> str:
    """Hash an arbitrary JSON-serializable object with stable key ordering."""
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(canonical.encode("utf-8"))


def short_hash(full_hash: str, length: int = 16) -> str:
    return full_hash[:length]


__all__ = ["sha256_bytes", "sha256_file", "sha256_json", "short_hash"]
