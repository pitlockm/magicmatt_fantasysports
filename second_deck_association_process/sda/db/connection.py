"""DuckDB connection helpers and single-writer lock."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "sda.duckdb"


class DatabaseBusyError(RuntimeError):
    """Raised when another SDA process currently holds the writer lock."""


@contextmanager
def writer_lock(database_path: Path = DEFAULT_DATABASE_PATH) -> Iterator[None]:
    """Acquire a non-blocking advisory lock beside the database file."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = database_path.parent / ".db.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DatabaseBusyError(
                f"Another SDA writer holds {lock_path}; retry after it finishes"
            ) from error
        yield
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


@contextmanager
def open_database(
    database_path: Path = DEFAULT_DATABASE_PATH,
    *,
    read_only: bool = False,
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open a DuckDB connection and lock it for the duration of writes."""
    database_path = Path(database_path)
    if read_only:
        connection = duckdb.connect(str(database_path), read_only=True)
        try:
            yield connection
        finally:
            connection.close()
        return

    with writer_lock(database_path):
        connection = duckdb.connect(str(database_path))
        try:
            yield connection
        finally:
            connection.close()