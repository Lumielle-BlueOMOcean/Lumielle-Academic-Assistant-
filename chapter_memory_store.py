"""Small SQLite store for source-anchored chapter memories."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DATABASE_NAME = "chapter_memory_v2.sqlite3"
MEMORY_TYPES = frozenset({
    "definition", "terminology", "method", "data_or_number", "finding",
    "conclusion", "limitation", "hypothesis", "cross_chapter_dependency",
})
_SCHEMA_VERSION = 1
_MAX_STATEMENT_CHARS = 1200
_MAX_QUOTE_CHARS = 500


def draft_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def database_path(data_dir) -> str:
    return str(Path(data_dir) / DATABASE_NAME)


def initialize_memory_store(data_dir) -> str:
    """Create the local store and its two business tables if they do not exist."""
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = database_path(root)
    with sqlite3.connect(path, timeout=5) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS chapter_memories (
                chapter_id TEXT PRIMARY KEY,
                draft_hash TEXT NOT NULL,
                narrative_summary TEXT NOT NULL,
                completion_status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_id TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                statement TEXT NOT NULL,
                source_quote TEXT NOT NULL,
                source_start INTEGER NOT NULL,
                source_end INTEGER NOT NULL,
                draft_hash TEXT NOT NULL,
                confirmed_for_writing INTEGER NOT NULL DEFAULT 0,
                verification_status TEXT NOT NULL DEFAULT 'draft_provisional',
                FOREIGN KEY(chapter_id) REFERENCES chapter_memories(chapter_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_memory_items_chapter
                ON memory_items(chapter_id, source_start, id);
            """
        )
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, _SCHEMA_VERSION):
            raise sqlite3.DatabaseError("Unsupported chapter memory schema version")
        connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
    return path


def _validated_items(draft_text, memory, current_hash):
    if not isinstance(memory, dict):
        raise ValueError("A chapter memory must be an object")
    raw_items = memory.get("items", [])
    if not isinstance(raw_items, list):
        raise ValueError("Chapter memory items must be a list")
    validated = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError("Each chapter memory item must be an object")
        memory_type = item.get("memory_type")
        statement = item.get("statement")
        quote = item.get("source_quote")
        start = item.get("source_start")
        end = item.get("source_end")
        if not isinstance(memory_type, str) or memory_type not in MEMORY_TYPES:
            raise ValueError("Unsupported chapter memory type")
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError("Chapter memory statements must be non-empty text")
        if len(statement) > _MAX_STATEMENT_CHARS:
            raise ValueError("Chapter memory statements are too long")
        if not isinstance(quote, str) or not quote:
            raise ValueError("Chapter memory items require a source quote")
        if len(quote) > _MAX_QUOTE_CHARS:
            raise ValueError("Chapter memory source quotes are too long")
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
            raise ValueError("Chapter memory source offsets must be integers")
        if start < 0 or end <= start or end > len(draft_text):
            raise ValueError("Chapter memory source offsets are outside the saved draft")
        if draft_text[start:end] != quote or end - start != len(quote):
            raise ValueError("Chapter memory source quote does not match the saved draft")
        validated.append({
            "memory_type": memory_type,
            "statement": statement.strip(),
            "source_quote": quote,
            "source_start": start,
            "source_end": end,
            "draft_hash": current_hash,
            "confirmed_for_writing": bool(item.get("confirmed_for_writing", False)),
            "verification_status": "draft_provisional",
        })
    return validated


def save_chapter_memory(data_dir, chapter_id, draft_text, memory) -> str:
    """Replace one chapter snapshot and its items in a single SQLite transaction."""
    if not isinstance(chapter_id, str) or not chapter_id:
        raise ValueError("A chapter id is required")
    if not isinstance(draft_text, str) or not draft_text.strip():
        raise ValueError("A non-empty saved draft is required")
    summary = memory.get("narrative_summary", "") if isinstance(memory, dict) else ""
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("A non-empty chapter narrative summary is required")
    completion_status = memory.get("completion_status", "complete")
    if completion_status != "complete":
        raise ValueError("Only complete chapter memories can be saved")
    current_hash = draft_hash(draft_text)
    items = _validated_items(draft_text, memory, current_hash)
    path = initialize_memory_store(data_dir)
    timestamp = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path, timeout=5) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM chapter_memories WHERE chapter_id = ?", (chapter_id,))
        connection.execute(
            "INSERT INTO chapter_memories(chapter_id, draft_hash, narrative_summary, completion_status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (chapter_id, current_hash, summary.strip(), completion_status, timestamp),
        )
        connection.executemany(
            "INSERT INTO memory_items(chapter_id, memory_type, statement, source_quote, source_start, source_end, "
            "draft_hash, confirmed_for_writing, verification_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(
                chapter_id, item["memory_type"], item["statement"], item["source_quote"],
                item["source_start"], item["source_end"], item["draft_hash"],
                int(item["confirmed_for_writing"]), item["verification_status"],
            ) for item in items],
        )
    return current_hash


def read_chapter_memory(data_dir, chapter_id, draft_text, legacy_summary=None):
    """Return an explicit freshness state; only a matching, valid snapshot is current."""
    if not isinstance(draft_text, str) or not draft_text.strip():
        return {"status": "unwritten", "items": [], "narrative_summary": ""}
    path = database_path(data_dir)
    if not os.path.exists(path):
        if legacy_summary:
            return {"status": "legacy_unverified", "items": [], "narrative_summary": "", "legacy_summary": str(legacy_summary)}
        return {"status": "memory_missing", "items": [], "narrative_summary": ""}
    try:
        with sqlite3.connect(path, timeout=5) as connection:
            connection.row_factory = sqlite3.Row
            header = connection.execute(
                "SELECT chapter_id, draft_hash, narrative_summary, completion_status, created_at "
                "FROM chapter_memories WHERE chapter_id = ?", (chapter_id,),
            ).fetchone()
            if header is None:
                if legacy_summary:
                    return {"status": "legacy_unverified", "items": [], "narrative_summary": "", "legacy_summary": str(legacy_summary)}
                return {"status": "memory_missing", "items": [], "narrative_summary": ""}
            rows = connection.execute(
                "SELECT id, memory_type, statement, source_quote, source_start, source_end, draft_hash, "
                "confirmed_for_writing, verification_status FROM memory_items "
                "WHERE chapter_id = ? ORDER BY source_start, id", (chapter_id,),
            ).fetchall()
    except (sqlite3.DatabaseError, OSError, ValueError):
        return {"status": "invalid", "items": [], "narrative_summary": ""}

    result = dict(header)
    result["items"] = [dict(row) for row in rows]
    stored_hash = result.get("draft_hash")
    if (
        result.get("completion_status") != "complete"
        or not isinstance(result.get("narrative_summary"), str)
        or not result["narrative_summary"].strip()
        or not isinstance(stored_hash, str)
        or len(stored_hash) != 64
        or any(character not in "0123456789abcdef" for character in stored_hash)
    ):
        return {**result, "status": "invalid"}
    if result["draft_hash"] != draft_hash(draft_text):
        return {**result, "status": "stale"}
    for item in result["items"]:
        try:
            start, end, quote = item["source_start"], item["source_end"], item["source_quote"]
            statement = item["statement"]
            memory_type = item["memory_type"]
            item_hash = item["draft_hash"]
        except (KeyError, TypeError):
            return {**result, "status": "invalid"}
        if (
            not isinstance(memory_type, str)
            or memory_type not in MEMORY_TYPES
            or not isinstance(statement, str)
            or not statement.strip()
            or not isinstance(quote, str)
            or isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end <= start
            or end > len(draft_text)
            or draft_text[start:end] != quote
            or item_hash != result["draft_hash"]
            or item.get("verification_status") != "draft_provisional"
            or item.get("confirmed_for_writing") not in (0, 1)
        ):
            return {**result, "status": "invalid"}
        item["confirmed_for_writing"] = bool(item["confirmed_for_writing"])
    return {**result, "status": "current"}


def set_memory_item_confirmation(data_dir, item_id, confirmed):
    """Set a writing-consistency preference without changing the item's provenance."""
    path = initialize_memory_store(data_dir)
    with sqlite3.connect(path, timeout=5) as connection:
        cursor = connection.execute(
            "UPDATE memory_items SET confirmed_for_writing = ? WHERE id = ?",
            (int(bool(confirmed)), item_id),
        )
        return cursor.rowcount == 1
