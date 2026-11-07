"""
Never make the same ticket twice, across Stop, Resume, closing and restarting.

The list of tickets already made is tickets_log.json (the same file the
dashboard shows). Before a live ticket is made the bot checks it twice:

  1. before any API call: the sheet's combination (Match IDs + chosen sides)
  2. just before create-draft: the exact outcome IDs the site resolved

If either matches a ticket already made, the sheet is skipped as "already
created". A combination is the SET of picks, so the same matches in another
sheet, another workbook or another order still count as the same ticket.
Tickets made before Match IDs were stored are matched by match name + side.

A ticket counts as made the moment the site returns its voucher. Right then,
before the QR and the log are written, it is noted in tickets_in_progress.json.
If the robot is closed or crashes in that short window, the next run finishes
that ticket from the note (QR + log entry) instead of making it again. If it
stops before a voucher arrives, nothing usable exists (no voucher = no QR to
scan), so that sheet is simply made on the next run.
"""

import json
import os
import time

import config
from resolver import match_id_code, normalize


class LedgerError(RuntimeError):
    """tickets_log.json / tickets_in_progress.json exists but cannot be read."""


class AlreadyCreated(Exception):
    """Raised for a ticket whose combination was already made."""

    def __init__(self, sheet, record):
        super().__init__(f"sheet {sheet}: already created as voucher {record.get('voucher')}")
        self.sheet = sheet
        self.record = record


# --- keys ----------------------------------------------------------------------
def _id_key(pairs):
    pairs = list(pairs)
    if not pairs or not all(code for code, _ in pairs):
        return None
    return "id:" + "|".join(sorted(f"{code}={side}" for code, side in pairs))


def _name_key(pairs):
    pairs = list(pairs)
    if not pairs:
        return None
    return "nm:" + "|".join(sorted(f"{normalize(name)}={side}" for name, side in pairs))


def _outcome_key(outcome_ids):
    ids = [str(o) for o in outcome_ids if o not in (None, "")]
    return "oc:" + "|".join(sorted(ids)) if ids else None


def pick_keys(picks):
    """(Match-ID key, match-name key) for a sheet's picks."""
    return (_id_key((match_id_code(p), p["side"]) for p in picks),
            _name_key((p.get("match"), p["side"]) for p in picks))


# --- files -----------------------------------------------------------------------
def _read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise LedgerError(
            f"{path} could not be read ({e}). It holds the tickets already made, which is "
            f"what stops duplicates, so nothing will run until it is fixed or restored.")


def write_json_atomic(path, data):
    """Write to a temp file, then swap it in, so a crash never leaves a half-written file."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(10):          # Windows: the dashboard may be reading it this instant
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.2)


def read_tickets_log():
    data = _read_json(config.TICKETS_LOG, [])
    if not isinstance(data, list):
        raise LedgerError(f"{config.TICKETS_LOG} is not a list of tickets.")
    return data


def append_ticket_record(record):
    tickets = read_tickets_log()
    tickets.append(record)
    write_json_atomic(config.TICKETS_LOG, tickets)


def _record_keys(record):
    sels = record.get("selections") or []
    keys = [_outcome_key(s.get("outcomeId") for s in sels)]
    if sels and all(s.get("match_id") for s in sels):
        keys.append(_id_key((match_id_code(s), s.get("side")) for s in sels))
    else:  # made before Match IDs were stored: fall back to match name + side
        keys.append(_name_key((s.get("match"), s.get("side")) for s in sels))
    return [k for k in keys if k]


def created_index():
    """{key: ticket record} for every ticket in tickets_log.json."""
    index = {}
    for record in read_tickets_log():
        if record.get("voucher"):
            for key in _record_keys(record):
                index.setdefault(key, record)
    return index


def find_created(picks=None, outcome_ids=None, index=None):
    """The earlier ticket with this combination, or None."""
    index = created_index() if index is None else index
    keys = []
    if picks is not None:
        keys.extend(pick_keys(picks))
    if outcome_ids is not None:
        keys.append(_outcome_key(outcome_ids))
    return next((index[k] for k in keys if k and k in index), None)


# --- in-progress notes ---------------------------------------------------------
def read_in_progress():
    data = _read_json(config.IN_PROGRESS_FILE, [])
    if not isinstance(data, list):
        raise LedgerError(f"{config.IN_PROGRESS_FILE} is not a list.")
    return data


def note_in_progress(record):
    """Note a ticket whose voucher just arrived, before its QR and log entry exist."""
    notes = [n for n in read_in_progress() if n.get("voucher") != record.get("voucher")]
    notes.append(record)
    write_json_atomic(config.IN_PROGRESS_FILE, notes)


def clear_in_progress(voucher):
    notes = read_in_progress()
    left = [n for n in notes if n.get("voucher") != voucher]
    if len(left) != len(notes):
        if left:
            write_json_atomic(config.IN_PROGRESS_FILE, left)
        else:
            os.remove(config.IN_PROGRESS_FILE)


def compress_labels(match_type, sheets):
    """['1','2','3','5'] -> '6-1 to 6-3, 6-5' (for popups)."""
    nums = sorted(int(s) for s in sheets)
    parts, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        a, b = f"{match_type}-{nums[i]}", f"{match_type}-{nums[j]}"
        parts.append(a if i == j else f"{a} to {b}")
        i = j + 1
    return ", ".join(parts)
