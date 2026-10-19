"""Small atomic JSON persistence helpers shared by both Streamlit apps."""

from __future__ import annotations

import json
import os
import tempfile


def atomic_write_json(path, data):
    """Write JSON beside its target and replace the target only after a full write."""
    target = os.path.abspath(os.fspath(path))
    directory = os.path.dirname(target)
    os.makedirs(directory, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory,
            prefix=f".{os.path.basename(target)}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary_path = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
        return True
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
