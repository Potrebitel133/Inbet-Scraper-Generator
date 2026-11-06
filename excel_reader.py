"""
Read + validate the Excel ticket workbook.

One numbered sheet (any integer name — "1", "2", ... "19", ...) = one N-fold
ticket, where N is the ticket type picked for the run (5..10, see
config.MATCH_TYPES). Type N always uses the fixed rows A2:I(N+1); the ticket's
total odds ("Product") is always in config.PRODUCT_CELL (I12). `load_all_tickets` opens the workbook once, validates every numbered
sheet actually present, and returns only the sheets that are usable. Bad
sheets are reported and skipped so the rest of the batch still runs.
"""

import logging
import re

import openpyxl

import config

log = logging.getLogger("bot.excel")


def pick_side(home_odd, draw_odd, away_odd, min_odd):
    """Return 'home' | 'draw' | 'away' — whichever odd equals the Min Odd (col I).

    A missing/non-numeric Min Odd (e.g. not filled in yet) is not a match for
    anything; _validate() reports that as its own clean error, so this must
    not raise — it just falls through to the "home" default like any other
    unmatchable case.
    """
    candidates = {"home": home_odd, "draw": draw_odd, "away": away_odd}
    mo = _num(min_odd)
    if mo is None:
        return "home"
    # Exact match first, then nearest (odds may have been rounded in the sheet).
    for side, odd in candidates.items():
        if _num(odd) is not None and abs(_num(odd) - mo) < 1e-9:
            return side
    present = {s: _num(o) for s, o in candidates.items() if _num(o) is not None}
    if not present:
        return "home"
    return min(present, key=lambda s: abs(present[s] - mo))


def _num(v):
    """Best-effort float, or None if not numeric."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


MATCH_ID_RE = re.compile(r"^#\d+$")


def available_ticket_sheets(wb):
    """Return existing numbered ticket sheets in numeric order."""
    numbered = []
    for name in wb.sheetnames:
        if str(name).isdigit():
            numbered.append(str(name))
    return sorted(numbered, key=int)


def get_available_ticket_sheets(excel_path):
    """Open a workbook and return only the numbered sheets it contains."""
    wb = openpyxl.load_workbook(excel_path, read_only=True, keep_vba=False)
    return available_ticket_sheets(wb)


# Header row (row 1) -> pick dict key. Matched by name, not position, so sheets
# may list columns in any order and Tournament ID may be absent (hand-built
# sheets) or wherever the writer put it (e.g. scraper-split sheets put it
# before Min Odd; the original hand-built workbook has 9 columns, Min Odd last).
_HEADER_TO_KEY = {
    "match id": "match_id",
    "day": "day",
    "time": "time",
    "match": "match",
    "home odds": "home_odd",
    "draw odds": "draw_odd",
    "away odds": "away_odd",
    "tournament": "tournament",
    "min odd": "min_odd",
    "tournament id": "tournament_id",
}


def _header_columns(ws):
    """Row 1 -> {pick_key: 1-based column index}, unknown headers ignored."""
    columns = {}
    for col_idx, header in enumerate(next(ws.iter_rows(min_row=1, max_row=1, values_only=True), []), start=1):
        key = _HEADER_TO_KEY.get(str(header or "").strip().lower())
        if key:
            columns[key] = col_idx
    return columns


FORMATTED_MATCHES_SHEET = "Formatted Matches"


def build_tournament_id_lookup(wb):
    """{match_id: tournament_id} from the workbook's "Formatted Matches" sheet.

    The client builds numbered ticket sheets by hand-copying rows out of the
    scraper's "Formatted Matches" sheet (which they leave in the workbook) but
    doesn't carry over its Tournament ID column. Rather than ask them to add
    it, look it up here by Match ID — every row in "Formatted Matches" already
    has both, since the scraper wrote it. Returns {} if there's no such sheet.
    """
    if FORMATTED_MATCHES_SHEET not in wb.sheetnames:
        return {}
    ws = wb[FORMATTED_MATCHES_SHEET]
    columns = _header_columns(ws)
    if "match_id" not in columns or "tournament_id" not in columns:
        return {}
    mid_col, tid_col = columns["match_id"] - 1, columns["tournament_id"] - 1
    lookup = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if mid_col >= len(row) or tid_col >= len(row):
            continue
        match_id = str(row[mid_col] or "").strip()
        tid = _num(row[tid_col])
        if match_id and tid is not None:
            lookup[match_id] = int(tid)
    return lookup


def _read_sheet(ws, tournament_id_lookup=None, match_count=10):
    """Read the ticket rows 2..match_count+1 (2..11 for 10), including incomplete rows.

    Columns are matched by their header text (row 1), not fixed position, so
    this works for both the original 9-column hand-built workbook (Min Odd
    last, no Tournament ID) and scraper-produced sheets (Tournament ID may
    appear anywhere, e.g. right before Min Odd). Tournament ID is optional —
    hand-built sheets fall back to name-based resolution; sheets that carry it
    let the bot skip fuzzy name matching entirely for that row.

    tournament_id_lookup, if given, fills in a missing tournament_id by Match
    ID (see build_tournament_id_lookup) — the client's usual workflow: numbered
    sheets hand-copied from "Formatted Matches" without its Tournament ID
    column, which is instead recovered here from that sheet.
    """
    tournament_id_lookup = tournament_id_lookup or {}
    columns = _header_columns(ws)
    max_col = max(columns.values(), default=0)

    def cell(row, key):
        idx = columns.get(key)
        return row[idx - 1] if idx and idx <= len(row) else None

    picks = []
    for row in ws.iter_rows(min_row=2, max_row=1 + match_count, max_col=max_col, values_only=True):
        match_id = cell(row, "match_id")
        match = cell(row, "match")
        home, draw, away = cell(row, "home_odd"), cell(row, "draw_odd"), cell(row, "away_odd")
        min_odd = cell(row, "min_odd")
        home_team, _, away_team = str(match).partition(" - ")
        tournament_id = cell(row, "tournament_id")
        if tournament_id is None:
            tournament_id = tournament_id_lookup.get(str(match_id or "").strip())
        picks.append({
            "match_id": match_id,
            "match": match,
            "home_team": home_team.strip(),
            "away_team": away_team.strip(),
            "home_odd": home,
            "draw_odd": draw,
            "away_odd": away,
            "min_odd": min_odd,
            "tournament": cell(row, "tournament"),
            "tournament_id": tournament_id,
            "side": pick_side(home, draw, away, min_odd),
        })
    return picks


def _extra_rows(ws, match_count):
    """Row numbers BELOW a type's fixed range (up to LAST_TICKET_ROW) that hold a match.

    Type N uses rows 2..N+1 only. A match further down means the sheet was built
    for a bigger ticket type than the one selected, so it must not run as N.
    """
    first = match_count + 2
    if first > config.LAST_TICKET_ROW:
        return []
    columns = _header_columns(ws)
    keys = [k for k in ("match_id", "match") if k in columns]
    if not keys:
        return []
    max_col = max(columns.values())
    rows = []
    for row_no, row in enumerate(ws.iter_rows(min_row=first, max_row=config.LAST_TICKET_ROW,
                                              max_col=max_col, values_only=True), start=first):
        if any(str(row[columns[k] - 1] or "").strip() for k in keys if columns[k] <= len(row)):
            rows.append(row_no)
    return rows


def read_product(ws):
    """The ticket's total odds from the fixed Product cell (I12), or None if not numeric."""
    return _num(ws[config.PRODUCT_CELL].value)


def _validate(picks, has_tournament_id_column=False, match_count=10):
    """Return a list of human-readable problems; empty list == sheet is good.

    has_tournament_id_column: True when the sheet's header row has a
    "Tournament ID" column at all. When it does, every row must fill it in —
    sheets that include the column (e.g. from the tournament scraper) are
    expected to resolve by id, not name. Sheets with NO such column (the
    original hand-built workbook) are unaffected and keep resolving by name.
    """
    errors = []
    found = sum(1 for p in picks if str(p.get("match") or "").strip())
    if found != match_count:
        errors.append(f"Sheet must contain exactly {match_count} matches; found {found}.")
    for row_no, p in enumerate(picks, start=2):
        where = f"row {row_no}"
        match_id = str(p["match_id"] or "").strip()
        if not match_id:
            errors.append(f"{where}: Match ID is empty; enter it like #3887")
        elif not MATCH_ID_RE.fullmatch(match_id):
            errors.append(f"{where}: Match ID must contain # followed by digits, like #3887")
        match_text = str(p["match"] or "").strip()
        if not match_text:
            errors.append(f"{where}: Match Name is empty.")
        elif not p["home_team"] or not p["away_team"]:
            errors.append(f"{where}: Match must contain both teams, separated by ' - '")
        for label in ("home_odd", "draw_odd", "away_odd"):
            if p[label] is None or _num(p[label]) is None:
                errors.append(f"{where}: {label.replace('_', ' ').title()} is required and must be numeric")
        if p["min_odd"] is None or _num(p["min_odd"]) is None:
            errors.append(f"{where}: Min Odd is required and must be numeric")
        # Required: the tournament name is how a Match ID is located at all — the
        # bot searches for the event inside a tournament, never site-wide.
        if not str(p["tournament"] or "").strip():
            errors.append(f"{where}: Tournament is empty; it is required to find the match")
        if has_tournament_id_column and _num(p["tournament_id"]) is None:
            errors.append(f"{where}: Tournament ID is empty; it is required and must be numeric")
        # min_odd must EXACTLY equal one side — otherwise pick_side falls back to
        # 'nearest' and we'd silently bet the wrong outcome.
        mo = _num(p["min_odd"])
        sides = [_num(p[o]) for o in ("home_odd", "draw_odd", "away_odd")]
        if mo is not None and not any(o is not None and abs(o - mo) < 1e-9 for o in sides):
            errors.append(f"{where}: Min Odd must equal Home, Away, or Draw")
    return errors


def load_all_tickets(excel_path, sheets=None, match_count=10):
    """Open the workbook once, validate each sheet.

    match_count is the ticket type for this run (5..10): each sheet must hold
    exactly that many matches in rows 2..match_count+1 and nothing below them.

    sheets=None (the default) picks up every numbered sheet actually present
    in the workbook (available_ticket_sheets) — 1..10, 1..19, whatever the
    client built. Pass an explicit list to process only specific sheets.

    Returns (tickets, skipped):
      tickets  = {sheet: [picks]} for sheets that passed validation
      skipped  = [(sheet, [reasons])] for sheets that were dropped
    """
    tickets, skipped, _ = load_tickets_and_products(excel_path, sheets, match_count)
    return tickets, skipped


def load_tickets_and_products(excel_path, sheets=None, match_count=10):
    """load_all_tickets plus {sheet: Product (I12) as float or None} for every sheet read."""
    if match_count not in config.MATCH_TYPES:
        raise ValueError(f"Ticket type must be one of {list(config.MATCH_TYPES)} matches; got {match_count}")
    wb = openpyxl.load_workbook(excel_path, data_only=True, keep_vba=False)
    if sheets is None:
        sheets = available_ticket_sheets(wb)
    tournament_id_lookup = build_tournament_id_lookup(wb)
    if tournament_id_lookup:
        log.info("tournament id lookup: %d match id(s) from '%s'",
                  len(tournament_id_lookup), FORMATTED_MATCHES_SHEET)
    tickets, skipped, products = {}, [], {}
    log.info("validating %d sheets from %s (%d-match tickets)", len(sheets), excel_path, match_count,
             extra={"ctx": {"match_type": match_count}})
    for sheet in sheets:
        if sheet not in wb.sheetnames:
            skipped.append((sheet, ["sheet missing from workbook"]))
            log.warning("sheet %s INVALID: missing from workbook", sheet,
                        extra={"ctx": {"sheet": sheet, "valid": False, "reasons": ["missing from workbook"]}})
            continue
        ws = wb[sheet]
        picks = _read_sheet(ws, tournament_id_lookup, match_count)
        products[sheet] = read_product(ws)
        has_tournament_id_column = "tournament_id" in _header_columns(ws)
        errors = _validate(picks, has_tournament_id_column, match_count)
        extra = _extra_rows(ws, match_count)
        if extra:
            errors.append(f"row {extra[0]}: there are matches below row {match_count + 1}, so this "
                          f"sheet has more than {match_count} matches. Is the right ticket type selected?")
        if errors:
            skipped.append((sheet, errors))
            log.warning("sheet %s INVALID (%d problems): %s", sheet, len(errors), "; ".join(errors),
                        extra={"ctx": {"sheet": sheet, "valid": False, "reasons": errors}})
        else:
            tickets[sheet] = picks
            log.info("sheet %s valid: %d legs, all cells present", sheet, len(picks),
                     extra={"ctx": {"sheet": sheet, "valid": True, "legs": len(picks)}})
    log.info("validation done: %d valid, %d skipped", len(tickets), len(skipped),
             extra={"ctx": {"valid_count": len(tickets), "skipped_count": len(skipped)}})
    return tickets, skipped, products


if __name__ == "__main__":
    # Self-check: pick_side (exact + nearest) and _validate catch the real cases.
    assert pick_side(2.0, 3.0, 4.0, 3.0) == "draw"
    assert pick_side(2.0, 3.0, 4.0, 4.1) == "away"      # nearest fallback
    assert pick_side(None, None, None, 1.5) == "home"   # empty guard
    good = [{"match_id": "#1234", "match": "A - B", "home_team": "A", "away_team": "B", "home_odd": 2.0,
             "draw_odd": 3.0, "away_odd": 4.0, "min_odd": 3.0, "tournament": "T"}] * 10
    assert _validate(good) == []
    bad = [dict(good[0], min_odd=9.9)]  # matches no side + only 1 leg
    errs = _validate(bad)
    assert any("Min Odd" in e for e in errs) and any("exactly 10 matches" in e for e in errs), errs

    # Tournament ID: required only when the sheet's header row HAS that column.
    good_with_tid = [dict(good[0], tournament_id=19000000679)] * 10
    assert _validate(good_with_tid, has_tournament_id_column=True) == []
    assert _validate(good, has_tournament_id_column=False) == []  # no column -> not required
    missing_tid = [dict(good[0], tournament_id=None)] * 10
    errs = _validate(missing_tid, has_tournament_id_column=True)
    assert any("Tournament ID" in e for e in errs), errs
    assert _validate(missing_tid, has_tournament_id_column=False) == []  # column absent -> no check

    # Ticket types: a 5-match sheet validates as type 5, never as type 10.
    assert _validate(good[:5], match_count=5) == []
    errs = _validate(good[:5], match_count=10)
    assert any("exactly 10 matches; found 5" in e for e in errs), errs
    errs = _validate(good[:8], match_count=8)
    assert errs == [], errs

    print("excel_reader self-check ok")
