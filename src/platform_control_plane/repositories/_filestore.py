"""Shared atomic JSON file-store helpers for local, file-backed adapters.

Used by both the account-request and decommissioning in-memory adapters. Writes
are atomic (temp file + ``os.replace``) and reads validate a version header;
corrupt or unversioned files raise rather than being silently discarded.

This is for local demonstration, NOT production concurrency — cross-process
writes are last-writer-wins.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from platform_control_plane.exceptions import (
    StoreCorruptError,
    UnsupportedStoreVersionError,
)

STORE_VERSION = 1


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Serialize ``payload`` and replace ``path`` atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_versioned_json(path: Path) -> dict[str, Any]:
    """Read a versioned store file, raising on corruption or unknown version."""
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StoreCorruptError(
            f"Store at {path} is not valid JSON.",
            details={"path": str(path)},
        ) from exc
    if not isinstance(raw, dict) or "_store_version" not in raw:
        raise StoreCorruptError(
            f"Store at {path} is missing its version header.",
            details={"path": str(path)},
        )
    version = raw["_store_version"]
    if version != STORE_VERSION:
        raise UnsupportedStoreVersionError(
            f"Store version {version} is not supported (expected {STORE_VERSION}).",
            details={"found": version, "supported": STORE_VERSION},
        )
    return raw
