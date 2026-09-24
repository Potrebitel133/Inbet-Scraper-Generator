"""
run_all.py — SINGLE-FILE combo-ticket bot for testing on a Bulgarian IP.

Everything (config, HTTP, Excel reading, resolution, draft, QR) is inlined here
so you can run one file. The modular version (bot.py + common.py + resolver.py)
is the same logic split up.

    python run_all.py                 # LIVE: fetch fresh, create drafts + save QRs (needs BG IP)
    python run_all.py --sheets 1      # only ticket sheet 1 (good for a first test)
    python run_all.py --dry-run       # resolve + build payloads from cached files, NO network

Live runs always fetch tournaments + games fresh from the API (nothing is
cached to disk). --dry-run reads the cached JSON files for an offline preview.

Auth tokens (x-platform-exp / x-platform-hash) are generated automatically by
make_platform_tokens() using the site's own algorithm (secret "amazingsecret",
verified against live requests). The ONLY runtime requirement is a Bulgarian IP,
because the API is Cloudflare-geoblocked.
"""

import argparse
import base64
import hashlib
import json
import math
import os
import random
import time
import unicodedata

import openpyxl
import qrcode
import requests

# =============================================================================
# CONFIG  — edit these
# =============================================================================
BASE_URL = "https://inbet-retail-api.egt-digital.com"
SPORT_ID = 1001                       # soccer
STAKE_BUDGET = 50000                  # stake = ceil(STAKE_BUDGET / combined_odds)
# selectedSystem name for an N-fold accumulator (from the site's BetSlip.Systems
# enum). 10 selections -> "ten_folds".
FOLD_SYSTEM = {
    1: "singles", 2: "doubles", 3: "trebles", 4: "four_folds", 5: "five_folds",
    6: "six_folds", 7: "seven_folds", 8: "eight_folds", 9: "nine_folds",
    10: "ten_folds", 11: "eleven_folds", 12: "twelve_folds", 13: "thirteen_folds",
    14: "fourteen_folds", 15: "fifteen_folds",
}
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 70

EXCEL_FILE = "PreFOrmattingOdds Goals MoreMatchesMainFOrtestOriginal - Copy (1).xlsm"
TOURNAMENTS_FILE = "tournaments.json"
GAMES_CACHE_FILE = "tournaments_games_with_bet.json"
QR_OUTPUT_DIR = "qr_codes"
TICKETS_LOG = "tickets_log.json"       # local ledger: voucher -> the bets on it
TICKET_SHEETS = [str(i) for i in range(1, 11)]

MATCH_RESULT_TEMPLATE_ID = 100001     # 1X2 / Full Time Result market
OUTCOME_TEMPLATE_ID = {"home": 100001, "draw": 100002, "away": 100003}
# Client rule: for leagues that have BOTH an Early-Payout (EP) and an
# "Enhanced Odds" (Завишен Коефициент) Full Time Result market, ALWAYS take the
# regular EP one and NEVER the Enhanced Odds one. Enhanced markets are tagged
# customMarketType == "ENHANCED_ODDS" (and their name carries this suffix).
ENHANCED_ODDS_MARKET_TYPE = "ENHANCED_ODDS"
ENHANCED_ODDS_NAME_MARKERS = ("завишен", "enhanced")

# --- Platform token generation (reversed from the site's main.js) ------------
# The site signs each request with:
#     exp  = round((Date.now() + 30*60*1000) / 1000)          # now + 30 min
#     hash = base64url( MD5("amazingsecret" + str(exp)) )     # '='->'', '+'->'-', '/'->'_'
# Verified byte-for-byte against captured live requests, so we generate them
# locally — no browser or manual token paste needed.
PLATFORM_SECRET = "amazingsecret"
PLATFORM_TTL_SECONDS = 30 * 60

# Static device/locale headers (Bulgarian FO session).
PLATFORM_STATIC = {
    "x-platform-device": "desktop",
    "x-platform-lang": "bg",
    "x-platform-timezone": "Europe/Sofia",
    "x-platform-tz": "180",
}


def make_platform_tokens():
    """Generate fresh x-platform-* headers (exp + hash) for one request."""
    exp = round((time.time() + PLATFORM_TTL_SECONDS))
    digest = hashlib.md5(f"{PLATFORM_SECRET}{exp}".encode()).digest()
    token = base64.b64encode(digest).decode().replace("=", "").replace("+", "-").replace("/", "_")
    return {**PLATFORM_STATIC, "x-platform-exp": str(exp), "x-platform-hash": token}

# =============================================================================
# HTTP
# =============================================================================
SESSION = requests.Session()

_BASE_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
    "cache-control": "no-cache",
    "dnt": "1",
    "origin": "https://inbetsport.com",
    "pragma": "no-cache",
    "priority": "u=1, i",
    "referer": "https://inbetsport.com/",
    "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
}


def _headers(json_body=False):
    h = dict(_BASE_HEADERS)
    h.update(make_platform_tokens())  # fresh exp + hash per request
    if json_body:
        h["content-type"] = "application/json"
    return h


def _parse(response):
    if "application/json" not in response.headers.get("content-type", ""):
        if response.headers.get("server") == "cloudflare":
            raise RuntimeError(
                f"HTTP {response.status_code} from Cloudflare (ray "
                f"{response.headers.get('cf-ray')}). A Bulgarian IP is required "
                f"(tokens are generated automatically)."
            )
        response.raise_for_status()
        raise RuntimeError(f"Non-JSON response: {response.text[:200]}")
    return response.json()


def fetch_sports(save_to=None):
    r = SESSION.get(f"{BASE_URL}/api/sportsapi/public/sports", headers=_headers(), timeout=30)
    data = _parse(r)
    if save_to:
        json.dump(data, open(save_to, "w", encoding="utf-8"), indent=4, ensure_ascii=False)
    return data


def fetch_tournament_games(tournament_ids, save_to=None):
    if isinstance(tournament_ids, (list, tuple)):
        tournament_ids = ",".join(str(t) for t in tournament_ids)
    else:
        tournament_ids = str(tournament_ids)
    params = {"tournamentIds": tournament_ids, "isLive": "true", "marketCount": "3", "viewType": "multi"}
    r = SESSION.get(
        f"{BASE_URL}/api/sportsapi/public/sport-events/v2/multi-leagueview/normalized/{SPORT_ID}",
        params=params, headers=_headers(), timeout=30,
    )
    data = _parse(r)
    if save_to:
        json.dump(data, open(save_to, "w", encoding="utf-8"), indent=4, ensure_ascii=False)
    return data


def create_bets(outcome_ids):
    payload = {
        "selections": [{"outcomeId": str(o), "isBanker": False} for o in outcome_ids],
        "includeSpecialBetTypes": True, "isTotalStake": False, "calculateTotalOdds": True,
    }
    r = SESSION.post(f"{BASE_URL}/api/betslip/public/create-bets",
                     headers=_headers(json_body=True), json=payload, timeout=30)
    return _parse(r)


def create_draft(payload):
    r = SESSION.post(f"{BASE_URL}/api/pretail/public/v2/betslip/create-draft",
                     headers=_headers(json_body=True), json=payload, timeout=30)
    return _parse(r)


def check_ticket(ticket_id, ticket_type="", cpf=""):
    """Look up an existing ticket by its number (the site's 'Проверка на талон').

    Returns the ticket details incl. its selections/bets. `ticket_id` is the
    voucher/coupon number (the digits the QR encodes, minus the leading 'N').
    """
    params = {"Cpf": cpf, "TicketId": ticket_id, "Type": ticket_type}
    r = SESSION.get(f"{BASE_URL}/api/pretail/public/v2/check-ticket",
                    params=params, headers=_headers(), timeout=30)
    return _parse(r)


# =============================================================================
# RESOLUTION
# =============================================================================
def cleanup_tournament_name(name):
    """'Superliga- Argentina' -> ['superliga-argentina', 'argentina-superliga']."""
    cleaned = name.lower().replace("-", " ")
    cleaned = "".join(c for c in cleaned if c.isalnum() or c.isspace())
    words = cleaned.split()
    slugs = []
    for slug in ("-".join(words), "-".join(reversed(words))):
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


def normalize(text):
    if text is None:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return "".join(c for c in text if c.isalnum() or c.isspace()).strip()


def pick_side(home_odd, draw_odd, away_odd, min_odd):
    cands = {"home": home_odd, "draw": draw_odd, "away": away_odd}
    for side, odd in cands.items():
        if odd is not None and abs(float(odd) - float(min_odd)) < 1e-9:
            return side
    # Fallback: nearest odd among the cells that actually have a value.
    present = {s: o for s, o in cands.items() if o is not None}
    if not present:
        return "home"
    return min(present, key=lambda s: abs(float(present[s]) - float(min_odd)))


def read_ticket(excel_path, sheet):
    ws = openpyxl.load_workbook(excel_path, data_only=True, keep_vba=False)[sheet]
    picks = []
    for row in ws.iter_rows(min_row=2, max_row=11, max_col=9, values_only=True):
        match_id, _day, _time, match, home, draw, away, tournament, min_odd = row
        if not match or min_odd is None:
            continue
        home_team, _, away_team = str(match).partition(" - ")
        picks.append({
            "match_id": match_id, "match": match,
            "home_team": home_team.strip(), "away_team": away_team.strip(),
            "home_odd": home, "draw_odd": draw, "away_odd": away,
            "min_odd": min_odd, "tournament": tournament,
            "side": pick_side(home, draw, away, min_odd),
        })
    return picks


def build_tournament_index(tournaments_json):
    index = {}

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "tournament" and node.get("path"):
                index[node["path"]] = node["id"]
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(tournaments_json)
    return index


def resolve_tournament_id(name, tournament_index):
    for slug in cleanup_tournament_name(name):
        if slug in tournament_index:
            return tournament_index[slug]
    return None


def is_enhanced_odds(market):
    """True for the 'Enhanced Odds' (Завишен Коефициент) Full Time Result market."""
    if market.get("customMarketType") == ENHANCED_ODDS_MARKET_TYPE:
        return True
    name = (market.get("marketName") or "").lower()
    return any(marker in name for marker in ENHANCED_ODDS_NAME_MARKERS)


def select_result_market(markets):
    """Pick the regular Early-Payout Full Time Result market, never Enhanced Odds."""
    candidates = [
        m for m in markets
        if m.get("marketTemplateId") == MATCH_RESULT_TEMPLATE_ID and not is_enhanced_odds(m)
    ]
    if not candidates:
        return None
    # Prefer the main market when several regular variants exist.
    return next((m for m in candidates if m.get("isMainMarket")), candidates[0])


def build_event_index(games_json):
    entries = []
    for event in games_json.get("events", {}).values():
        markets = event.get("markets")
        markets = list(markets.values()) if isinstance(markets, dict) else (markets or [])
        market = select_result_market(markets)
        if not market:
            continue
        outcomes = market.get("outcomes")
        outcomes = list(outcomes.values()) if isinstance(outcomes, dict) else outcomes
        by_side = {o.get("marketTemplateOutcomeId"): o for o in outcomes}
        entries.append({
            "home": normalize((by_side.get(OUTCOME_TEMPLATE_ID["home"]) or {}).get("nameEN", "")),
            "away": normalize((by_side.get(OUTCOME_TEMPLATE_ID["away"]) or {}).get("nameEN", "")),
            "event": event, "market": market, "by_side": by_side,
        })
    return entries


def find_event(pick, event_index):
    ph, pa = normalize(pick["home_team"]), normalize(pick["away_team"])

    def team_matches(a, b):
        return bool(a) and bool(b) and (a in b or b in a)

    def score(entry):
        return int(team_matches(ph, entry["home"])) + int(team_matches(pa, entry["away"]))

    best = max(event_index, key=score, default=None)
    if not best:
        return None
    # Require BOTH team names to match when we have both (avoids betting on the
    # wrong game that merely shares one team). Only fall back to a single-team
    # match when the Excel row didn't give us both names.
    need = 2 if (ph and pa) else 1
    return best if score(best) >= need else None


def build_selection(pick, entry):
    event, market = entry["event"], entry["market"]
    outcome = entry["by_side"].get(OUTCOME_TEMPLATE_ID[pick["side"]])
    if not outcome or not outcome.get("isActive", True):
        return None
    oid = str(outcome["id"])
    return {
        "eventTitle": event.get("eventTitle"), "sportEventId": event.get("sportEventId"),
        "marketName": market.get("marketName"), "outcomeTitle": outcome.get("title"),
        "outcomeId": oid, "odds": outcome.get("odds"),
        "americanOdds": outcome.get("americanOdds"), "fractionalOdds": outcome.get("fractionalOdds"),
        "hongKongOdds": outcome.get("hongKongOdds"), "malayOdds": outcome.get("malayOdds"),
        "marketId": market.get("marketId"), "sportId": event.get("sportId"),
        "marketTemplateId": market.get("marketTemplateId"), "marketStatus": market.get("marketStatus"),
        "isActive": outcome.get("isActive", True), "sportType": "REGULAR",
        "isLive": bool(event.get("live")), "tournamentId": event.get("tournamentId"),
        "categoryId": event.get("categoryId"),
        "marketTemplateOutcomeId": OUTCOME_TEMPLATE_ID[pick["side"]],
        "specifier": outcome.get("specifier"), "eventPath": event.get("eventPath"),
        "sportEventType": "match", "homeTeam": event.get("homeTeam"), "awayTeam": event.get("awayTeam"),
        "startTime": event.get("startTime"), "tournamentName": event.get("tournamentName"),
        "marketTemplateName": market.get("marketTemplateName"), "id": oid,
        "isBanker": False, "isChecked": True,
    }


def combined_odds(selections):
    total = 1.0
    for s in selections:
        total *= float(s["odds"])
    return total


def stake_for(total_odds):
    return math.ceil(STAKE_BUDGET / total_odds)


# =============================================================================
# DRAFT + QR
# =============================================================================
def build_draft_payload(selections, stake):
    system = FOLD_SYSTEM.get(len(selections))
    if not system:
        raise ValueError(f"no accumulator system name for {len(selections)} selections")
    return {
        "bets": [{
            "stake": {"value": stake},
            "selectedSystem": system,
            "selectionRefs": [{"selectionIndex": i, "banker": False} for i in range(len(selections))],
            "isFreeBet": False,
        }],
        "selections": selections,
        "sender": {"id": "0", "channel": "internet"},
        "oddsChange": "higher", "language": "bg", "placeType": None, "stakeType": "BET_STAKE",
    }


VOUCHER_KEYS = ("code", "betCode", "voucher", "voucherCode", "shortCode", "barcode", "id")


def extract_voucher(draft):
    if not isinstance(draft, dict):
        return None
    for key in VOUCHER_KEYS:
        val = draft.get(key)
        if isinstance(val, (str, int)):
            return str(val)
    for val in draft.values():
        if isinstance(val, dict):
            found = extract_voucher(val)
            if found:
                return found
    return None


def generate_qr(voucher, out_path):
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    qrcode.make("N" + str(voucher)).save(out_path)
    return out_path


def save_ticket_record(voucher, sheet, stake, odds, picks, selections, qr_path):
    """Append a readable record of the ticket's bets to the local ledger.

    check-ticket only finds *placed* (paid) tickets, so this ledger is the
    reliable way to see what bets are on a voucher the bot generated.
    """
    record = {
        "voucher": str(voucher),
        "sheet": sheet,
        "stake": stake,
        "combined_odds": round(float(odds), 4),
        "qr": qr_path,
        "selections": [
            {"match": p["match"], "side": p["side"], "odds": s["odds"], "outcomeId": s["outcomeId"]}
            for p, s in zip(picks, selections)
        ],
    }
    log = []
    if os.path.exists(TICKETS_LOG):
        try:
            log = json.load(open(TICKETS_LOG, encoding="utf-8"))
        except (ValueError, OSError):
            log = []
    log.append(record)
    json.dump(log, open(TICKETS_LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return record


def show_ticket(voucher):
    """Print the bets on a voucher from the local ledger."""
    if not os.path.exists(TICKETS_LOG):
        print(f"no ledger yet ({TICKETS_LOG} not found)")
        return
    log = json.load(open(TICKETS_LOG, encoding="utf-8"))
    hits = [r for r in log if r["voucher"] == str(voucher)]
    if not hits:
        print(f"voucher {voucher} not found in {TICKETS_LOG}")
        return
    for r in hits:
        print(f"Voucher {r['voucher']}  (sheet {r['sheet']})  stake={r['stake']}  odds={r['combined_odds']}")
        for s in r["selections"]:
            print(f"  {str(s['match'])[:45]:<45} {s['side']:<5} @ {s['odds']}")


# =============================================================================
# ORCHESTRATION
# =============================================================================
class GamesProvider:
    """Fetch games per tournament, cached for the run. Offline reads one file."""

    def __init__(self, offline_file=None):
        self.offline_file = offline_file
        self._cache = {}

    def get(self, tournament_id):
        if tournament_id not in self._cache:
            if self.offline_file:
                self._cache[tournament_id] = json.load(open(self.offline_file, encoding="utf-8"))
            else:
                self._cache[tournament_id] = fetch_tournament_games(tournament_id)
        return self._cache[tournament_id]


def enrich_selections_from_bets(cb_response):
    """Build create-draft selections from the create-bets response outcomes.

    The betslip API returns fully-populated outcome objects (~54 fields). The
    site sends THESE back as create-draft selections; hand-built subsets get
    rejected with RT_500. Python preserves the 19-digit ids exactly.
    """
    selections = []
    for o in cb_response.get("outcomes", []):
        oid = str(o["id"])
        selections.append({**o, "outcomeId": oid, "id": oid, "isBanker": False, "isChecked": True})
    return selections


def nfold_odds(cb_response, n):
    """Total odds of the full N-fold accumulator from a create-bets response."""
    for bet in cb_response.get("bets", []):
        if bet.get("fold") == n and bet.get("combinationsCount") == 1:
            return bet.get("totalOdds")
    return None


def process_ticket(sheet, tournament_index, games_provider, dry_run):
    print(f"\n=== Ticket (sheet {sheet}) ===")
    picks = read_ticket(EXCEL_FILE, sheet)
    print(f"  read {len(picks)} picks")

    selections, missing = [], []
    for pick in picks:
        tid = resolve_tournament_id(pick["tournament"], tournament_index)
        if not tid:
            missing.append((pick, "tournament not found"))
            continue
        entry = find_event(pick, build_event_index(games_provider.get(tid)))
        if not entry:
            missing.append((pick, "event not found"))
            continue
        selection = build_selection(pick, entry)
        if not selection:
            missing.append((pick, "outcome inactive/missing"))
            continue
        print(f"  {str(pick['match'])[:42]:<42} {pick['side']:<5} @ {selection['odds']}  -> {selection['outcomeId']}")
        selections.append(selection)

    for pick, reason in missing:
        print(f"  [SKIP] {pick['match']} ({pick['side']}): {reason}")

    if len(selections) != len(picks):
        print(f"  !! only {len(selections)}/{len(picks)} resolved — skipping this ticket")
        return None

    total = combined_odds(selections)
    stake = stake_for(total)
    print(f"  combined odds = {total:.4f}  ->  stake = ceil({STAKE_BUDGET}/odds) = {stake}")

    payload = build_draft_payload(selections, stake)

    if dry_run:
        os.makedirs("drafts", exist_ok=True)
        out = os.path.join("drafts", f"draft_sheet_{sheet}.json")
        json.dump(payload, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"  [dry-run] wrote {out} (no network)")
        return out

    print("  create-bets (validate)...")
    cb = create_bets([s["outcomeId"] for s in selections])
    # The server returns fully-enriched outcome objects; the create-draft
    # selections must be those (not our hand-built ones), or it errors RT_500.
    enriched = enrich_selections_from_bets(cb)
    if len(enriched) != len(selections):
        print(f"  !! create-bets returned {len(enriched)} outcomes, expected {len(selections)} — skipping")
        return None
    live_odds = nfold_odds(cb, len(enriched)) or total
    stake = stake_for(live_odds)
    print(f"  live combined odds = {live_odds:.4f}  ->  stake = {stake}")
    payload = build_draft_payload(enriched, stake)
    print("  create-draft...")
    draft = create_draft(payload)
    voucher = extract_voucher(draft)
    if not voucher:
        os.makedirs("drafts", exist_ok=True)
        json.dump(draft, open(f"drafts/draft_response_sheet_{sheet}.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"  !! no voucher found — full response saved to drafts/draft_response_sheet_{sheet}.json")
        print(f"     response head: {json.dumps(draft, ensure_ascii=False)[:300]}")
        return None
    qr_path = os.path.join(QR_OUTPUT_DIR, f"ticket_sheet_{sheet}.png")
    generate_qr(voucher, qr_path)
    save_ticket_record(voucher, sheet, stake, live_odds, picks, selections, qr_path)
    # Full draft response kept per voucher (there is no public API to re-fetch an
    # unplaced draft by token, so this is the only complete record).
    os.makedirs("drafts", exist_ok=True)
    json.dump(draft, open(f"drafts/ticket_{voucher}.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"  voucher {voucher}  ->  QR saved {qr_path}  (logged to {TICKETS_LOG})")
    return qr_path


def main():
    ap = argparse.ArgumentParser(description="Combo-ticket bot (single-file).")
    ap.add_argument("--dry-run", action="store_true", help="build payloads from cached games, no network")
    ap.add_argument("--sheets", nargs="*", default=TICKET_SHEETS, help="which sheets (default 1..10)")
    ap.add_argument("--offline-games", metavar="FILE", help="cached games JSON to use instead of the API")
    ap.add_argument("--show", metavar="VOUCHER", help="print the bets on a bot-generated voucher (from the local ledger)")
    ap.add_argument("--check", metavar="TICKET_ID", help="query a PLACED ticket via the site's check-ticket API")
    ap.add_argument("--check-type", default="Sport", help="ticket Type for --check (default Sport)")
    ap.add_argument("--check-cpf", default="", help="customer CPF the placed ticket is registered under")
    args = ap.parse_args()

    if args.show:
        show_ticket(args.show)
        return

    if args.check:
        data = check_ticket(args.check, ticket_type=args.check_type, cpf=args.check_cpf)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if args.dry_run:
        # Offline preview: read the cached files, no network.
        offline_file = args.offline_games or GAMES_CACHE_FILE
        tournament_index = build_tournament_index(json.load(open(TOURNAMENTS_FILE, encoding="utf-8")))
        print(f"loaded {len(tournament_index)} tournaments (cache)")
    else:
        # Live: always fetch fresh from the API, never cache to disk.
        offline_file = args.offline_games
        print("fetching tournaments (live)...")
        tournament_index = build_tournament_index(fetch_sports())
        print(f"loaded {len(tournament_index)} tournaments")

    games_provider = GamesProvider(offline_file=offline_file)

    done, skipped, errored = [], [], []
    for i, sheet in enumerate(args.sheets):
        try:
            out = process_ticket(sheet, tournament_index, games_provider, dry_run=args.dry_run)
            (done if out else skipped).append(sheet)
        except Exception as e:
            # Isolate failures so one bad ticket doesn't abort the whole batch.
            print(f"  !! ticket sheet {sheet} failed: {e}")
            errored.append(sheet)
        if not args.dry_run and i < len(args.sheets) - 1:
            wait = random.uniform(MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS)
            print(f"  waiting {wait:.0f}s before next ticket...")
            time.sleep(wait)

    print(f"\n=== Summary === done: {done or '-'} | skipped: {skipped or '-'} | errored: {errored or '-'}")


if __name__ == "__main__":
    main()
