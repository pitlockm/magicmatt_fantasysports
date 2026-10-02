"""Portable CSV snapshots and rebuild helpers for the SDA DuckDB database."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

from sda.db.connection import DEFAULT_DATABASE_PATH, open_database
from sda.db.schema import create_schema

TABLE_NAMES = (
    "teams",
    "players",
    "contract_events",
    "roster_snapshots",
    "pending_contracts",
    "season_config",
    "league_history",
)
_SEQUENCES = {
    "contract_event_id_seq": ("contract_events", "event_id"),
    "pending_contract_id_seq": ("pending_contracts", "pending_id"),
}


def export_text_snapshot(
    database_path: Path = DEFAULT_DATABASE_PATH,
    *,
    snapshot_date: date | None = None,
    snapshot_root: Path | None = None,
) -> Path:
    """Export every base table as a headered CSV under a date directory."""
    snapshot_day = snapshot_date or date.today()
    root = snapshot_root or Path(database_path).parent / "snapshots"
    output_dir = root / snapshot_day.isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)

    with open_database(database_path, read_only=True) as connection:
        for table_name in TABLE_NAMES:
            csv_path = output_dir / f"{table_name}.csv"
            temporary_path = csv_path.with_suffix(".csv.tmp")
            sql_path = _sql_string(temporary_path)
            connection.execute(
                f"COPY {table_name} TO '{sql_path}' "
                "(FORMAT CSV, HEADER TRUE, NULLSTR '\\N')"
            )
            temporary_path.replace(csv_path)
    return output_dir


def restore_text_snapshot(
    snapshot_dir: Path,
    database_path: Path,
) -> Path:
    """Rebuild a fresh DuckDB file from all table CSVs in a snapshot directory."""
    snapshot_dir = Path(snapshot_dir)
    database_path = Path(database_path)
    if database_path.exists() and database_path.stat().st_size > 0:
        raise FileExistsError(f"Refusing to overwrite an existing database: {database_path}")
    database_path.parent.mkdir(parents=True, exist_ok=True)

    with open_database(database_path) as connection:
        create_schema(connection)
        connection.execute("BEGIN TRANSACTION")
        for table_name in TABLE_NAMES:
            csv_path = snapshot_dir / f"{table_name}.csv"
            if not csv_path.is_file():
                raise FileNotFoundError(f"Snapshot is missing required table file: {csv_path}")
            connection.execute(
                f"COPY {table_name} FROM '{_sql_string(csv_path)}' "
                "(FORMAT CSV, HEADER TRUE, NULLSTR '\\N')"
            )
        connection.execute("COMMIT")
        _advance_sequences(connection)
    return database_path


def _advance_sequences(connection: duckdb.DuckDBPyConnection) -> None:
    for sequence_name, (table_name, id_column) in _SEQUENCES.items():
        maximum_id = connection.execute(
            f"SELECT COALESCE(MAX({id_column}), 0) FROM {table_name}"
        ).fetchone()[0]
        for _ in range(int(maximum_id)):
            connection.execute(f"SELECT nextval('{sequence_name}')")


def _sql_string(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")