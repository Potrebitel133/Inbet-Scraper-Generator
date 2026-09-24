"""
Combo-ticket bot orchestrator — connects every step end to end.

For each numbered Excel sheet (one 10-fold accumulator):
  1. read the 10 picks + chosen side (Min Odd)          [resolver.read_ticket]
  2. resolve tournament ids                              [tournaments.json]
  3. fetch games and resolve each pick -> outcome id     [games API / cache]
  4. create-bets to validate the slip                    [common.create_bets]
  5. stake = ceil(50000 / combined_odds)                 [resolver.stake_for]
  6. create-draft to build the ticket                    [common.create_draft]
  7. save the QR code                                    [generate_qr]
  8. wait 60-70s, next ticket                            [anti-fingerprinting]

Run modes:
  python bot.py                 # live: hits the API (needs Bulgarian IP + tokens)
  python bot.py --dry-run       # resolve + build payloads from cached games,
                                #   write them to ./drafts, no network POSTs
  python bot.py --sheets 1 2    # only these ticket sheets
"""

import argparse
import json
import logging
import os
import random
import time

import qrcode

import common
import config
import excel_reader
import logsetup
import resolver

log = logging.getLogger("bot")


# --- QR ----------------------------------------------------------------------
VOUCHER_KEYS = ("code", "betCode", "voucher", "voucherCode", "shortCode", "barcode", "id")


def extract_voucher(draft_response):
    """Best-effort pull of the voucher/betslip code from a create-draft response.

    The exact field is unconfirmed (geoblocked), so we search a few likely keys
    at the top level and one level down. Returns the code string or None.
    """
    if not isinstance(draft_response, dict):
        return None
    for key in VOUCHER_KEYS:
        val = draft_response.get(key)
        if isinstance(val, (str, int)):
            return str(val)
    for val in draft_response.values():
        if isinstance(val, dict):
            found = extract_voucher(val)
            if found:
                return found
    return None


def generate_qr(voucher, out_path):
    """Save the kiosk QR for a voucher code (site prefixes voucher with 'N')."""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    qrcode.make("N" + str(voucher)).save(out_path)
    return out_path


def save_ticket_record(voucher, sheet, stake, odds, picks, selections, qr_path, run_id=None):
    """Append a readable record of the ticket's bets to the local ledger."""
    record = {
        "voucher": str(voucher), "sheet": sheet, "stake": stake,
        "combined_odds": round(float(odds), 4), "qr": qr_path,
        "run_id": run_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "selections": [
            {"match": p["match"], "side": p["side"], "odds": s["odds"], "outcomeId": s["outcomeId"]}
            for p, s in zip(picks, selections)
        ],
    }
    log = []
    if os.path.exists(config.TICKETS_LOG):
        try:
            with open(config.TICKETS_LOG, encoding="utf-8") as f:
                log = json.load(f)
        except (ValueError, OSError):
            log = []
    log.append(record)
    with open(config.TICKETS_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    return record


# --- draft payload -----------------------------------------------------------
def build_draft_payload(selections, stake):
    """Assemble the create-draft body: 10 selections combined into one bet."""
    system = config.FOLD_SYSTEM.get(len(selections))
    if not system:
        raise ValueError(f"no accumulator system name for {len(selections)} selections")
    return {
        "bets": [
            {
                "stake": {"value": stake},
                "selectedSystem": system,
                "selectionRefs": [
                    {"selectionIndex": i, "banker": False} for i in range(len(selections))
                ],
                "isFreeBet": False,
            }
        ],
        "selections": selections,
        "sender": {"id": "0", "channel": "internet"},
        "oddsChange": "higher",
        "language": "bg",
        "placeType": None,
        "stakeType": "BET_STAKE",
    }


# --- games source (live API or cached file) ----------------------------------
class GamesProvider:
    """Fetches games per tournament, caching by tournament id for the run."""

    def __init__(self, offline_file=None, dump_api=False):
        self.offline_file = offline_file
        self.dump_api = dump_api
        self._cache = {}          # tid -> raw leagueview JSON
        self._index_cache = {}    # tid -> built event index (list of entries)

    def get(self, tournament_id):
        if tournament_id in self._cache:
            return self._cache[tournament_id]
        if self.offline_file:
            with open(self.offline_file, encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = common.fetch_tournament_games(tournament_id)
        if self.dump_api:
            os.makedirs("logs/api", exist_ok=True)
            path = os.path.join("logs", "api", f"games_{tournament_id}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log.info("dumped raw games response -> %s", path, extra={"ctx": {"tournament": tournament_id}})
        self._cache[tournament_id] = data
        return data

    def get_many(self, tournament_ids, chunk=50):
        """Games for several tournaments at once — the API takes a comma list.

        Used by the Match-ID fallback so sweeping a shortlist (or every
        tournament) costs a handful of requests, not one per id.
        """
        ids = [t for t in tournament_ids if t not in self._cache]
        for i in range(0, len(ids), chunk):
            batch = ids[i:i + chunk]
            if self.offline_file:
                data = self.get(batch[0])
            else:
                data = common.fetch_tournament_games(batch)
            # One response covers the whole batch; cache it under every id in it
            # so a later single get() is a hit rather than a refetch.
            for tid in batch:
                self._cache[tid] = data
        return [self._cache[t] for t in tournament_ids if t in self._cache]

    def get_event_index(self, tournament_id, fetch_event_markets=None):
        """Build (and cache) the resolved event index for one tournament.

        fetch_event_markets is called at most once per Enhanced-only event in
        the tournament across the entire run — never once per pick.  All
        subsequent calls for the same tournament_id return the already-built
        list instantly without any network activity.
        """
        if tournament_id not in self._index_cache:
            raw = self.get(tournament_id)
            self._index_cache[tournament_id] = resolver.build_event_index(
                raw, fetch_event_markets
            )
        return self._index_cache[tournament_id]


# --- Match ID fallback -------------------------------------------------------
def resolve_unresolved_tournaments(tickets, tournament_index, games_provider):
    """{excel_name: tournament_id} for Excel tournament names no path matches.

    The site spells one tournament differently per endpoint — id 19000000007 is
    "international-clubs-uefa-champions-league" in the sports tree we index, but
    "Champions League" in the leagueview response and "championship-league" in
    the UI url. So a correct-looking Excel name can resolve to nothing and every
    row under it gets skipped.

    Recovery: fuzzy-match the name to a shortlist, then keep only the candidate
    whose fixtures contain EVERY Match ID that name carries. Match IDs are
    grouped across the WHOLE workbook, so each name is resolved once against the
    largest available set of Match IDs — and later sheets reuse that id instead
    of re-resolving.
    """
    groups = {}
    for picks in tickets.values():
        for pick in picks:
            name = pick.get("tournament")
            if not name or pick.get("tournament_id"):
                continue                      # empty, or the row already names its tournament id
            if resolver.resolve_tournament_ids(name, tournament_index):
                continue                      # already exact
            groups.setdefault(name, set()).add(resolver.match_id_code(pick))
    if not groups:
        return {}

    def fetch_codes(ids):
        return {tid: resolver.event_codes(games) for tid, games in
                zip(ids, games_provider.get_many(ids))}

    resolved = {}
    for name, codes in groups.items():
        tid, detail = resolver.resolve_by_match_ids(name, codes, tournament_index, fetch_codes)
        if tid:
            resolved[name] = tid
            log.warning("tournament %r does not match any tournament name — resolved to id %s "
                        "by matching %d/%d Match IDs; reused for every sheet%s",
                        name, tid, detail["matched"], detail["total"],
                        f" (not listed there: {', '.join('#' + c for c in detail['missing'])})"
                        if detail.get("missing") else "",
                        extra={"ctx": {"tournament": name, "tournamentId": tid, **detail}})
        else:
            log.error("tournament %r not found: %s", name, detail.get("reason"),
                      extra={"ctx": {"tournament": name, **detail}})
    return resolved


# --- one ticket --------------------------------------------------------------
def process_ticket(sheet, picks, tournament_index, games_provider, dry_run, run_id=None,
                   tournament_overrides=None):
    ctx = {"sheet": sheet}
    log.info("ticket sheet %s: %d picks", sheet, len(picks), extra={"ctx": ctx})
    overrides = tournament_overrides or {}

    # In --dry-run (or any offline/cached run), never hit eventview live —
    # cached games files won't have it and dry-run must stay network-free.
    fetch_event_markets = None if (dry_run or games_provider.offline_file) else common.fetch_event_markets

    selections, missing = [], []
    for pick in picks:
        code = resolver.match_id_code(pick)
        entry = None

        # --- Stage 1: fast path (shortCode + tournament_id from the scraper) ---
        # The sheet carries an exact tournament_id for this row. Find the event
        # in one O(n) scan by shortCode, then call eventview for ONLY that event
        # if it turns out to be Enhanced-only. Cost: at most 1 eventview call per
        # pick — never one call per event in the tournament.
        if pick.get("tournament_id"):
            tid = int(pick["tournament_id"])
            raw = games_provider.get(tid)
            entry = resolver.resolve_event_for_pick(raw, pick, fetch_event_markets)

        # --- Stage 2: fallback — name-based resolution (unchanged behaviour) ---
        # Reached when:
        #   • the row has no tournament_id (hand-built sheet without scraper IDs)
        #   • Stage 1 found no event with that shortCode (event moved tournament)
        if not entry:
            if pick.get("tournament_id"):
                tids = [int(pick["tournament_id"])]
            else:
                tids = resolver.resolve_tournament_ids(pick["tournament"], tournament_index)
                if not tids and pick["tournament"] in overrides:
                    tids = [overrides[pick["tournament"]]]
            # A slug can map to several tournament ids. Prefer a hit that matched
            # on the Excel "Match ID" (the site's shortCode — exact); only fall
            # back to a name-matched hit if no id gives a code match.
            fallback = None
            for tid in tids:
                # get_event_index builds the index once per tournament per run
                # and caches it — eventview sub-calls happen at most once per
                # event, never once per pick.
                events = games_provider.get_event_index(tid, fetch_event_markets)
                hit = resolver.find_event(pick, events)
                if not hit:
                    continue
                if code and hit.get("short_code") == code:
                    entry = hit
                    break
                fallback = fallback or hit
            if not entry:
                entry = fallback

        if not entry:
            reason = "event not found" if pick.get("tournament_id") else (
                "tournament not found" if not tids else "event not found"
            )
            missing.append((pick, reason))
            continue
        selection, outcome_id = resolver.build_selection(pick, entry)
        if not selection:
            missing.append((pick, "outcome inactive/missing"))
            continue
        market_name = entry["market"].get("marketName")
        enhanced_skipped = entry.get("enhanced_skipped") or []
        note = f"  (skipped Enhanced: {enhanced_skipped})" if enhanced_skipped else ""
        log.info("  %-40s %-5s @ %s  [%s]%s -> %s",
                 pick["match"], pick["side"], selection["odds"], market_name, note, outcome_id,
                 extra={"ctx": {**ctx, "match": pick["match"], "side": pick["side"],
                                "market": market_name, "enhanced_skipped": enhanced_skipped,
                                "outcomeId": outcome_id}})
        selections.append(selection)

    for pick, reason in missing:
        log.warning("SKIP %s (%s) [%s]: %s", pick["match"], pick["side"], pick["tournament"], reason,
                    extra={"ctx": {**ctx, "match": pick["match"], "match_id": pick.get("match_id"),
                                   "side": pick["side"], "tournament": pick["tournament"], "reason": reason}})

    if len(selections) != len(picks):
        log.warning("only %d/%d resolved — not building a partial ticket", len(selections), len(picks),
                    extra={"ctx": {**ctx, "resolved": len(selections), "total": len(picks)}})
        return None

    total_odds = resolver.combined_odds(selections)
    stake = resolver.stake_for(total_odds)
    log.info("combined odds = %.4f -> stake = %d", total_odds, stake,
             extra={"ctx": {**ctx, "odds": round(total_odds, 4), "stake": stake}})

    payload = build_draft_payload(selections, stake)

    if dry_run:
        os.makedirs("drafts", exist_ok=True)
        out = os.path.join("drafts", f"draft_sheet_{sheet}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log.info("[dry-run] wrote draft payload -> %s (no network)", out, extra={"ctx": ctx})
        return out

    # Live: validate the slip. The server returns fully-enriched outcome objects;
    # the create-draft selections must be those (not our hand-built ones) or it
    # errors RT_500.
    cb = common.create_bets([s["outcomeId"] for s in selections])
    enriched = resolver.enrich_selections_from_bets(cb)
    if len(enriched) != len(selections):
        log.warning("create-bets returned %d outcomes, expected %d — skipping", len(enriched), len(selections),
                    extra={"ctx": {**ctx, "got": len(enriched), "expected": len(selections)}})
        return None
    live_odds = resolver.nfold_odds(cb, len(enriched)) or total_odds
    stake = resolver.stake_for(live_odds)
    log.info("live combined odds = %.4f -> stake = %d", live_odds, stake,
             extra={"ctx": {**ctx, "odds": round(live_odds, 4), "stake": stake}})
    draft = common.create_draft(build_draft_payload(enriched, stake))
    voucher = extract_voucher(draft)
    if not voucher:
        os.makedirs("drafts", exist_ok=True)
        resp_path = f"drafts/draft_response_sheet_{sheet}.json"
        with open(resp_path, "w", encoding="utf-8") as f:
            json.dump(draft, f, ensure_ascii=False, indent=2)
        log.error("no voucher in create-draft response — saved %s", resp_path,
                  extra={"ctx": {**ctx, "response_saved": resp_path}})
        return None
    # Per-run subfolder so each run keeps its own 10 QRs instead of overwriting
    # the last run's. run_id has colons (invalid in Windows paths) — strip them.
    run_folder = (run_id or "misc").replace(":", "").replace("T", "_").replace("Z", "")
    qr_path = os.path.join(config.QR_OUTPUT_DIR, run_folder, f"ticket_sheet_{sheet}.png")
    generate_qr(voucher, qr_path)
    save_ticket_record(voucher, sheet, stake, live_odds, picks, selections, qr_path, run_id)
    os.makedirs("drafts", exist_ok=True)
    with open(f"drafts/ticket_{voucher}.json", "w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)
    log.info("voucher %s -> QR saved %s", voucher, qr_path,
             extra={"ctx": {**ctx, "voucher": str(voucher), "stake": stake, "qr": qr_path}})
    return qr_path


# --- run loop ----------------------------------------------------------------
def run(excel=None, dry_run=False, sheets=None, offline_games=None, dump_api=False,
        cancel=None, stake_budget=None, min_interval=None, max_interval=None,
        on_ticket_done=None, run_id=None, wait_before_first=False):
    """Generate tickets for one workbook. Safe to call in-process (the GUI does).

    Logs everything via `logsetup`; the caller sets logging up once.
    `cancel`          optional callable -> bool; True stops cleanly before the next ticket.
    `stake_budget`    overrides config.STAKE_BUDGET for this run (stake = ceil(budget/odds)).
    `min_interval`    seconds (int/float) — lower bound of the wait between tickets.
                      Overrides config.MIN_INTERVAL_SECONDS for this run only.
    `max_interval`    seconds (int/float) — upper bound. Overrides config.MAX_INTERVAL_SECONDS.
    `on_ticket_done`  optional callable(sheet) invoked on the worker thread after each
                      successful ticket; used by the dashboard to track resume state.
    `run_id`          optional ISO timestamp string; pass the same id on resume so all
                      sheets from one session share a single run_id in tickets_log.json.
    `wait_before_first` when True, apply the same anti-fingerprinting interval
                      wait BEFORE the first ticket too. A fresh run doesn't need
                      this (nothing has happened yet), but resuming after a Stop
                      does — otherwise the ticket right after Resume fires with
                      no gap since the last one before the pause, defeating the
                      pacing. Interruptible by `cancel` like every other wait.
    """
    excel = excel or config.EXCEL_FILE
    sheets = sheets if sheets is not None else None
    stop = cancel or (lambda: False)
    if stake_budget is not None:
        if float(stake_budget) <= 0 or float(stake_budget) > config.MAX_STAKE_BUDGET:
            raise ValueError(f"Stake budget must be between 1 and {config.MAX_STAKE_BUDGET:,}")
        config.STAKE_BUDGET = float(stake_budget)
    log.info("stake budget: %s (stake = ceil(budget / combined odds))", config.STAKE_BUDGET,
             extra={"ctx": {"stake_budget": config.STAKE_BUDGET}})
    run_id = run_id or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())  # groups this run's tickets

    # Workbook validation must happen before any live API request.
    log.info("using workbook: %s", excel, extra={"ctx": {"excel": excel}})
    tickets, bad = excel_reader.load_all_tickets(excel, sheets)
    if bad:
        log.error("workbook validation failed for %d sheet(s)", len(bad),
                  extra={"ctx": {"validation_errors": {str(s): e for s, e in bad}}})
        return {"tickets": tickets, "validation_errors": bad}

    if dry_run:
        offline_file = offline_games or "tournaments_games_with_bet.json"
        with open(config.TOURNAMENTS_FILE, encoding="utf-8") as f:
            tournament_index = resolver.build_tournament_index(json.load(f))
        log.info("loaded %d tournaments (cache)", len(tournament_index))
    else:
        offline_file = offline_games
        log.info("fetching tournaments (live)...")
        tournament_index = resolver.build_tournament_index(common.fetch_sports())
        log.info("loaded %d tournaments", len(tournament_index))

    games_provider = GamesProvider(offline_file=offline_file, dump_api=dump_api)

    # Recover Excel tournament names that match no path, once for the whole
    # workbook, by matching their Match IDs against the fuzzy candidates.
    overrides = resolve_unresolved_tournaments(tickets, tournament_index, games_provider)

    valid_sheets = list(tickets)

    def anti_fingerprint_wait():
        lo = min_interval if min_interval is not None else config.MIN_INTERVAL_SECONDS
        hi = max_interval if max_interval is not None else config.MAX_INTERVAL_SECONDS
        wait = random.uniform(lo, hi)
        log.info("waiting %.0fs before next ticket...", wait)
        # Interruptible wait: check `stop` twice a second instead of one long sleep.
        for _ in range(int(wait * 2)):
            if stop():
                break
            time.sleep(0.5)

    done, skipped, errored = [], [s for s, _ in bad], []
    if not dry_run and wait_before_first and valid_sheets and not stop():
        anti_fingerprint_wait()
    for i, sheet in enumerate(valid_sheets):
        if stop():
            log.warning("run stopped by user before sheet %s", sheet, extra={"ctx": {"sheet": sheet}})
            break
        try:
            out = process_ticket(sheet, tickets[sheet], tournament_index, games_provider,
                                  dry_run=dry_run, run_id=run_id,
                                  tournament_overrides=overrides)
            (done if out else skipped).append(sheet)
            if out and on_ticket_done:
                on_ticket_done(sheet)  # notify dashboard so it can track resume state
        except Exception as e:
            # Isolate failures so one bad ticket doesn't abort the whole batch.
            log.error("ticket sheet %s failed: %s", sheet, e, exc_info=True,
                      extra={"ctx": {"sheet": sheet}})
            errored.append(sheet)
        if not dry_run and i < len(valid_sheets) - 1:
            anti_fingerprint_wait()

    log.info("summary — done: %s | skipped: %s | errored: %s",
             done or "-", skipped or "-", errored or "-",
             extra={"ctx": {"done": done, "skipped": skipped, "errored": errored}})
    return {"tickets": tickets, "validation_errors": bad, "done": done,
            "skipped": skipped, "errored": errored}


def main():
    ap = argparse.ArgumentParser(description="Generate combo betting tickets from Excel.")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve + build payloads from cached games, no network POSTs")
    ap.add_argument("--offline-games", metavar="FILE",
                    help="use a cached games JSON instead of the live API "
                         "(default in --dry-run: tournaments_games_with_bet.json)")
    ap.add_argument("--sheets", nargs="*", default=None,
                    help="which sheets to process (default: all numbered sheets present)")
    ap.add_argument("--dump-api", action="store_true",
                    help="save each fetched games response to logs/api/ (for verifying markets)")
    ap.add_argument("--excel", default=config.EXCEL_FILE,
                    help="path to the ticket workbook (default: config.EXCEL_FILE)")
    args = ap.parse_args()
    logsetup.setup_logging()
    run(excel=args.excel, dry_run=args.dry_run, sheets=args.sheets,
        offline_games=args.offline_games, dump_api=args.dump_api)


if __name__ == "__main__":
    main()
