"""
Split a scraper output workbook's "Formatted Matches" rows into numbered
ticket sheets (1, 2, 3, ...) the combo-ticket bot can read directly.

Each sheet gets up to 10 matches, laid out with the bot's expected columns
(excel_reader._read_sheet reads them by header name, so order here doesn't
matter to the bot -- but Min Odd is placed last, as the final human-filled
column): Match ID, Day, Time, Match, Home Odds, Draw Odds, Away Odds,
Tournament, Tournament ID, Min Odd. Min Odd is left BLANK -- it's the human's
pick of which side to bet and the scraper has no opinion on it. The last
sheet may have fewer than 10 matches; the bot's validation will flag it until
someone fills in the rest (or removes it).

Usage:
    python split_into_tickets.py "path\\to\\Scraped_Matches_....xlsx"
    python split_into_tickets.py "path\\to\\Scraped_Matches_....xlsx" --output "path\\to\\out.xlsx"
"""

import argparse
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper import COLUMNS  # Match ID, Day, Time, Match, Home/Draw/Away Odds, Tournament, Tournament ID

TICKET_COLUMNS = [
    "Match ID", "Day", "Time", "Match", "Home Odds", "Draw Odds", "Away Odds",
    "Tournament", "Tournament ID", "Min Odd",
]


def _safe_print(text):
    enc = sys.stdout.encoding or "utf-8"
    print(str(text).encode(enc, "replace").decode(enc))


def read_scraped_rows(path):
    """First worksheet, header row + data rows -> list of dicts keyed by COLUMNS."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    return [dict(zip(COLUMNS, row)) for row in rows if any(v not in (None, "") for v in row)]


def split_into_sheets(rows, per_sheet=10):
    """[{...}, ...] -> {"1": [...], "2": [...], ...}, 10 per sheet (last may be short)."""
    sheets = {}
    for i in range(0, len(rows), per_sheet):
        sheet_no = str(i // per_sheet + 1)
        sheets[sheet_no] = rows[i:i + per_sheet]
    return sheets


def write_ticket_workbook(sheets, path):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # drop the default blank sheet
    for sheet_no, rows in sheets.items():
        ws = wb.create_sheet(title=sheet_no)
        ws.append(TICKET_COLUMNS)
        for row in rows:
            ws.append([
                row.get("Match ID"), row.get("Day"), row.get("Time"), row.get("Match"),
                row.get("Home Odds"), row.get("Draw Odds"), row.get("Away Odds"),
                row.get("Tournament"), row.get("Tournament ID"),
                None,  # Min Odd: left blank for manual entry, last column
            ])
    wb.save(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="Scraper output workbook (Formatted Matches sheet)")
    parser.add_argument("--output", help="Output path (default: <input>_tickets.xlsx)")
    parser.add_argument("--per-sheet", type=int, default=10)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        _safe_print(f"File not found: {input_path}")
        raise SystemExit(1)
    output_path = Path(args.output) if args.output else input_path.with_name(input_path.stem + "_tickets.xlsx")

    rows = read_scraped_rows(input_path)
    if not rows:
        _safe_print(f"No match rows found in {input_path}")
        raise SystemExit(1)

    sheets = split_into_sheets(rows, per_sheet=args.per_sheet)
    write_ticket_workbook(sheets, output_path)

    _safe_print(f"{len(rows)} matches -> {len(sheets)} sheet(s) in {output_path}")
    for sheet_no, sheet_rows in sheets.items():
        note = "" if len(sheet_rows) == args.per_sheet else f"  (only {len(sheet_rows)} — needs {args.per_sheet - len(sheet_rows)} more or will fail validation)"
        _safe_print(f"  sheet {sheet_no}: {len(sheet_rows)} match(es){note}")
    _safe_print("\nMin Odd is blank on every row — fill it in (must equal one of the Home/Draw/Away odds) before running the bot.")


if __name__ == "__main__":
    main()
