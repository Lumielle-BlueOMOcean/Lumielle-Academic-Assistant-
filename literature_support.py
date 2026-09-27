"""Safe cleanup helpers for user-confirmed imported literature source deletion."""

import os
from pathlib import Path


def _delete_source_files(raw_dir, file_paths):
    deleted = 0
    failed = 0
    try:
        raw_root = Path(os.path.abspath(raw_dir))
        raw_root_resolved = raw_root.resolve(strict=True)
        if not raw_root_resolved.is_dir():
            return {"deleted": 0, "failed": len(file_paths)}
    except (OSError, RuntimeError, ValueError):
        return {"deleted": 0, "failed": len(file_paths)}

    seen = set()
    for file_path in file_paths:
        if not isinstance(file_path, (str, os.PathLike)) or not str(file_path).strip():
            continue
        try:
            candidate = Path(file_path)
            if not candidate.is_absolute():
                candidate = raw_root / candidate
            candidate = Path(os.path.abspath(candidate))
            candidate.relative_to(raw_root)
            if candidate.is_symlink():
                failed += 1
                continue
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(raw_root_resolved)
        except (OSError, RuntimeError, ValueError, TypeError):
            failed += 1
            continue

        identity = str(resolved)
        if identity in seen:
            continue
        seen.add(identity)
        if not resolved.exists():
            continue
        if not resolved.is_file():
            failed += 1
            continue
        try:
            resolved.unlink()
            deleted += 1
        except OSError:
            failed += 1

    return {"deleted": deleted, "failed": failed}


def clear_literature_library(literatures, raw_dir, save_records, delete_source_files=False, save_evidence=None):
    """Clear records/evidence, then optionally delete only their safe RAW_DIR files."""
    records = literatures if isinstance(literatures, list) else []
    source_paths = [
        record.get("file_path")
        for record in records
        if isinstance(record, dict) and record.get("file_path")
    ]
    try:
        records_cleared = bool(save_records([]))
    except Exception:
        records_cleared = False

    if not records_cleared:
        return {"records_cleared": False, "deleted": 0, "failed": 0}

    if save_evidence is not None:
        try:
            evidence_cleared = bool(save_evidence({}))
        except Exception:
            evidence_cleared = False
        if not evidence_cleared:
            try:
                save_records(records)
            except Exception:
                pass
            return {"records_cleared": False, "deleted": 0, "failed": 0}

    file_result = (
        _delete_source_files(raw_dir, source_paths)
        if delete_source_files
        else {"deleted": 0, "failed": 0}
    )
    return {
        "records_cleared": True,
        "deleted": file_result["deleted"],
        "failed": file_result["failed"],
    }
