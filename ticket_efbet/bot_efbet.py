"""
efbet ticket generator: one efbet ticket (booking number + barcode) per sheet
of the chosen ticket type.

The client's efbet workbook holds every ticket type in one file: a sheet named
"5-19" is ticket 19 of the 5-match type, "8-3" ticket 3 of the 8-match type.
He makes one type at a time (5 matches, then 8, ...), like on 8888, so a run
takes only the sheets of the type chosen in the window. Inside, a sheet is
laid out like the 8888 / Inbet sheets (Match ID, ..., Min Odd, Product in I12),
with efbet's plain match numbers as Match ID.

Same stake rules, window and never-twice record as the 8888 robot (stake_rules,
ticket_ledger, dashboard). For every sheet of the type:
  1. the sheet is read and checked. When Min Odd equals two sides, one of them
     is picked at random (client's rule)
  2. skipped if the same ticket was made before (with either tied side)
  3. every match is read from efbet: it must exist, be the sheet's match, not
     have started, and have its early payout odds (Ранно Изплащане) open
  4. stake from the ticket type's rule: fixed, or a formula with I12 / ODDS
  5. the ticket is booked on efbet exactly as the efbet page does it -> number
  6. the booking is read back from efbet and compared: matches, markets,
     picks, stake. Anything different -> no barcode, the sheet is reported
  7. barcode saved (Code 128 of the number, like efbet's own) and the ticket
     recorded in tickets_log_efbet.json

"Check only" (dry_run) does steps 1 to 4 against the live site and creates nothing.
"""

import contextlib
import json
import logging
import os
import random
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for folder in (HERE, HERE.parent, HERE.parent / "scraper_8888", HERE.parent / "ticket_8888"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import config  # noqa: E402

# Own files, so the efbet robot never mixes its tickets with the other robots'.
FILES_EFBET = {
    "TICKETS_LOG": "tickets_log_efbet.json",
    "IN_PROGRESS_FILE": "tickets_in_progress_efbet.json",
    "SETTINGS_FILE": "settings_efbet.json",
    "QR_OUTPUT_DIR": "barcodes_efbet",
    "EXCEL_FILE": "efbet_tickets.xlsx",
    "LOG_FILE_NAME": "bot_efbet.jsonl",
    "DEFAULT_MATCH_TYPE": 5,
}


def use_efbet_files():
    """Point the shared modules (ticket_ledger, stake_rules, dashboard) at the efbet files."""
    for key, value in FILES_EFBET.items():
        setattr(config, key, value)


@contextlib.contextmanager
def _own_files():
    saved = {key: getattr(config, key, None) for key in FILES_EFBET}
    use_efbet_files()
    try:
        yield
    finally:
        for key, value in saved.items():
            setattr(config, key, value)


import openpyxl  # noqa: E402

import barcode_efbet  # noqa: E402
import excel_reader  # noqa: E402
import slip_efbet as slip  # noqa: E402
import stake_rules  # noqa: E402
import swarm_efbet as swarm  # noqa: E402
import ticket_ledger  # noqa: E402
from bot_8888 import find_created, pick_random_sides, tied_sides  # noqa: E402,F401
from swarm_efbet import SwarmError  # noqa: E402

log = logging.getLogger("bot.efbet")

SUPPORTS_CHECK = True                 # the window shows "Check only"
REJECTED_LOG = "tickets_rejected_efbet.json"
CHECK_WAIT = (1.0, 2.5)               # seconds between sheets in "Check only"
READ_BACK_TRIES = 3
SHEET_NAME = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")
MATCH_ID_RE = re.compile(r"^#?\d+$")
MATCH_ID_EXAMPLE = "31462439 (efbet's match number)"


# --- the workbook -----------------------------------------------------------------------
def type_sheets(sheetnames, match_type):
    """{'19': '5-19', ...}: the sheets of one ticket type, by ticket number."""
    found = {}
    for name in sheetnames:
        m = SHEET_NAME.match(str(name))
        if m and int(m.group(1)) == int(match_type):
            found[str(int(m.group(2)))] = name
    return dict(sorted(found.items(), key=lambda kv: int(kv[0])))


def available_sheets(excel_path, match_type=None):
    """Ticket numbers of the chosen type in the workbook (the window's Resume uses it)."""
    wb = openpyxl.load_workbook(excel_path, read_only=True, keep_vba=False)
    try:
        return list(type_sheets(wb.sheetnames, match_type or config.DEFAULT_MATCH_TYPE))
    finally:
        wb.close()


def load_tickets_and_products(excel_path, sheets=None, match_count=5):
    """Like excel_reader.load_tickets_and_products, for the sheets named '<type>-<number>'.

    Returns ({number: picks}, [(number, [problems])], {number: Product I12}).
    """
    if match_count not in config.MATCH_TYPES:
        raise ValueError(f"Ticket type must be one of {list(config.MATCH_TYPES)} matches; got {match_count}")
    wb = openpyxl.load_workbook(excel_path, data_only=True, keep_vba=False)
    names = type_sheets(wb.sheetnames, match_count)
    if not names:
        problem = (f"The workbook has no sheets for {match_count}-match tickets. Name them "
                   f"{match_count}-1, {match_count}-2, ... (it has: {', '.join(map(str, wb.sheetnames[:12]))})")
        log.warning(problem, extra={"ctx": {"match_type": match_count}})
        return {}, [(f"{match_count}-?", [problem])], {}
    sheets = list(names) if sheets is None else [str(s) for s in sheets]
    log.info("validating %d sheets from %s (%d-match tickets)", len(sheets), excel_path, match_count,
             extra={"ctx": {"match_type": match_count}})
    tickets, skipped, products = {}, [], {}
    for sheet in sheets:
        if sheet not in names:
            skipped.append((sheet, [f"sheet {match_count}-{sheet} missing from workbook"]))
            continue
        ws = wb[names[sheet]]
        picks = excel_reader._read_sheet(ws, None, match_count)
        for p in picks:
            if isinstance(p.get("match_id"), float) and p["match_id"].is_integer():
                p["match_id"] = int(p["match_id"])
        products[sheet] = excel_reader.read_product(ws)
        has_tid = "tournament_id" in excel_reader._header_columns(ws)
        errors = excel_reader._validate(picks, has_tid, match_count, match_id_re=MATCH_ID_RE,
                                        match_id_example=MATCH_ID_EXAMPLE)
        extra = excel_reader._extra_rows(ws, match_count)
        if extra:
            errors.append(f"row {extra[0]}: there are matches below row {match_count + 1}, so this "
                          f"sheet has more than {match_count} matches.")
        if errors:
            skipped.append((sheet, errors))
            log.warning("sheet %s INVALID (%d problems): %s", names[sheet], len(errors), "; ".join(errors),
                        extra={"ctx": {"sheet": sheet, "valid": False, "reasons": errors}})
        else:
            tickets[sheet] = picks
            log.info("sheet %s valid: %d matches", names[sheet], len(picks),
                     extra={"ctx": {"sheet": sheet, "valid": True, "legs": len(picks)}})
    return tickets, skipped, products


# --- names, barcode, records -------------------------------------------------------------
def ticket_label(match_type, sheet):
    """'5-19': the sheet's own name. Names the barcode file and the ticket everywhere."""
    return f"{match_type}-{sheet}"


def generate_qr(code, out_path):
    """The barcode the cashier scans: Code 128 of the booking number, like efbet's own
    (named generate_qr because the shared window calls it that)."""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    barcode_efbet.save(str(code), out_path)
    if barcode_efbet.decode(barcode_efbet.render(str(code))) != str(code):     # never save an unreadable one
        raise ValueError(f"barcode for {code} does not read back")
    return out_path


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def efbet_figures(stake, legs, rules):
    odds = slip.total_odds(legs)
    percent, cap = slip.bonus_rule(legs, rules)
    possible, bonus, final = slip.winnings(stake, odds, percent, cap)
    return {"total_odds": odds, "bonus_percent": percent, "possible_win": possible, "bonus": bonus,
            "final_payout": final}


def build_record(code, sheet, stake, legs, path, run_id, match_type, figures):
    return {
        "voucher": code, "sheet": sheet, "stake": slip.money(stake), "match_type": match_type,
        "label": ticket_label(match_type, sheet), "combined_odds": figures["total_odds"],
        "qr": path, "run_id": run_id, "timestamp": _now_iso(), "site": "efbet", "verified": False,
        "on_efbet": figures,
        "selections": [{
            "match": leg["match"], "match_id": leg["match_id"], "side": leg["side"], "pick": leg["pick"],
            "odds": leg["odds"], "outcomeId": leg["selection_id"], "market_id": leg["market_id"],
            "market": leg["market_name"], "start": leg["start"], "random_pick": bool(leg.get("tie")),
        } for leg in legs],
    }


def _append_json_list(path, item):
    try:
        with open(path, encoding="utf-8") as f:
            items = json.load(f)
        if not isinstance(items, list):
            items = []
    except (OSError, ValueError):
        items = []
    items.append(item)
    ticket_ledger.write_json_atomic(path, items)


# --- efbet ---------------------------------------------------------------------------------
def create_ticket(legs, stake):
    return slip.booking_id(swarm.create_booking(slip.booking_params(legs, stake)))


def read_back(code):
    """The booking as efbet stored it."""
    last = None
    for attempt in range(READ_BACK_TRIES):
        try:
            return slip.parse_booking(swarm.get_booking(code))
        except (SwarmError, ValueError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise SwarmError(f"could not read ticket {code} back from efbet ({last})")


def stake_limits():
    """(min, max) stake in EUR for one ticket, from efbet's session (0.25 / 1943 on 25 Nov 2026)."""
    cfg = swarm.partner_config()
    lo = (cfg.get("min_bet_stakes") or {}).get("EUR")
    hi = (cfg.get("prematch_max_stake") or {}).get("EUR")
    return (float(lo) if lo else None), (float(hi) if hi else None)


def _reject(record, problems):
    record = dict(record, rejected=problems, rejected_at=_now_iso())
    _append_json_list(REJECTED_LOG, record)
    ticket_ledger.clear_in_progress(record["voucher"])


def finish_pending():
    """Tickets whose number arrived but whose barcode/record was cut off (closed or crashed).

    Each one is read back and checked like a new ticket, then finished
    (barcode + record) so it counts as made and is never made again, or rejected.
    """
    logged = {str(t.get("voucher")) for t in ticket_ledger.read_tickets_log()}
    finished = []
    for record in ticket_ledger.read_in_progress():
        code = str(record.get("voucher"))
        if code in logged:
            ticket_ledger.clear_in_progress(code)
            continue
        legs = record.get("_legs") or []
        try:
            booking = read_back(code)
        except SwarmError as e:
            log.error("ticket %s (number %s) was cut off and cannot be checked now: %s. It stays "
                      "pending and is checked on the next run.", record.get("label"), code, e,
                      extra={"ctx": {"sheet": record.get("sheet"), "voucher": code}})
            continue
        problems, _ = slip.check_booking(code, legs, record["stake"], booking)
        if problems:
            log.error("ticket %s (number %s) was cut off and is not what was asked on efbet: %s. "
                      "No barcode saved.", record.get("label"), code, "; ".join(problems),
                      extra={"ctx": {"sheet": record.get("sheet"), "voucher": code, "problems": problems}})
            _reject(record, problems)
            continue
        record = {k: v for k, v in record.items() if k != "_legs"}
        record["verified"] = True
        generate_qr(code, record["qr"])
        ticket_ledger.append_ticket_record(record)
        ticket_ledger.clear_in_progress(code)
        finished.append(record)
        log.warning("ticket %s (number %s) was cut off last time: checked on efbet and finished now, "
                    "it will not be made again", record.get("label"), code,
                    extra={"ctx": {"sheet": record.get("sheet"), "voucher": code, "recovered": True}})
    return finished


def recover_interrupted():
    """For the window: nothing to do before a run. Cut-off tickets are checked on efbet
    and finished at the start of the next run (finish_pending)."""
    return []


# --- one sheet ----------------------------------------------------------------------------
def _legs_for(sheet, picks, ctx, games_cache):
    ids = [str(p.get("match_id")).lstrip("#").strip() for p in picks]
    missing = [i for i in ids if i not in games_cache]
    if missing:
        found = swarm.get_games(missing)
        for i in missing:
            games_cache[i] = found.get(i)
    legs, problems = [], []
    for p, mid in zip(picks, ids):
        try:
            leg = slip.resolve_pick(games_cache.get(mid), p)
            leg["tie"] = p.get("tie")
            legs.append(leg)
            if leg.get("name_note"):
                log.info("ticket %s: %s: %s", ctx["label"], p["match"], leg["name_note"],
                         extra={"ctx": {**ctx, "match": p["match"]}})
            mo = excel_reader._num(p.get("min_odd"))
            if mo is not None and abs(leg["odds"] - mo) > 1e-9:
                log.info("ticket %s: %s %s odds %s in the sheet, %s on efbet now", ctx["label"], p["match"],
                         leg["pick"], p["min_odd"], leg["odds"],
                         extra={"ctx": {**ctx, "match": p["match"], "excel_odds": mo, "live_odds": leg["odds"]}})
        except slip.PickError as e:
            problems.append(str(e))
            log.warning("ticket %s: %s (%s): %s", ctx["label"], p["match"], mid, e,
                        extra={"ctx": {**ctx, "match": p["match"], "match_id": mid,
                                       "tournament": p.get("tournament"), "reason": str(e)}})
    return legs, problems


def process_sheet(sheet, picks, product, match_type, stake_rule, dry_run, run_id, games_cache, rules):
    """Make (or, in "Check only", check) the ticket of one sheet -> barcode path / True, or None."""
    label = ticket_label(match_type, sheet)
    ctx = {"sheet": sheet, "label": label}
    legs, problems = _legs_for(sheet, picks, ctx, games_cache)
    if problems:
        log.warning("ticket %s not %s: %d match(es) cannot go on it", label,
                    "ready" if dry_run else "created", len(problems), extra={"ctx": ctx})
        return None
    odds = slip.total_odds(legs)
    stake = stake_rules.compute_stake(stake_rule, i12=product, odds=odds)
    lo, hi = stake_limits()
    if (lo is not None and float(stake) < lo) or (hi is not None and float(stake) > hi):
        reason = (f"stake {stake} EUR is outside what efbet takes on one ticket "
                  f"({lo:g} to {hi:g} EUR)" if lo is not None and hi is not None else f"stake {stake} not allowed")
        log.error("ticket %s not %s: %s", label, "ready" if dry_run else "created", reason,
                  extra={"ctx": {**ctx, "reason": reason, "stake": stake}})
        return None
    figures = efbet_figures(stake, legs, rules)
    log.info("ticket %s: %d matches, odds %s on efbet now (I12 %s), stake %s, bonus %g%%, "
             "possible payout %s EUR", label, len(legs), figures["total_odds"],
             f"{product:.2f}" if isinstance(product, (int, float)) else product, stake,
             figures["bonus_percent"], figures["final_payout"],
             extra={"ctx": {**ctx, "odds": figures["total_odds"], "stake": stake, "efbet": figures}})
    previous = find_created(outcome_ids=[leg["selection_id"] for leg in legs])
    if previous:
        raise ticket_ledger.AlreadyCreated(sheet, previous)
    if dry_run:
        log.info("ticket %s is ready (check only, nothing created)", label,
                 extra={"ctx": {**ctx, "ready": True}})
        return True

    try:
        code = create_ticket(legs, stake)
    except ValueError as e:                         # efbet said no
        log.error("ticket %s not created: %s", label, e, extra={"ctx": {**ctx, "reason": str(e)}})
        return None
    run_folder = (run_id or _now_iso()).replace(":", "").replace("T", "_").replace("Z", "")
    path = os.path.join(config.QR_OUTPUT_DIR, run_folder, f"{label}.png")
    record = build_record(code, sheet, stake, legs, path, run_id, match_type, figures)
    # The booking exists on efbet from this moment: note it before anything else.
    ticket_ledger.note_in_progress(dict(record, _legs=legs))
    log.info("ticket %s: efbet booked it, number %s, reading it back...", label, code,
             extra={"ctx": {**ctx, "voucher": code}})

    booking = read_back(code)
    problems, notes = slip.check_booking(code, legs, stake, booking)
    for note in notes:
        log.info("ticket %s: %s", label, note, extra={"ctx": {**ctx, "voucher": code}})
    if problems:
        log.error("ticket %s (number %s) on efbet is not what was asked: %s. No barcode saved, do not use it.",
                  label, code, "; ".join(problems),
                  extra={"ctx": {**ctx, "voucher": code, "reason": "ticket on efbet differs", "problems": problems}})
        _reject(record, problems)
        return None
    record["verified"] = True
    generate_qr(code, path)
    ticket_ledger.append_ticket_record(record)
    ticket_ledger.clear_in_progress(code)
    log.info("ticket %s: number %s checked on efbet (%d matches, stake %s, odds %s, payout with bonus %s) "
             "-> barcode saved %s", label, code, len(legs), stake, figures["total_odds"], figures["final_payout"],
             path, extra={"ctx": {**ctx, "voucher": code, "qr": path, "stake": stake}})
    return path


# --- run ------------------------------------------------------------------------------------
def _wait(lo, hi, stop):
    wait = random.uniform(lo, hi)
    for _ in range(int(wait * 2)):
        if stop():
            break
        time.sleep(0.5)
    return wait


def run(**kwargs):
    """Make the efbet tickets of one type for one workbook (same call as the other robots' run)."""
    with _own_files():
        return _run(**kwargs)


def _run(excel=None, dry_run=False, sheets=None, cancel=None, min_interval=None, max_interval=None,
         on_ticket_done=None, run_id=None, wait_before_first=False, match_type=None, stake_rule=None,
         **_unused):
    stop = cancel or (lambda: False)
    match_type = int(match_type or config.DEFAULT_MATCH_TYPE)
    stake_rule = stake_rule or stake_rules.default_rule()
    stake_rules.check_rule(stake_rule)
    run_id = run_id or _now_iso()
    excel = excel or config.EXCEL_FILE
    log.info("using workbook: %s", excel, extra={"ctx": {"excel": excel}})
    log.info("efbet tickets: %d matches each (sheets %d-...) | stake: %s | %s", match_type, match_type,
             stake_rules.describe(stake_rule), "CHECK ONLY, nothing is created" if dry_run else "LIVE",
             extra={"ctx": {"match_type": match_type, "stake_rule": stake_rule, "check_only": dry_run}})

    tickets, bad, products = load_tickets_and_products(excel, sheets, match_type)
    for sheet, problem in stake_rules.stake_problems(stake_rule, products, list(tickets)).items():
        tickets.pop(sheet, None)
        bad.append((sheet, [problem]))
    if bad:
        log.error("workbook validation failed for %d sheet(s)", len(bad),
                  extra={"ctx": {"validation_errors": {str(s): e for s, e in bad}}})
        return {"tickets": tickets, "validation_errors": bad}
    for sheet, picks in tickets.items():
        pick_random_sides(ticket_label(match_type, sheet), picks)

    done, skipped, errored, already = [], [], [], []

    def summary():
        log.info("summary — %s: %s | not %s: %s | errored: %s | already created: %s",
                 "ready" if dry_run else "done", done or "-", "ready" if dry_run else "created",
                 skipped or "-", errored or "-", already or "-",
                 extra={"ctx": {"done": done, "skipped": skipped, "errored": errored,
                                "already_done": already, "check_only": dry_run}})
        return {"tickets": tickets, "validation_errors": bad, "done": done, "skipped": skipped,
                "errored": errored, "already_done": already}

    try:
        with swarm.connection(lambda message: log.info(message.strip())):
            if not dry_run:
                finish_pending()
            try:
                rules = swarm.bonus_rules()
            except SwarmError as e:
                rules = None
                log.warning("efbet's bonus rules could not be read (%s): the log shows the bonus from "
                            "the table of 25 Nov 2026", e)
            index = ticket_ledger.created_index()
            for sheet in list(tickets):
                previous = find_created(picks=tickets[sheet], index=index)
                if previous:
                    already.append(sheet)
                    tickets.pop(sheet)
                    log.warning("sheet %s: already created as ticket %s (number %s, %s), skipped, never made "
                                "twice", ticket_label(match_type, sheet), previous.get("label"),
                                previous.get("voucher"), previous.get("timestamp"),
                                extra={"ctx": {"sheet": sheet, "already_created": True,
                                               "voucher": previous.get("voucher")}})
                    if on_ticket_done and not dry_run:
                        on_ticket_done(sheet)
            todo = list(tickets)
            lo = min_interval if min_interval is not None else config.MIN_INTERVAL_SECONDS
            hi = max_interval if max_interval is not None else config.MAX_INTERVAL_SECONDS
            if not dry_run and wait_before_first and todo and not stop():
                log.info("waiting %.0fs before the first ticket...", _wait(lo, hi, stop))
            for i, sheet in enumerate(todo):
                if stop():
                    log.warning("run stopped by user before sheet %s", ticket_label(match_type, sheet),
                                extra={"ctx": {"sheet": sheet}})
                    break
                games_cache = {}                # fresh odds for every live ticket
                made = False
                try:
                    out = process_sheet(sheet, tickets[sheet], products.get(sheet), match_type, stake_rule,
                                        dry_run, run_id, games_cache, rules)
                    (done if out else skipped).append(sheet)
                    made = bool(out) and not dry_run
                    if made and on_ticket_done:
                        on_ticket_done(sheet)
                except ticket_ledger.AlreadyCreated as e:
                    already.append(sheet)
                    log.warning("sheet %s: these exact picks are already ticket %s (number %s), skipped, "
                                "never made twice", ticket_label(match_type, sheet), e.record.get("label"),
                                e.record.get("voucher"),
                                extra={"ctx": {"sheet": sheet, "already_created": True,
                                               "voucher": e.record.get("voucher")}})
                    if on_ticket_done and not dry_run:
                        on_ticket_done(sheet)
                except Exception as e:  # noqa: BLE001 - one bad sheet never stops the batch
                    log.error("sheet %s failed: %s", ticket_label(match_type, sheet), e, exc_info=True,
                              extra={"ctx": {"sheet": sheet, "reason": str(e)}})
                    errored.append(sheet)
                if i < len(todo) - 1 and not stop():
                    if made:
                        log.info("waiting %.0fs before the next ticket...", _wait(lo, hi, stop))
                    elif dry_run:
                        _wait(*CHECK_WAIT, stop)
    except SwarmError as e:
        log.critical("efbet could not be opened: %s", e, extra={"ctx": {"reason": str(e)}})
        errored.extend(s for s in tickets if s not in done + skipped + errored)
    return summary()


def selftest():
    """Extra checks for `TicketEfbet.exe --selftest`: barcode, booking and the browser."""
    from browser_session import BrowserSession
    assert barcode_efbet.decode(barcode_efbet.render("3535002")) == "3535002"
    leg = {"selection_id": 7474303803, "odds": 1.64}
    params = slip.booking_params([leg, dict(leg, selection_id=7474303854, odds=1.87)], 2)
    assert params["bets"][0]["BetType"] == 2 and params["bets"][0]["Amount"] == 2
    assert slip.total_odds([{"odds": o} for o in (1.64, 1.87, 1.42, 2.3, 1.65)]) == 16.526
    browser = BrowserSession("about:blank", "-", executable=swarm.BROWSER_EXECUTABLE)
    try:
        browser.launch_only()
    finally:
        browser.close()
