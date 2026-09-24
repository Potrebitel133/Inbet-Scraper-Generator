"""
Read tournament links from tournaments/data/Tournament_Link.xlsx, scrape each,
and write the results as an xlsx matching the "Formatted Matches" layout of
PreFOrmattingOdds Goals MoreMatchesMainFOrtestOriginal - Copy.xlsm (its 8
columns, excluding Min Odd, which is filled in by hand), plus a 9th
"Tournament ID" column. When copied into a numbered ticket sheet with Min Odd
added as column 9, Tournament ID lands in column 10, where the bot reads it
and resolves the tournament directly instead of matching by name.

Input workbook (Sheet1): one row per tournament, column A = label (unused),
column B = tournament URL. Add more rows for more tournaments.

Usage:
    python run_scraper.py
    python run_scraper.py --input path\\to\\links.xlsx --output path\\to\\out.xlsx
"""

import argparse
import os
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # project root

from scraper import (
    COLUMNS,
    MAX_TOURNAMENT_WAIT_SECONDS,
    MIN_TOURNAMENT_WAIT_SECONDS,
    TournamentUrlError,
    _wait,
    parse_tournament_url,
    scrape_tournament,
)

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_INPUT = DATA_DIR / "Tournament_Link.xlsx"
DEFAULT_OUTPUT = DATA_DIR / "Scraped_Matches.xlsx"


def _safe_print(text):
    enc = sys.stdout.encoding or "utf-8"
    print(str(text).encode(enc, "replace").decode(enc))


def read_tournament_url_rows(path):
    """Sheet1, column B, all non-empty rows -> list of (row_number, url)."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    rows = []
    for row_number, row in enumerate(ws.iter_rows(min_row=1, values_only=True), start=1):
        if len(row) < 2:
            continue
        url = row[1]
        if url and str(url).strip():
            rows.append((row_number, str(url).strip()))
    return rows


def read_tournament_urls(path):
    """Sheet1, column B, all non-empty rows -> list of URLs."""
    return [url for _, url in read_tournament_url_rows(path)]


def validate_input_file(path):
    """Check the links workbook can be read and every URL in it is well-formed.

    Returns a list of human-readable problems; empty list == the file is good
    to scrape. Does not hit the network — only checks the workbook + URL shape.
    """
    if not os.path.exists(path):
        return [f"File not found: {path}"]
    try:
        rows = read_tournament_url_rows(path)
    except Exception as e:
        return [f"Could not open workbook: {e}"]
    if not rows:
        return ["No tournament URLs found (expected a URL in column B of Sheet1)."]

    errors = []
    for row_number, url in rows:
        try:
            parse_tournament_url(url)
        except TournamentUrlError:
            errors.append(f"Row {row_number}: not a recognizable tournament URL — {url}")
    return errors


def write_matches(rows, path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Formatted Matches"
    ws.append(COLUMNS)
    for row in rows:
        ws.append([row[c] for c in COLUMNS])
    wb.save(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    input_path = Path(args.input)
    validation_errors = validate_input_file(input_path)
    if validation_errors:
        _safe_print(f"Tournament links file is invalid ({input_path}):")
        for err in validation_errors:
            _safe_print(f"  ! {err}")
        raise SystemExit(1)

    urls = read_tournament_urls(input_path)

    all_rows = []
    seen_match_ids = set()
    for i, url in enumerate(urls):
        if i > 0:
            _wait(MIN_TOURNAMENT_WAIT_SECONDS, MAX_TOURNAMENT_WAIT_SECONDS,
                  lambda s: _safe_print(f"  (waiting {s:.1f}s before next tournament...)"))
        _safe_print(f"Scraping: {url}")
        rows, errors = scrape_tournament(
            url, on_wait=lambda s: _safe_print(f"  (waiting {s:.1f}s before next match...)"))
        for err in errors:
            _safe_print(f"  ! {err}")
        added = 0
        for row in rows:
            key = row["Match ID"] or row["Match"]
            if key in seen_match_ids:  # de-dupe across multiple tournament links
                continue
            seen_match_ids.add(key)
            all_rows.append(row)
            added += 1
        _safe_print(f"  -> {added} match(es) added ({len(rows) - added} duplicate(s) skipped)")

    write_matches(all_rows, args.output)
    _safe_print(f"\nWrote {len(all_rows)} match(es) -> {args.output}")


if __name__ == "__main__":
    main()
