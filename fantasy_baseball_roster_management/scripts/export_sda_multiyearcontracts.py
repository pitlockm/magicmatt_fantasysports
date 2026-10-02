#!/usr/bin/env python3
"""Export a cleaned SDA multiyear-contract roster CSV to the staging directory."""

from __future__ import annotations

import argparse
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd


DEFAULT_RAW_DATA_DIR = Path("/Users/matthewpitlock/Development/FantasyBaseballAnalytics/data/raw")
DEFAULT_STAGING_DIR = Path("/Users/matthewpitlock/Development/FantasyBaseballAnalytics/data/staging")
DATE_SUFFIX = datetime.now().strftime("%m%d%y")
DEFAULT_OUTPUT_NAME = f"sda_multiyearcontracts_{DATE_SUFFIX}.csv"

SKIP_SHEETS = {
    "Intro Page",
    "League History",
    "Example Team Template",
    "Blank Salary Tracker (July 14,",
    "Salary Cap Tracking",
    "Estimated Free Agent Class",
    "Boe - Sorted",
}


def canonicalize_header(value: object) -> str:
    if pd.isna(value):
        return ""
    cleaned = re.sub(r"[\.\*]", "", str(value).strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def normalize_name(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_owner_tabs(excel_path: Path) -> pd.DataFrame:
    excel_file = pd.ExcelFile(excel_path)
    sheet_names = [s for s in excel_file.sheet_names if s not in SKIP_SHEETS]

    frames: list[pd.DataFrame] = []
    for sheet_name in sheet_names:
        raw = pd.read_excel(excel_path, sheet_name=sheet_name, header=None)

        header_row_idx: Optional[int] = None
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

        selected_columns = ["Player Name", "Pos", "Free Agent Year", "Move Type", "Player Contract Years"]
        selected_columns = [c for c in selected_columns if c in tab_df.columns]
        tab_df = tab_df[selected_columns].copy()

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

        tab_df["Player Name"] = tab_df["Player Name"].fillna("").astype(str).str.strip()
        tab_df["Move Type"] = tab_df["Move Type"].fillna("").astype(str).str.strip()
        tab_df["Player Contract Years"] = pd.to_numeric(tab_df["Player Contract Years"], errors="coerce")

        mask = (
            tab_df["Player Name"].ne("")
            & tab_df["Move Type"].ne("DFA")
            & tab_df["Move Type"].ne("")
            & tab_df["Player Contract Years"].fillna(1).gt(0)
        )
        tab_df = tab_df.loc[mask].copy()
        tab_df["Player Contract Years"] = tab_df["Player Contract Years"].fillna(1)
        tab_df["team_owner"] = team_owner
        frames.append(tab_df)

    if not frames:
        return pd.DataFrame(columns=["team_owner", "Player Name", "Pos", "Free Agent Year", "Move Type", "Player Contract Years"])

    owner_tab_df = pd.concat(frames, ignore_index=True)
    return owner_tab_df[["team_owner", "Player Name", "Pos", "Free Agent Year", "Move Type", "Player Contract Years"]]


def load_stats_frames(raw_dir: Path) -> pd.DataFrame:
    stats_frames: list[pd.DataFrame] = []

    hitter_path = raw_dir / "Fantrax-Players-allhitterstats_080126.csv"
    pitcher_path = raw_dir / "Fantrax-Players-allpitcherstats_080126.csv"

    if hitter_path.exists():
        stats_frames.append(pd.read_csv(hitter_path))
    if pitcher_path.exists():
        stats_frames.append(pd.read_csv(pitcher_path))

    if not stats_frames:
        return pd.DataFrame(columns=["Player", "Team", "RkOv", "Age"])

    combined = pd.concat(stats_frames, ignore_index=True)
    return combined[[col for col in ["Player", "Team", "RkOv", "Age"] if col in combined.columns]]


def resolve_raw_dir(explicit_dir: Optional[str] = None) -> Path:
    candidates: list[Path] = []

    if explicit_dir:
        candidates.append(Path(explicit_dir).expanduser())

    env_dir = os.getenv("FANTASY_BASEBALL_RAW_DIR") or os.getenv("RAW_DATA_DIR")
    if env_dir:
        candidates.append(Path(env_dir).expanduser())

    candidates.extend([
        DEFAULT_RAW_DATA_DIR,
        Path(__file__).resolve().parents[2] / "data" / "raw",
        Path.cwd(),
    ])

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve() if candidate.exists() else candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists() and resolved.is_dir():
            return resolved

    for base_dir in [Path("/Users/matthewpitlock/Development"), Path.home(), Path.cwd()]:
        for filename in [
            "SDA - Major League Contract Tracker 080126.xlsx",
            "Fantrax-Players-Second Deck Association.csv",
            "Fantrax-Players-allhitterstats_080126.csv",
            "Fantrax-Players-allpitcherstats_080126.csv",
        ]:
            matches = sorted(base_dir.rglob(filename))
            if matches:
                return matches[0].parent

    raise FileNotFoundError(
        "Could not locate the raw data directory. Provide --raw-dir or set FANTASY_BASEBALL_RAW_DIR."
    )


def find_workbook(raw_dir: Path) -> Path:
    candidates = [
        raw_dir / "SDA - Major League Contract Tracker 080126.xlsx",
        raw_dir / "SDA - Major League Contract Tracker - Pitlock.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    matches = sorted(raw_dir.glob("*Contract*Tracker*.xlsx")) + sorted(raw_dir.glob("*Contract*Tracker*.csv"))
    if matches:
        return matches[0]

    raise FileNotFoundError(f"Workbook not found in {raw_dir}")


def build_export_frame(raw_dir: Path) -> pd.DataFrame:
    workbook_path = find_workbook(raw_dir)
    owner_tab_df = parse_owner_tabs(workbook_path)

    result = owner_tab_df[[
        "team_owner",
        "Player Name",
        "Pos",
        "Free Agent Year",
        "Move Type",
        "Player Contract Years",
    ]].copy()
    result.columns = [
        "team_owner",
        "Player Name",
        "Pos",
        "Free Agent Year",
        "Move Type",
        "Player Contract Years",
    ]
    result["Player Contract Years"] = pd.to_numeric(result["Player Contract Years"], errors="coerce").fillna(1)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export cleaned SDA multiyear-contract data to a staging CSV")
    parser.add_argument("--raw-dir", default=None, help="Directory containing the raw workbook and Fantrax CSV files")
    parser.add_argument("--output-dir", default=str(DEFAULT_STAGING_DIR), help="Directory to write the exported CSV")
    parser.add_argument("--output-name", default=DEFAULT_OUTPUT_NAME, help="Output filename for the CSV")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_dir = resolve_raw_dir(args.raw_dir)
    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / args.output_name

    export_df = build_export_frame(raw_dir)
    export_df.to_csv(output_path, index=False)

    print(f"Wrote {len(export_df):,} rows to {output_path}")


if __name__ == "__main__":
    main()
