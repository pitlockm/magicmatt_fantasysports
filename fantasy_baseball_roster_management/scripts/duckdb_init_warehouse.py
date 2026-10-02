#!/usr/bin/env python3
"""Initialize a DuckDB warehouse for fantasy baseball data and load the current sources."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


def normalize_name(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def canonicalize_header(value: Any) -> str:
    if pd.isna(value):
        return ""
    cleaned = re.sub(r"[\.\*]", "", str(value).strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def parse_contract_tracker(excel_path: Path) -> pd.DataFrame:
    excel_file = pd.ExcelFile(excel_path)
    skip_sheets = {
        "Intro Page",
        "League History",
        "Example Team Template",
        "Blank Salary Tracker (July 14,",
        "Salary Cap Tracking",
        "Estimated Free Agent Class",
        "Boe - Sorted",
    }
    sheet_names = [s for s in excel_file.sheet_names if s not in skip_sheets]

    frames = []
    for sheet_name in sheet_names:
        raw = pd.read_excel(excel_path, sheet_name=sheet_name, header=None)
        header_row_idx = None
        for idx, row in raw.iterrows():
            values = [canonicalize_header(v).lower() for v in row.tolist()]
            if any("player name" in v for v in values) and any("free agent year" in v for v in values):
                header_row_idx = idx
                break
        if header_row_idx is None:
            continue

        header_row = [canonicalize_header(value) for value in raw.iloc[header_row_idx].tolist()]
        tab_df = raw.iloc[header_row_idx + 1 :].copy()
        tab_df.columns = header_row

        column_lookup = {canonicalize_header(col).casefold(): col for col in tab_df.columns}
        rename_map: dict[str, str] = {}
        for target, aliases in {
            "Player Name": ["player name"],
            "Pos": ["pos"],
            "Free Agent Year": ["free agent year"],
            "Move Type": ["move type"],
            "Player Contract Years": ["player contract years"],
        }.items():
            for alias in aliases:
                if alias in column_lookup:
                    rename_map[column_lookup[alias]] = target
                    break
        if rename_map:
            tab_df = tab_df.rename(columns=rename_map)

        selected = [c for c in ["Player Name", "Pos", "Free Agent Year", "Move Type", "Player Contract Years"] if c in tab_df.columns]
        tab_df = tab_df[selected].copy()

        team_owner = sheet_name
        for _, row in raw.iterrows():
            for col_idx, value in enumerate(row.tolist()):
                text = canonicalize_header(value).lower()
                if text.startswith("team owner") and col_idx + 1 < len(row):
                    candidate_owner = canonicalize_header(row.iloc[col_idx + 1])
                    if candidate_owner:
                        team_owner = candidate_owner
                        break
            if team_owner != sheet_name:
                break

        tab_df["team_owner"] = team_owner
        frames.append(tab_df)

    if not frames:
        return pd.DataFrame(columns=["team_owner", "Player Name", "Pos", "Free Agent Year", "Move Type", "Player Contract Years"])

    result = pd.concat(frames, ignore_index=True)
    result = result.rename(columns={
        "team_owner": "team_owner",
        "Player Name": "player_name",
        "Pos": "position",
        "Free Agent Year": "free_agent_year",
        "Move Type": "move_type",
        "Player Contract Years": "contract_years",
    })
    result["player_name"] = result["player_name"].fillna("").astype(str).str.strip()
    result["move_type"] = result["move_type"].fillna("").astype(str).str.strip()
    result["position"] = result["position"].fillna("").astype(str).str.strip()
    result["team_owner"] = result["team_owner"].fillna("").astype(str).str.strip()
    result["contract_years"] = pd.to_numeric(result["contract_years"], errors="coerce").fillna(1)
    result["free_agent_year"] = pd.to_numeric(result["free_agent_year"], errors="coerce")
    result = result[result["player_name"].ne("")].copy()
    return result


def parse_keith_prospects(raw_path: Path) -> pd.DataFrame:
    df = pd.read_csv(raw_path)
    values = df.iloc[:, 1].fillna("").astype(str).str.strip().tolist()
    values = [v for v in values if v != ""]

    records: list[dict[str, Any]] = []
    current_record: list[str] = []
    for idx, value in enumerate(values):
        if value.isdigit() and idx + 1 < len(values):
            next_value = values[idx + 1]
            if next_value and re.search(r"[A-Za-zÀ-ÿ]", next_value) and not next_value.endswith(":"):
                if current_record:
                    records.append({
                        "rank": int(current_record[0]) if current_record[0].isdigit() else None,
                        "player_name": current_record[1] if len(current_record) > 1 else None,
                        "position": current_record[2] if len(current_record) > 2 else None,
                        "team": current_record[3] if len(current_record) > 3 else None,
                        "age": current_record[4] if len(current_record) > 4 else None,
                        "height": current_record[5] if len(current_record) > 5 else None,
                        "weight": current_record[6] if len(current_record) > 6 else None,
                        "bats": current_record[7] if len(current_record) > 7 else None,
                        "throws": current_record[8] if len(current_record) > 8 else None,
                    })
                current_record = [value]
                continue
        if current_record:
            current_record.append(value)

    if current_record:
        records.append({
            "rank": int(current_record[0]) if current_record[0].isdigit() else None,
            "player_name": current_record[1] if len(current_record) > 1 else None,
            "position": current_record[2] if len(current_record) > 2 else None,
            "team": current_record[3] if len(current_record) > 3 else None,
            "age": current_record[4] if len(current_record) > 4 else None,
            "height": current_record[5] if len(current_record) > 5 else None,
            "weight": current_record[6] if len(current_record) > 6 else None,
            "bats": current_record[7] if len(current_record) > 7 else None,
            "throws": current_record[8] if len(current_record) > 8 else None,
        })

    result = pd.DataFrame(records)
    if result.empty:
        return pd.DataFrame(columns=["rank", "player_name", "position", "team", "age", "height", "weight", "bats", "throws"])
    return result


def create_tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
    CREATE OR REPLACE TABLE stg_contract_tracker (
        source_file_name VARCHAR,
        imported_at TIMESTAMP,
        source_date DATE,
        team_owner VARCHAR,
        player_name VARCHAR,
        position VARCHAR,
        free_agent_year INTEGER,
        move_type VARCHAR,
        contract_years INTEGER
    )
    """)

    conn.execute("""
    CREATE OR REPLACE TABLE stg_fantrax_status (
        source_file_name VARCHAR,
        imported_at TIMESTAMP,
        source_date DATE,
        source_player_id VARCHAR,
        player_name VARCHAR,
        team VARCHAR,
        position VARCHAR,
        rk_ov INTEGER,
        status VARCHAR,
        age INTEGER,
        opponent VARCHAR,
        score DOUBLE,
        ros VARCHAR,
        plus_minus VARCHAR
    )
    """)

    conn.execute("""
    CREATE OR REPLACE TABLE stg_fantrax_stats (
        source_file_name VARCHAR,
        imported_at TIMESTAMP,
        source_date DATE,
        source_player_id VARCHAR,
        player_name VARCHAR,
        team VARCHAR,
        position VARCHAR,
        rk_ov INTEGER,
        age INTEGER,
        stat_type VARCHAR,
        stats_json VARCHAR
    )
    """)

    conn.execute("""
    CREATE OR REPLACE TABLE stg_harry_rankings (
        source_file_name VARCHAR,
        imported_at TIMESTAMP,
        source_date DATE,
        rank INTEGER,
        player_name VARCHAR,
        value INTEGER,
        age DOUBLE,
        positions VARCHAR,
        team VARCHAR,
        level VARCHAR
    )
    """)

    conn.execute("""
    CREATE OR REPLACE TABLE stg_keith_prospects (
        source_file_name VARCHAR,
        imported_at TIMESTAMP,
        source_date DATE,
        rank INTEGER,
        player_name VARCHAR,
        position VARCHAR,
        team VARCHAR,
        age VARCHAR,
        height VARCHAR,
        weight VARCHAR,
        bats VARCHAR,
        throws VARCHAR
    )
    """)

    conn.execute("""
    CREATE OR REPLACE TABLE dim_players (
        player_id INTEGER PRIMARY KEY,
        player_name VARCHAR,
        player_name_norm VARCHAR,
        primary_position VARCHAR,
        primary_team VARCHAR,
        is_prospect BOOLEAN,
        created_at TIMESTAMP
    )
    """)

    conn.execute("""
    CREATE OR REPLACE TABLE player_aliases (
        alias_id INTEGER PRIMARY KEY,
        player_id INTEGER,
        source_system VARCHAR,
        source_player_id VARCHAR,
        source_name VARCHAR,
        source_name_norm VARCHAR,
        is_primary BOOLEAN
    )
    """)

    conn.execute("""
    CREATE OR REPLACE TABLE dim_teams (
        team_id INTEGER PRIMARY KEY,
        team_name VARCHAR,
        league_name VARCHAR
    )
    """)

    conn.execute("""
    CREATE OR REPLACE TABLE dim_positions (
        position_id INTEGER PRIMARY KEY,
        position_code VARCHAR,
        position_group VARCHAR
    )
    """)

    conn.execute("""
    CREATE OR REPLACE TABLE fact_roster_snapshots (
        roster_snapshot_id INTEGER PRIMARY KEY,
        player_id INTEGER,
        snapshot_date DATE,
        roster_type VARCHAR,
        team_owner VARCHAR,
        is_active BOOLEAN,
        source_system VARCHAR
    )
    """)

    conn.execute("""
    CREATE OR REPLACE TABLE fact_contracts (
        contract_id INTEGER PRIMARY KEY,
        player_id INTEGER,
        team_owner VARCHAR,
        free_agent_year INTEGER,
        move_type VARCHAR,
        contract_years INTEGER,
        source_file_date DATE,
        source_system VARCHAR
    )
    """)

    conn.execute("""
    CREATE OR REPLACE TABLE fact_player_status (
        player_status_id INTEGER PRIMARY KEY,
        player_id INTEGER,
        status VARCHAR,
        owner_estimate VARCHAR,
        source_system VARCHAR,
        snapshot_date DATE
    )
    """)

    conn.execute("""
    CREATE OR REPLACE TABLE fact_player_stats (
        player_stats_id INTEGER PRIMARY KEY,
        player_id INTEGER,
        stats_date DATE,
        source_system VARCHAR,
        player_team VARCHAR,
        rk_ov INTEGER,
        age INTEGER,
        stat_type VARCHAR,
        stats_json VARCHAR
    )
    """)

    conn.execute("""
    CREATE OR REPLACE TABLE fact_harry_rankings (
        harry_rank_id INTEGER PRIMARY KEY,
        player_id INTEGER,
        ranking_date DATE,
        overall_rank INTEGER,
        ranking_source VARCHAR,
        raw_rank_text VARCHAR
    )
    """)

    conn.execute("""
    CREATE OR REPLACE TABLE fact_keith_rankings (
        keith_rank_id INTEGER PRIMARY KEY,
        player_id INTEGER,
        ranking_date DATE,
        prospect_rank INTEGER,
        ranking_source VARCHAR,
        raw_rank_text VARCHAR
    )
    """)


def load_data(conn: duckdb.DuckDBPyConnection, raw_dir: Path, source_date: date) -> None:
    imported_at = datetime.now()

    contract_path = raw_dir / "SDA - Major League Contract Tracker 080126.xlsx"
    contract_df = parse_contract_tracker(contract_path)
    contract_df["source_file_name"] = contract_path.name
    contract_df["imported_at"] = imported_at
    contract_df["source_date"] = source_date
    contract_df = contract_df[[
        "source_file_name",
        "imported_at",
        "source_date",
        "team_owner",
        "player_name",
        "position",
        "free_agent_year",
        "move_type",
        "contract_years",
    ]]
    conn.register("contract_df", contract_df)
    conn.execute("""
    INSERT INTO stg_contract_tracker
    SELECT source_file_name, imported_at, source_date, team_owner, player_name, position,
           free_agent_year, move_type, contract_years
    FROM contract_df
    """)

    status_path = raw_dir / "Fantrax-Players-Second Deck Association.csv"
    status_df = pd.read_csv(status_path)
    status_df = status_df.rename(columns={
        "ID": "source_player_id",
        "Player": "player_name",
        "Team": "team",
        "Position": "position",
        "RkOv": "rk_ov",
        "Status": "status",
        "Age": "age",
        "Opponent": "opponent",
        "Score": "score",
        "Ros": "ros",
        "+/-": "plus_minus",
    })
    status_df["source_file_name"] = status_path.name
    status_df["imported_at"] = imported_at
    status_df["source_date"] = source_date
    status_df = status_df[[
        "source_file_name",
        "imported_at",
        "source_date",
        "source_player_id",
        "player_name",
        "team",
        "position",
        "rk_ov",
        "status",
        "age",
        "opponent",
        "score",
        "ros",
        "plus_minus",
    ]]
    conn.register("status_df", status_df)
    conn.execute("""
    INSERT INTO stg_fantrax_status
    SELECT source_file_name, imported_at, source_date, source_player_id, player_name, team, position,
           rk_ov, status, age, opponent, score, ros, plus_minus
    FROM status_df
    """)

    stats_rows: list[dict[str, Any]] = []
    for stats_name, stat_type in [
        ("Fantrax-Players-allhitterstats_080126.csv", "hitter"),
        ("Fantrax-Players-allpitcherstats_080126.csv", "pitcher"),
    ]:
        path = raw_dir / stats_name
        df = pd.read_csv(path)
        for _, row in df.iterrows():
            payload = {k: row[k] for k in df.columns if k not in {"ID", "Player", "Team", "Position", "RkOv", "Age"}}
            stats_rows.append({
                "source_file_name": path.name,
                "imported_at": imported_at,
                "source_date": source_date,
                "source_player_id": row.get("ID"),
                "player_name": row.get("Player"),
                "team": row.get("Team"),
                "position": row.get("Position"),
                "rk_ov": row.get("RkOv"),
                "age": row.get("Age"),
                "stat_type": stat_type,
                "stats_json": json.dumps(payload, default=str),
            })
    stats_df = pd.DataFrame(stats_rows)
    conn.register("stats_df", stats_df)
    conn.execute("""
    INSERT INTO stg_fantrax_stats
    SELECT source_file_name, imported_at, source_date, source_player_id, player_name, team, position,
           rk_ov, age, stat_type, stats_json
    FROM stats_df
    """)

    harry_path = raw_dir / "harryknowsball_daynasty_ranking.csv"
    harry_df = pd.read_csv(harry_path)
    harry_df = harry_df.rename(columns={
        "Rank": "rank",
        "Name": "player_name",
        "Value": "value",
        "Age": "age",
        "Positions": "positions",
        "Team": "team",
        "Level": "level",
    })
    harry_df["source_file_name"] = harry_path.name
    harry_df["imported_at"] = imported_at
    harry_df["source_date"] = source_date
    harry_df = harry_df[["source_file_name", "imported_at", "source_date", "rank", "player_name", "value", "age", "positions", "team", "level"]]
    conn.register("harry_df", harry_df)
    conn.execute("""
    INSERT INTO stg_harry_rankings
    SELECT source_file_name, imported_at, source_date, rank, player_name, value, age, positions, team, level
    FROM harry_df
    """)

    keith_path = raw_dir / "Keith_Top_Prospects.csv"
    keith_df = parse_keith_prospects(keith_path)
    keith_df["source_file_name"] = keith_path.name
    keith_df["imported_at"] = imported_at
    keith_df["source_date"] = source_date
    conn.register("keith_df", keith_df)
    conn.execute("""
    INSERT INTO stg_keith_prospects
    SELECT source_file_name, imported_at, source_date, rank, player_name, position, team, age, height, weight, bats, throws
    FROM keith_df
    """)


def build_warehouse(conn: duckdb.DuckDBPyConnection, source_date: date) -> None:
    conn.execute("""
    INSERT INTO dim_players (player_id, player_name, player_name_norm, primary_position, primary_team, is_prospect, created_at)
    SELECT
        row_number() OVER (ORDER BY player_name_norm) AS player_id,
        player_name,
        player_name_norm,
        NULL,
        NULL,
        FALSE,
        CURRENT_TIMESTAMP
    FROM (
        SELECT DISTINCT player_name AS player_name, normalize_name(player_name) AS player_name_norm
        FROM (
            SELECT player_name FROM stg_contract_tracker
            UNION
            SELECT player_name FROM stg_fantrax_status
            UNION
            SELECT player_name FROM stg_fantrax_stats
            UNION
            SELECT player_name FROM stg_harry_rankings
            UNION
            SELECT player_name FROM stg_keith_prospects
        ) AS combined
    ) AS deduped
    ON CONFLICT(player_id) DO NOTHING
    """)

    # Populate aliases from each source
    conn.execute("""
    INSERT INTO player_aliases (alias_id, player_id, source_system, source_player_id, source_name, source_name_norm, is_primary)
    SELECT
        row_number() OVER (ORDER BY player_id, source_system, source_name_norm) AS alias_id,
        dp.player_id,
        source_system,
        source_player_id,
        source_name,
        source_name_norm,
        TRUE
    FROM (
        SELECT
            normalize_name(player_name) AS source_name_norm,
            player_name AS source_name,
            'stg_contract_tracker' AS source_system,
            NULL AS source_player_id
        FROM stg_contract_tracker
        UNION ALL
        SELECT
            normalize_name(player_name) AS source_name_norm,
            player_name AS source_name,
            'stg_fantrax_status' AS source_system,
            source_player_id
        FROM stg_fantrax_status
        UNION ALL
        SELECT
            normalize_name(player_name) AS source_name_norm,
            player_name AS source_name,
            'stg_fantrax_stats' AS source_system,
            source_player_id
        FROM stg_fantrax_stats
        UNION ALL
        SELECT
            normalize_name(player_name) AS source_name_norm,
            player_name AS source_name,
            'stg_harry_rankings' AS source_system,
            NULL AS source_player_id
        FROM stg_harry_rankings
        UNION ALL
        SELECT
            normalize_name(player_name) AS source_name_norm,
            player_name AS source_name,
            'stg_keith_prospects' AS source_system,
            NULL AS source_player_id
        FROM stg_keith_prospects
    ) AS src
    JOIN dim_players dp ON dp.player_name_norm = src.source_name_norm
    ON CONFLICT(alias_id) DO NOTHING
    """)

    conn.execute("""
    INSERT INTO dim_teams (team_id, team_name, league_name)
    SELECT row_number() OVER (ORDER BY team_name) AS team_id, team_name, 'fantasy_baseball' AS league_name
    FROM (
        SELECT DISTINCT team FROM stg_fantrax_status WHERE team IS NOT NULL AND team != ''
        UNION
        SELECT DISTINCT team FROM stg_harry_rankings WHERE team IS NOT NULL AND team != ''
        UNION
        SELECT DISTINCT team FROM stg_keith_prospects WHERE team IS NOT NULL AND team != ''
    ) AS teams(team_name)
    ON CONFLICT(team_id) DO NOTHING
    """)

    conn.execute("""
    INSERT INTO dim_positions (position_id, position_code, position_group)
    SELECT row_number() OVER (ORDER BY position_code) AS position_id, position_code, position_code AS position_group
    FROM (
        SELECT DISTINCT position AS position_code FROM stg_contract_tracker WHERE position IS NOT NULL AND position != ''
        UNION
        SELECT DISTINCT position AS position_code FROM stg_fantrax_status WHERE position IS NOT NULL AND position != ''
        UNION
        SELECT DISTINCT position AS position_code FROM stg_keith_prospects WHERE position IS NOT NULL AND position != ''
    ) AS positions(position_code)
    ON CONFLICT(position_id) DO NOTHING
    """)

    conn.execute("""
    INSERT INTO fact_contracts (contract_id, player_id, team_owner, free_agent_year, move_type, contract_years, source_file_date, source_system)
    SELECT
        row_number() OVER (ORDER BY dp.player_id, stg.source_date) AS contract_id,
        dp.player_id,
        stg.team_owner,
        stg.free_agent_year,
        stg.move_type,
        stg.contract_years,
        stg.source_date,
        'sda_contract_tracker'
    FROM stg_contract_tracker stg
    JOIN dim_players dp ON dp.player_name_norm = normalize_name(stg.player_name)
    ON CONFLICT(contract_id) DO NOTHING
    """)

    conn.execute("""
    INSERT INTO fact_player_status (player_status_id, player_id, status, owner_estimate, source_system, snapshot_date)
    SELECT
        row_number() OVER (ORDER BY dp.player_id, stg.source_date) AS player_status_id,
        dp.player_id,
        stg.status,
        stg.team,
        'fantrax_status',
        stg.source_date
    FROM stg_fantrax_status stg
    JOIN dim_players dp ON dp.player_name_norm = normalize_name(stg.player_name)
    ON CONFLICT(player_status_id) DO NOTHING
    """)

    conn.execute("""
    INSERT INTO fact_player_stats (player_stats_id, player_id, stats_date, source_system, player_team, rk_ov, age, stat_type, stats_json)
    SELECT
        row_number() OVER (ORDER BY dp.player_id, stg.source_date) AS player_stats_id,
        dp.player_id,
        stg.source_date,
        'fantrax_stats',
        stg.team,
        stg.rk_ov,
        stg.age,
        stg.stat_type,
        stg.stats_json
    FROM stg_fantrax_stats stg
    JOIN dim_players dp ON dp.player_name_norm = normalize_name(stg.player_name)
    ON CONFLICT(player_stats_id) DO NOTHING
    """)

    conn.execute("""
    INSERT INTO fact_harry_rankings (harry_rank_id, player_id, ranking_date, overall_rank, ranking_source, raw_rank_text)
    SELECT
        row_number() OVER (ORDER BY dp.player_id) AS harry_rank_id,
        dp.player_id,
        stg.source_date,
        stg.rank,
        'harryknowsball',
        stg.player_name
    FROM stg_harry_rankings stg
    JOIN dim_players dp ON dp.player_name_norm = normalize_name(stg.player_name)
    ON CONFLICT(harry_rank_id) DO NOTHING
    """)

    conn.execute("""
    INSERT INTO fact_keith_rankings (keith_rank_id, player_id, ranking_date, prospect_rank, ranking_source, raw_rank_text)
    SELECT
        row_number() OVER (ORDER BY dp.player_id) AS keith_rank_id,
        dp.player_id,
        stg.source_date,
        stg.rank,
        'keith_prospects',
        stg.player_name
    FROM stg_keith_prospects stg
    JOIN dim_players dp ON dp.player_name_norm = normalize_name(stg.player_name)
    ON CONFLICT(keith_rank_id) DO NOTHING
    """)

    conn.execute("""
    INSERT INTO fact_roster_snapshots (roster_snapshot_id, player_id, snapshot_date, roster_type, team_owner, is_active, source_system)
    SELECT
        row_number() OVER (ORDER BY dp.player_id) AS roster_snapshot_id,
        dp.player_id,
        stg.source_date,
        'MLB',
        stg.team_owner,
        TRUE,
        'sda_contract_tracker'
    FROM stg_contract_tracker stg
    JOIN dim_players dp ON dp.player_name_norm = normalize_name(stg.player_name)
    ON CONFLICT(roster_snapshot_id) DO NOTHING
    """)


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize DuckDB warehouse for fantasy baseball")
    parser.add_argument("--raw-dir", default="/Users/matthewpitlock/Development/FantasyBaseballAnalytics/data/raw", help="Directory containing raw fantasy baseball data")
    parser.add_argument("--db-path", default="/Users/matthewpitlock/Development/FantasyBaseballAnalytics/database/sda_fantasybaseball.db", help="Path to the DuckDB database file")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir).expanduser()
    db_path = Path(args.db_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(str(db_path))
    conn.create_function("normalize_name", normalize_name)
    try:
        create_tables(conn)
        load_data(conn, raw_dir, date(2026, 8, 2))
        build_warehouse(conn, date(2026, 8, 2))
        conn.commit()
        print(f"Created database at {db_path}")
        for table_name in [
            "stg_contract_tracker",
            "stg_fantrax_status",
            "stg_fantrax_stats",
            "stg_harry_rankings",
            "stg_keith_prospects",
            "dim_players",
            "fact_contracts",
            "fact_player_status",
            "fact_player_stats",
            "fact_harry_rankings",
            "fact_keith_rankings",
        ]:
            count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            print(f"{table_name}: {count}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
