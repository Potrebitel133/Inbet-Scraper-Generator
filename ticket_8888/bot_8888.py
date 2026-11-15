"""
8888 ticket generator: one combined 8888 ticket (code + QR) per numbered sheet.

Same workbook layout, stake rules, window and never-twice record as the Inbet
robot (excel_reader, stake_rules, ticket_ledger, dashboard). New is how the
ticket is made on 8888 (slip_8888) and that every ticket is read back from 8888
and checked before its QR is saved.

For every numbered sheet:
  1. the sheet is read and checked (excel_reader). When Min Odd equals two sides
     (for example 2.40 / 2.40), one of them is picked at random (client's rule)
  2. skipped if the same ticket was made before (with either of the tied sides)
  3. every match is read from its 8888 match page: it must exist, match the
     sheet's names, not have started, and have its main (early payout) odds open
  4. stake from the ticket type's rule: fixed, or a formula with I12 / ODDS
  5. the ticket is created on 8888 exactly as the 8888 page does it -> code
  6. the ticket is read back from 8888 and compared: matches, picks, stake.
     Anything different -> no QR, the sheet is reported
  7. QR saved (it holds the code, like the QR on the 8888 page) and the ticket
     recorded in tickets_log_8888.json

"Check only" (dry_run) does steps 1 to 4 against the live site and creates nothing.

Everything goes through a hidden browser on 8888punkt.bg (the site's own key,
see scraper_8888/browser_session.py), so the PC needs a normal Bulgarian
connection, like the scraper.
"""

import contextlib
import itertools
import json
import logging
import os
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for folder in (HERE, HERE.parent, HERE.parent / "scraper_8888"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import config  # noqa: E402

# Own files, so the 8888 robot never mixes its tickets with the Inbet robot's,
# even when both programs sit in the same folder.
FILES_8888 = {
    "TICKETS_LOG": "tickets_log_8888.json",
    "IN_PROGRESS_FILE": "tickets_in_progress_8888.json",
    "SETTINGS_FILE": "settings_8888.json",
    "QR_OUTPUT_DIR": "qr_codes_8888",
    "EXCEL_FILE": "8888_tickets.xlsx",
    "LOG_FILE_NAME": "bot_8888.jsonl",
    "DEFAULT_MATCH_TYPE": 5,
}


def use_8888_files():
    """Point the shared modules (ticket_ledger, stake_rules, dashboard) at the 8888 files.
    The 8888 window calls this once at start; run() does it for its own duration."""
    for key, value in FILES_8888.items():
        setattr(config, key, value)


@contextlib.contextmanager
def _own_files():
    saved = {key: getattr(config, key, None) for key in FILES_8888}
    use_8888_files()
    try:
        yield
    finally:
        for key, value in saved.items():
            setattr(config, key, value)

import qrcode  # noqa: E402

import excel_reader  # noqa: E402
import scraper_8888  # noqa: E402
import slip_8888 as slip  # noqa: E402
import stake_rules  # noqa: E402
import ticket_ledger  # noqa: E402
from scraper_8888 import ScrapeError  # noqa: E402

log = logging.getLogger("bot.8888")

SUPPORTS_CHECK = True                 # the window shows "Check only"
REJECTED_LOG = "tickets_rejected_8888.json"
ACCA_FILE = "acca_bonus_8888.json"
CHECK_WAIT = (1.0, 2.5)               # seconds between sheets in "Check only"
READ_BACK_TRIES = 3
SIDES = ("home", "draw", "away")
ODDS_KEY = {"home": "home_odd", "draw": "draw_odd", "away": "away_odd"}


# --- names, QR, records ---------------------------------------------------------------
def ticket_label(match_type, sheet):
    """'5-17' = sheet 17 of a 5-match run. Names the QR file and the ticket everywhere."""
    return f"{match_type}-{sheet}"


def generate_qr(code, out_path):
    """The QR the cashier scans: it holds the ticket code, exactly like the QR on the 8888 page."""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    qrcode.make(str(code)).save(out_path)
    return out_path


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def build_record(code, sheet, stake, odds, legs, qr_path, run_id, match_type, detail=None):
    return {
        "voucher": code, "sheet": sheet, "stake": slip.money(stake), "match_type": match_type,
        "label": ticket_label(match_type, sheet), "combined_odds": round(odds, 2),
        "qr": qr_path, "run_id": run_id, "timestamp": _now_iso(), "site": "8888",
        "verified": detail is not None,
        "on_8888": {k: detail.get(k) for k in ("total_odds", "acca_percent", "total_win")} if detail else None,
        "selections": [{
            "match": leg["match"], "match_id": leg["match_id"], "side": leg["side"],
            "pick": leg["pick"], "odds": leg["odds"], "outcomeId": leg["selection_id"],
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


# --- ties: the same odd on two sides ----------------------------------------------------
def tied_sides(pick):
    """Every side whose odd equals Min Odd (normally one)."""
    mo = excel_reader._num(pick.get("min_odd"))
    if mo is None:
        return [pick["side"]]
    sides = [s for s in SIDES if excel_reader._num(pick.get(ODDS_KEY[s])) is not None
             and abs(excel_reader._num(pick.get(ODDS_KEY[s])) - mo) < 1e-9]
    return sides or [pick["side"]]


def pick_random_sides(sheet, picks, rng=random):
    """Client's rule: when Min Odd equals two sides, take one of them at random."""
    for p in picks:
        sides = tied_sides(p)
        if len(sides) > 1:
            p["side"] = rng.choice(sides)
            p["tie"] = sides
            names = {"home": "1", "draw": "X", "away": "2"}
            log.info("sheet %s: %s has the same odd %s for %s, picked %s at random", sheet, p["match"],
                     p["min_odd"], " and ".join(names[s] for s in sides), names[p["side"]],
                     extra={"ctx": {"sheet": sheet, "match": p["match"], "random_pick": names[p["side"]]}})
    return picks


def find_created(picks=None, outcome_ids=None, index=None):
    """The earlier ticket with this combination, or None.

    A tied pick counts with either side, so running a sheet again never makes
    the same ticket with the other side.
    """
    index = ticket_ledger.created_index() if index is None else index
    if outcome_ids is not None:
        found = ticket_ledger.find_created(outcome_ids=outcome_ids, index=index)
        if found:
            return found
    if not picks:
        return None
    options = [tied_sides(p) for p in picks]
    for combo in itertools.product(*options):
        found = ticket_ledger.find_created(picks=[dict(p, side=s) for p, s in zip(picks, combo)], index=index)
        if found:
            return found
    return None


# --- ACCA bonus sizes (only for the page's display copy of the winnings) ------------------
def acca_percent(count):
    try:
        with open(ACCA_FILE, encoding="utf-8") as f:
            learned = {int(k): v for k, v in json.load(f).items()}
    except (OSError, ValueError, AttributeError):
        learned = {}
    return learned.get(count, slip.KNOWN_ACCA_PERCENT.get(count, 0))


def remember_acca(count, percent):
    if percent is None or acca_percent(count) == percent:
        return
    try:
        with open(ACCA_FILE, encoding="utf-8") as f:
            learned = json.load(f)
    except (OSError, ValueError):
        learned = {}
    learned[str(count)] = percent
    ticket_ledger.write_json_atomic(ACCA_FILE, learned)


# --- 8888 ---------------------------------------------------------------------------------
def fetch_event(pick):
    code = str(pick.get("match_id") or "").lstrip("#").strip()
    try:
        return scraper_8888.fetch_json(f"v2/widgets/events/{code}")
    except ScrapeError as e:
        if "404" in str(e):
            raise slip.PickError("not found on 8888 (the match was removed or is over)")
        raise


def create_ticket(legs, stake):
    count = len(legs)
    body = slip.build_slip(legs, stake, acca_percent=acca_percent(count))
    reply = scraper_8888.post_json("v2/bet/slip-data", body)
    return slip.slip_code(reply)


def read_back(code):
    """The ticket as 8888 stored it (8888's own ticket page)."""
    last = None
    for attempt in range(READ_BACK_TRIES):
        try:
            reply = scraper_8888.post_site_form("tickets/detail/", {
                "skip_pager": 1, "code": code, "is_ajax": 1, "cache": int(time.time() * 1000)})
            return slip.parse_ticket_detail(reply)
        except (ScrapeError, ValueError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise ScrapeError(f"could not read ticket {code} back from 8888 ({last})")


def _reject(record, problems):
    record = dict(record, rejected=problems, rejected_at=_now_iso())
    _append_json_list(REJECTED_LOG, record)
    ticket_ledger.clear_in_progress(record["voucher"])


def finish_pending():
    """Tickets whose code arrived but whose QR/record was cut off (closed or crashed).

    Each one is read back and checked like a new ticket, then finished (QR +
    record) so it counts as made and is never made again, or rejected.
    Needs the open 8888 connection, so it runs inside run().
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
            detail = read_back(code)
        except ScrapeError as e:
            log.error("ticket %s (code %s) was cut off and cannot be checked now: %s. It stays "
                      "pending and is checked on the next run.", record.get("label"), code, e,
                      extra={"ctx": {"sheet": record.get("sheet"), "voucher": code}})
            continue
        problems, _ = slip.check_ticket(code, legs, record["stake"], detail)
        if problems:
            log.error("ticket %s (code %s) was cut off and is not what was asked on 8888: %s. "
                      "No QR saved.", record.get("label"), code, "; ".join(problems),
                      extra={"ctx": {"sheet": record.get("sheet"), "voucher": code, "problems": problems}})
            _reject(record, problems)
            continue
        record = {k: v for k, v in record.items() if k != "_legs"}
        record.update(verified=True, on_8888={k: detail.get(k) for k in ("total_odds", "acca_percent",
                                                                           "total_win")})
        generate_qr(code, record["qr"])
        ticket_ledger.append_ticket_record(record)
        ticket_ledger.clear_in_progress(code)
        finished.append(record)
        log.warning("ticket %s (code %s) was cut off last time: checked on 8888 and finished now, "
                    "it will not be made again", record.get("label"), code,
                    extra={"ctx": {"sheet": record.get("sheet"), "voucher": code, "recovered": True}})
    return finished


def recover_interrupted():
    """For the window: nothing to do before a run. Cut-off tickets are checked on 8888
    and finished at the start of the next run (finish_pending)."""
    return []


# --- one sheet ----------------------------------------------------------------------------
def _legs_for(sheet, picks, ctx, events_cache=None):
    legs, problems = [], []
    for p in picks:
        key = str(p.get("match_id"))
        try:
            event = events_cache.get(key) if events_cache is not None else None
            if event is None:
                event = fetch_event(p)
                if events_cache is not None:
                    events_cache[key] = event
            leg = slip.resolve_pick(event, p)
            leg["tie"] = p.get("tie")
            legs.append(leg)
            mo = excel_reader._num(p.get("min_odd"))
            if mo is not None and abs(leg["odds"] - mo) > 1e-9:
                log.info("sheet %s: %s %s odds %s in the sheet, %s on 8888 now", sheet, p["match"],
                         leg["pick"], p["min_odd"], leg["odds"],
                         extra={"ctx": {**ctx, "match": p["match"], "excel_odds": mo, "live_odds": leg["odds"]}})
        except (slip.PickError, ScrapeError) as e:
            problems.append(str(e))
            log.warning("sheet %s: %s (%s): %s", sheet, p["match"], p["match_id"], e,
                        extra={"ctx": {**ctx, "match": p["match"], "match_id": p["match_id"],
                                       "tournament": p.get("tournament"), "reason": str(e)}})
    return legs, problems


def process_sheet(sheet, picks, product, match_type, stake_rule, dry_run, run_id, events_cache=None):
    """Make (or, in "Check only", check) the ticket of one sheet -> QR path / True, or None if not possible."""
    label = ticket_label(match_type, sheet)
    ctx = {"sheet": sheet, "label": label}
    legs, problems = _legs_for(sheet, picks, ctx, events_cache)
    if problems:
        log.warning("sheet %s: ticket %s not %s: %d match(es) cannot go on it", sheet, label,
                    "ready" if dry_run else "created", len(problems), extra={"ctx": ctx})
        return None
    odds = slip.total_odds(legs)
    stake = stake_rules.compute_stake(stake_rule, i12=product, odds=odds)
    log.info("sheet %s: ticket %s, %d matches, odds %.2f on 8888 now (I12 %s), stake %s", sheet, label,
             len(legs), odds, f"{product:.2f}" if isinstance(product, (int, float)) else product, stake,
             extra={"ctx": {**ctx, "odds": round(odds, 4), "stake": stake}})
    previous = find_created(outcome_ids=[leg["selection_id"] for leg in legs])
    if previous:
        raise ticket_ledger.AlreadyCreated(sheet, previous)
    if dry_run:
        log.info("sheet %s: ticket %s is ready (check only, nothing created)", sheet, label,
                 extra={"ctx": {**ctx, "ready": True}})
        return True

    try:
        code = create_ticket(legs, stake)
    except ValueError as e:                         # 8888 said no (limit, suspended ...)
        log.error("sheet %s: ticket %s not created: %s", sheet, label, e,
                  extra={"ctx": {**ctx, "reason": str(e)}})
        return None
    run_folder = (run_id or _now_iso()).replace(":", "").replace("T", "_").replace("Z", "")
    qr_path = os.path.join(config.QR_OUTPUT_DIR, run_folder, f"{label}.png")
    record = build_record(code, sheet, stake, odds, legs, qr_path, run_id, match_type)
    # The ticket exists on 8888 from this moment: note it before anything else.
    ticket_ledger.note_in_progress(dict(record, _legs=legs))
    log.info("sheet %s: 8888 created ticket %s, code %s, reading it back...", sheet, label, code,
             extra={"ctx": {**ctx, "voucher": code}})

    detail = read_back(code)
    problems, notes = slip.check_ticket(code, legs, stake, detail)
    for note in notes:
        log.info("ticket %s: %s", label, note, extra={"ctx": {**ctx, "voucher": code}})
    if problems:
        log.error("ticket %s (code %s) on 8888 is not what was asked: %s. No QR saved, do not use it.",
                  label, code, "; ".join(problems),
                  extra={"ctx": {**ctx, "voucher": code, "reason": "ticket on 8888 differs", "problems": problems}})
        _reject(record, problems)
        return None
    remember_acca(len(legs), detail.get("acca_percent"))
    record.update(verified=True, on_8888={k: detail.get(k) for k in ("total_odds", "acca_percent", "total_win")})
    generate_qr(code, qr_path)
    ticket_ledger.append_ticket_record(record)
    ticket_ledger.clear_in_progress(code)
    log.info("ticket %s: code %s checked on 8888 (%d matches, stake %s, odds %s, total win %s) -> QR saved %s",
             label, code, len(legs), detail.get("stake"), detail.get("total_odds"), detail.get("total_win"),
             qr_path, extra={"ctx": {**ctx, "voucher": code, "qr": qr_path, "stake": stake}})
    return qr_path


# --- run ------------------------------------------------------------------------------------
def _wait(lo, hi, stop):
    wait = random.uniform(lo, hi)
    for _ in range(int(wait * 2)):
        if stop():
            break
        time.sleep(0.5)
    return wait


def run(**kwargs):
    """Make the 8888 tickets for one workbook (same call as the Inbet robot's bot.run)."""
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
    log.info("8888 tickets: %d matches each | stake: %s | %s", match_type, stake_rules.describe(stake_rule),
             "CHECK ONLY, nothing is created" if dry_run else "LIVE",
             extra={"ctx": {"match_type": match_type, "stake_rule": stake_rule, "check_only": dry_run}})

    tickets, bad, products = excel_reader.load_tickets_and_products(excel, sheets, match_type)
    for sheet, problem in stake_rules.stake_problems(stake_rule, products, list(tickets)).items():
        tickets.pop(sheet, None)
        bad.append((sheet, [problem]))
    if bad:
        log.error("workbook validation failed for %d sheet(s)", len(bad),
                  extra={"ctx": {"validation_errors": {str(s): e for s, e in bad}}})
        return {"tickets": tickets, "validation_errors": bad}
    for sheet, picks in tickets.items():
        pick_random_sides(sheet, picks)

    done, skipped, errored, already = [], [], [], []

    def summary():
        log.info("summary — %s: %s | not %s: %s | errored: %s | already created: %s",
                 "ready" if dry_run else "done", done or "-", "ready" if dry_run else "created",
                 skipped or "-", errored or "-", already or "-",
                 extra={"ctx": {"done": done, "skipped": skipped, "errored": errored,
                                "already_done": already, "check_only": dry_run}})
        return {"tickets": tickets, "validation_errors": bad, "done": done, "skipped": skipped,
                "errored": errored, "already_done": already}

    def browser_log(message):
        log.info(message.strip())

    try:
        with scraper_8888.odds_connection(browser_log):
            if not dry_run:
                finish_pending()
            index = ticket_ledger.created_index()
            for sheet in list(tickets):
                previous = find_created(picks=tickets[sheet], index=index)
                if previous:
                    already.append(sheet)
                    tickets.pop(sheet)
                    log.warning("sheet %s: already created as ticket %s (code %s, %s), skipped, never made twice",
                                sheet, previous.get("label"), previous.get("voucher"), previous.get("timestamp"),
                                extra={"ctx": {"sheet": sheet, "already_created": True,
                                               "voucher": previous.get("voucher")}})
                    if on_ticket_done and not dry_run:
                        on_ticket_done(sheet)
            todo = list(tickets)
            events_cache = {} if dry_run else None
            lo = min_interval if min_interval is not None else config.MIN_INTERVAL_SECONDS
            hi = max_interval if max_interval is not None else config.MAX_INTERVAL_SECONDS
            if not dry_run and wait_before_first and todo and not stop():
                log.info("waiting %.0fs before the first ticket...", _wait(lo, hi, stop))
            for i, sheet in enumerate(todo):
                if stop():
                    log.warning("run stopped by user before sheet %s", sheet, extra={"ctx": {"sheet": sheet}})
                    break
                made = False
                try:
                    out = process_sheet(sheet, tickets[sheet], products.get(sheet), match_type, stake_rule,
                                        dry_run, run_id, events_cache)
                    (done if out else skipped).append(sheet)
                    made = bool(out) and not dry_run
                    if made and on_ticket_done:
                        on_ticket_done(sheet)
                except ticket_ledger.AlreadyCreated as e:
                    already.append(sheet)
                    log.warning("sheet %s: these exact picks are already ticket %s (code %s), skipped, "
                                "never made twice", sheet, e.record.get("label"), e.record.get("voucher"),
                                extra={"ctx": {"sheet": sheet, "already_created": True,
                                               "voucher": e.record.get("voucher")}})
                    if on_ticket_done and not dry_run:
                        on_ticket_done(sheet)
                except Exception as e:  # noqa: BLE001 - one bad sheet never stops the batch
                    log.error("sheet %s failed: %s", sheet, e, exc_info=True,
                              extra={"ctx": {"sheet": sheet, "reason": str(e)}})
                    errored.append(sheet)
                if i < len(todo) - 1 and not stop():
                    if made:
                        log.info("waiting %.0fs before the next ticket...", _wait(lo, hi, stop))
                    elif dry_run:
                        _wait(*CHECK_WAIT, stop)
    except ScrapeError as e:
        log.critical("8888 could not be opened: %s", e, extra={"ctx": {"reason": str(e)}})
        errored.extend(s for s in tickets if s not in done + skipped + errored)
    return summary()


def selftest():
    """Extra checks for `Ticket8888.exe --selftest`: the browser can be driven and the betslip builds."""
    from browser_session import BrowserSession
    leg = {"selection_id": "1", "sport_id": 66, "sport": "football", "event_id": 1, "odds": 2.0,
           "market_name": "Краен Резултат", "market_type_name": "Краен Резултат", "home": "A", "away": "B",
           "selection_name": "1", "bonus_types": []}
    body = slip.build_slip([leg, dict(leg, selection_id="2")], 10, acca_percent=0)
    assert json.loads(body["data"])[0]["bets"]["entities"] and body["betSlip"]["bets"][0]["betAmount"] == 10
    browser = BrowserSession("about:blank", "-", executable=scraper_8888.BROWSER_EXECUTABLE)
    try:
        browser.launch_only()
    finally:
        browser.close()
