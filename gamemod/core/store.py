"""SQLite store for translations, so runs can pause and resume without repeating work."""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from gamemod.adapters.base import SourceLine

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    hint TEXT NOT NULL DEFAULT '',
    target TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    first_order INTEGER NOT NULL,
    UNIQUE(source, hint)
);
CREATE INDEX IF NOT EXISTS items_status ON items(status, first_order);
CREATE TABLE IF NOT EXISTS lines(
    table_name TEXT NOT NULL,
    key TEXT NOT NULL,
    item_id INTEGER NOT NULL REFERENCES items(id),
    line_order INTEGER NOT NULL,
    prefix TEXT NOT NULL DEFAULT '',
    suffix TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(table_name, key)
);
CREATE TABLE IF NOT EXISTS usage(
    ts REAL NOT NULL,
    model TEXT NOT NULL,
    items INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    thought_tokens INTEGER NOT NULL
);
"""

PENDING = "pending"
RETRY = "retry"  # rejected once, waiting to be redone with the others at the end of the run
DONE = "done"
REVIEW = "review"  # usable, but a quality check wants a person to look
FAILED = "failed"  # unusable, the game falls back to the original line
EDITED = "edited"  # changed by hand, never overwritten
FIXED = "fixed"  # from the glossary
SKIP = "skip"  # nothing to translate, such as "..."
USABLE = (DONE, REVIEW, EDITED, FIXED, SKIP)

_HAS_LETTER = re.compile(r"[^\W\d_]")


@dataclass(frozen=True)
class Item:
    id: int
    source: str
    hint: str
    target: str | None
    status: str
    note: str
    first_order: int


class Store:
    def __init__(self, path: str | Path):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def sync(self, lines: Iterable[SourceLine], fixed: Mapping[str, str] | None = None) -> None:
        """Record every game line. Existing translations are kept."""
        fixed = fixed or {}
        with self._lock, self._db:
            cur = self._db.cursor()
            known = {
                (source, hint): item_id
                for item_id, source, hint in cur.execute("SELECT id, source, hint FROM items")
            }
            for line in lines:
                pair = (line.text, line.hint)
                item_id = known.get(pair)
                if item_id is None:
                    if line.text in fixed:
                        status, target = FIXED, fixed[line.text]
                    elif not _HAS_LETTER.search(line.text):
                        status, target = SKIP, line.text
                    else:
                        status, target = PENDING, None
                    cur.execute(
                        "INSERT INTO items(source, hint, target, status, first_order) VALUES(?,?,?,?,?)",
                        (line.text, line.hint, target, status, line.order),
                    )
                    item_id = cur.lastrowid
                    known[pair] = item_id
                cur.execute(
                    "INSERT OR REPLACE INTO lines(table_name, key, item_id, line_order, prefix, suffix) "
                    "VALUES(?,?,?,?,?,?)",
                    (line.table, line.key, item_id, line.order, line.prefix, line.suffix),
                )
            for source, target in fixed.items():
                cur.execute(
                    "UPDATE items SET target=?, status=? WHERE source=? AND status != ?",
                    (target, FIXED, source, EDITED),
                )

    def counts(self) -> Counter:
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) FROM items GROUP BY status").fetchall()
        return Counter(dict(rows))

    def pending(
        self,
        limit: int,
        order_range: tuple[int, int] | None = None,
        status: str = PENDING,
    ) -> list[Item]:
        query = (
            "SELECT id, source, hint, target, status, note, first_order FROM items WHERE status = ?"
        )
        params: list = [status]
        if order_range:
            query += " AND first_order BETWEEN ? AND ?"
            params += list(order_range)
        query += " ORDER BY first_order LIMIT ?"
        params.append(limit)
        with self._lock:
            return [Item(*row) for row in self._db.execute(query, params)]

    def items_by_id(self, ids: Sequence[int]) -> dict[int, Item]:
        found: dict[int, Item] = {}
        with self._lock:
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                rows = self._db.execute(
                    "SELECT id, source, hint, target, status, note, first_order FROM items "
                    f"WHERE id IN ({','.join('?' * len(chunk))})",
                    chunk,
                )
                found.update((row[0], Item(*row)) for row in rows)
        return found

    def count_pending(self, order_range: tuple[int, int] | None = None) -> int:
        """Lines with no final translation yet, including those waiting for a retry."""
        query = "SELECT COUNT(*) FROM items WHERE status IN (?, ?)"
        params: list = [PENDING, RETRY]
        if order_range:
            query += " AND first_order BETWEEN ? AND ?"
            params += list(order_range)
        with self._lock:
            return self._db.execute(query, params).fetchone()[0]

    def pack_lines(self) -> dict[str, dict[str, str]]:
        """Translated text by line, for a subtitle pack. Tags come from the game, so none here."""
        out: dict[str, dict[str, str]] = {}
        with self._lock:
            rows = self._db.execute(
                "SELECT l.table_name, l.key, i.target FROM lines l JOIN items i ON i.id = l.item_id "
                "WHERE i.status IN (?, ?, ?) ORDER BY l.table_name, l.line_order",
                (DONE, REVIEW, EDITED),
            )
            for table, key, target in rows:
                out.setdefault(table, {})[key] = target
        return out

    def import_pack(
        self,
        lines: Mapping[str, Mapping[str, str]],
        is_usable: Callable[[str, str], bool],
        model: str = "pack",
    ) -> int:
        """Take translations from a subtitle pack. Returns how many lines changed.

        A newer pack replaces an older one, but never a person's edits or a line translated
        on this PC.
        """
        changed = 0
        seen: set[int] = set()
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT l.table_name, l.key, i.id, i.source, i.target, i.status, i.model "
                "FROM lines l JOIN items i ON i.id = l.item_id"
            ).fetchall()
            for table, key, item_id, source, target, status, item_model in rows:
                new = lines.get(table, {}).get(key)
                if new is None or item_id in seen:
                    continue
                seen.add(item_id)
                replaceable = status in (PENDING, RETRY, FAILED) or (
                    item_model == model and status == DONE
                )
                if replaceable and new != target and is_usable(source, new):
                    self._db.execute(
                        "UPDATE items SET target=?, status=?, model=?, note='' WHERE id=?",
                        (new, DONE, model, item_id),
                    )
                    changed += 1
        return changed

    def fill_duplicates(self, can_share: Callable[[str], bool]) -> int:
        """Copy a finished translation to unfinished lines with the same English."""
        filled = 0
        with self._lock, self._db:
            finished: dict[str, tuple[str, str]] = {}
            rows = self._db.execute(
                "SELECT source, target, model FROM items WHERE status IN (?, ?, ?)", (DONE, REVIEW, EDITED)
            )
            for source, target, model in rows.fetchall():
                if can_share(source):
                    finished.setdefault(source, (target, model))
            for source, (target, model) in finished.items():
                filled += self._db.execute(
                    "UPDATE items SET target=?, status=?, model=?, note='' WHERE source=? AND status IN (?, ?)",
                    (target, DONE, model, source, PENDING, RETRY),
                ).rowcount
        return filled

    def context_before(self, order: int, count: int) -> list[tuple[str, str]]:
        marks = ",".join("?" * len(USABLE))
        with self._lock:
            rows = self._db.execute(
                f"SELECT source, target FROM items WHERE first_order < ? AND status IN ({marks}) "
                "ORDER BY first_order DESC LIMIT ?",
                (order, *USABLE, count),
            ).fetchall()
        return rows[::-1]

    def save(self, item_id: int, target: str | None, status: str, note: str = "", model: str = "") -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE items SET target=?, status=?, note=?, model=? WHERE id=? AND status != ?",
                (target, status, note, model, item_id, EDITED),
            )

    def edit(self, item_id: int, target: str) -> None:
        """A person's edit. It is never overwritten by later runs."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE items SET target=?, status=?, note='' WHERE id=?", (target, EDITED, item_id)
            )

    def retry_failed(self) -> int:
        with self._lock, self._db:
            return self._db.execute(
                "UPDATE items SET status=?, note='' WHERE status=?", (PENDING, FAILED)
            ).rowcount

    def reset_range(self, start: int, end: int) -> int:
        """Queue machine translations in a scene range to be redone. Hand edits are kept."""
        with self._lock, self._db:
            return self._db.execute(
                "UPDATE items SET status=?, target=NULL, note='' "
                "WHERE first_order BETWEEN ? AND ? AND status IN (?,?,?,?)",
                (PENDING, start, end, DONE, REVIEW, FAILED, RETRY),
            ).rowcount

    def line_targets(self) -> dict[str, dict[str, str]]:
        """Finished text for every line that has a usable translation."""
        marks = ",".join("?" * len(USABLE))
        out: dict[str, dict[str, str]] = {}
        with self._lock:
            rows = self._db.execute(
                f"SELECT l.table_name, l.key, l.prefix, i.target, l.suffix FROM lines l "
                f"JOIN items i ON i.id = l.item_id WHERE i.status IN ({marks})",
                USABLE,
            )
            for table, key, prefix, target, suffix in rows:
                out.setdefault(table, {})[key] = f"{prefix}{target}{suffix}"
        return out

    def search(
        self,
        text: str = "",
        statuses: Sequence[str] | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Item]:
        query = "SELECT id, source, hint, target, status, note, first_order FROM items WHERE 1=1"
        params: list = []
        if text:
            query += " AND (source LIKE ? OR target LIKE ?)"
            params += [f"%{text}%", f"%{text}%"]
        if statuses:
            query += f" AND status IN ({','.join('?' * len(statuses))})"
            params += list(statuses)
        query += " ORDER BY first_order LIMIT ? OFFSET ?"
        params += [limit, offset]
        with self._lock:
            return [Item(*row) for row in self._db.execute(query, params)]

    def log_usage(self, model: str, items: int, prompt: int, output: int, thoughts: int) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO usage VALUES(?,?,?,?,?,?)",
                (time.time(), model, items, prompt, output, thoughts),
            )

    def usage_totals(self) -> dict[str, int]:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(items),0), COALESCE(SUM(prompt_tokens),0), "
                "COALESCE(SUM(output_tokens),0), COALESCE(SUM(thought_tokens),0) FROM usage"
            ).fetchone()
        return dict(zip(("requests", "items", "prompt_tokens", "output_tokens", "thought_tokens"), row))
