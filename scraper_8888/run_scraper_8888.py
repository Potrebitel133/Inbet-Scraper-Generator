"""
8888 scraper: links workbook in, "Formatted Matches" workbook out.

Links workbook (first sheet): column A = any label, column B = an 8888punkt.bg
tournament link (or just the tournament id). One tournament per row. A header
row ("Link"/"URL" in column B) is allowed.

Output workbook: sheet "Formatted Matches" with the client's template columns
A..I (Match ID, Day, Time, Match, Home/Draw/Away Odds, Tournament, Tournament ID).

    python run_scraper_8888.py --input data/Tournament_Links_8888.xlsx --output out.xlsx
"""

import argparse
import os
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scraper_8888 import (  # noqa: E402
    COLUMNS,
    MAX_TOURNAMENT_WAIT_SECONDS,
    MIN_TOURNAMENT_WAIT_SECONDS,
    TournamentUrlError,
    _wait,
    parse_tournament_url,
    scrape_tournament,
)

HEADER_WORDS = {"link", "links", "url", "urls", "tournament link"}
COLUMN_WIDTHS = {"A": 10, "B": 20, "C": 7, "D": 36, "E": 11, "F": 11, "G": 11, "H": 22, "I": 14}

# Sample links file made on first start, so the layout is obvious.
SAMPLE_LINKS = [
    ("Premier League (football)", 2936), ("La Liga (football)", 2941), ("Serie A (football)", 2942),
    ("Bundesliga (football)", 2950), ("Ligue 1 (football)", 2943), ("NBA (basketball)", 2980),
    ("ATP Paris (tennis)", 18320),
]


def read_tournament_url_rows(path):
    """First sheet, column B, non-empty rows -> [(row_number, url)], header row skipped."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.worksheets[0]
    rows = []
    for row_number, row in enumerate(ws.iter_rows(min_row=1, values_only=True), start=1):
        if len(row) < 2 or row[1] is None or not str(row[1]).strip():
            continue
        value = str(row[1]).strip()
        if row_number == 1 and value.lower() in HEADER_WORDS:
            continue
        rows.append((row_number, value))
    wb.close()
    return rows


def read_tournament_urls(path):
    return [url for _, url in read_tournament_url_rows(path)]


def validate_input_file(path):
    """Problems with the links workbook (no network); empty list == ready to scrape."""
    if not os.path.exists(path):
        return [f"File not found: {path}"]
    try:
        rows = read_tournament_url_rows(path)
    except Exception as e:  # noqa: BLE001 - shown to the user as is
        return [f"Could not open workbook: {e}"]
    if not rows:
        return ["No tournament links found (expected a link in column B of the first sheet)."]
    errors = []
    for row_number, url in rows:
        try:
            parse_tournament_url(url)
        except TournamentUrlError as e:
            errors.append(f"Row {row_number}: {e}")
    return errors


def write_matches(rows, path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Formatted Matches"
    ws.append(COLUMNS)
    for row in rows:
        ws.append([row[c] for c in COLUMNS])
    for col, width in COLUMN_WIDTHS.items():
        ws.column_dimensions[col].width = width
    wb.save(path)


def write_sample_links(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Links"
    ws.append(["Tournament", "Link"])
    for label, tid in SAMPLE_LINKS:
        ws.append([label, tid])
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 70
    wb.save(path)


def scrape_all(urls, tourney_wait=(MIN_TOURNAMENT_WAIT_SECONDS, MAX_TOURNAMENT_WAIT_SECONDS), log=print):
    """Scrape every link in order; returns de-duplicated rows. Problems are logged, never fatal."""
    all_rows, seen = [], set()
    for i, url in enumerate(urls):
        if i > 0:
            _wait(*tourney_wait, lambda s: log(f"  (waiting {s:.1f}s before next tournament...)"))
        log(f"Scraping: {url}")
        rows, messages = scrape_tournament(url)
        for msg in messages:
            log(f"  ! {msg}")
        added = 0
        for row in rows:
            if row["Match ID"] in seen:          # same match listed under two links
                continue
            seen.add(row["Match ID"])
            all_rows.append(row)
            added += 1
        name = rows[0]["Tournament"] if rows else ""
        log(f"  -> {added} match(es) added{f' from {name}' if name else ''}"
            f" ({len(rows) - added} duplicate(s) skipped)")
    return all_rows


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Scrape 8888punkt.bg odds into the Formatted Matches layout.")
    parser.add_argument("--input", default=str(here / "data" / "Tournament_Links_8888.xlsx"))
    parser.add_argument("--output", default=str(here / "data" / "Scraped_Matches_8888.xlsx"))
    args = parser.parse_args()

    errors = validate_input_file(args.input)
    if errors:
        print(f"Tournament links file is invalid ({args.input}):")
        for err in errors:
            print(f"  ! {err}")
        raise SystemExit(1)
    rows = scrape_all(read_tournament_urls(args.input))
    write_matches(rows, args.output)
    print(f"\nWrote {len(rows)} match(es) -> {args.output}")


if __name__ == "__main__":
    main()
