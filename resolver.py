"""
Turns Excel ticket rows into concrete outcome IDs + draft selections.

Pipeline per row:
  Excel match name + chosen odd
    -> tournament id   (tournaments.json, via utils.cleanup_tournament_name)
    -> event           (games JSON, matched on English team names)
    -> outcome id      (1X2 market, side chosen from Min Odd)
    -> draft selection (full metadata needed by create-draft)
"""

import math
import unicodedata

import config
from utils import cleanup_tournament_name


# --- text matching -----------------------------------------------------------
def normalize(text):
    """Lowercase, strip accents and non-alphanumerics — for fuzzy team matching."""
    if text is None:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    return "".join(c for c in text if c.isalnum() or c.isspace()).strip()


# --- tournaments.json --------------------------------------------------------
def build_tournament_index(tournaments_json):
    """Flatten the sports tree into {path_slug: [tournamentId, ...]}.

    A path can appear more than once (e.g. "norway-eliteserien" exists as both
    19000000020 and 19000000260, and only one of them carries the fixtures), so
    every id is kept and the caller tries them all.
    """
    index = {}

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "tournament" and node.get("path"):
                ids = index.setdefault(node["path"], [])
                if node["id"] not in ids:
                    ids.append(node["id"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(tournaments_json)
    return index


def resolve_tournament_ids(name, tournament_index):
    """All ids an Excel tournament name maps to, trying both slug orderings."""
    for slug in cleanup_tournament_name(name):  # e.g. superliga-argentina / argentina-superliga
        if slug in tournament_index:
            return list(tournament_index[slug])
    return []


def resolve_tournament_id(name, tournament_index):
    """First matching id, or None. Kept for callers that want a single id."""
    ids = resolve_tournament_ids(name, tournament_index)
    return ids[0] if ids else None


def _self_check():
    """Self-check the tournament fallback against the real tournament list.

    Uses tournaments_full.json (whatever the site currently returns) rather than
    a hardcoded index, so this keeps testing reality as the site changes.
    """
    import json
    with open("tournaments_full.json", encoding="utf-8") as f:
        index = {}
        for t in json.load(f):
            index.setdefault(t["path"], []).append(t["id"])

    # A name that matches a path exactly must NOT need the fallback.
    exact = resolve_tournament_ids("Championship- England", index)
    assert exact, "Championship- England should resolve exactly"

    # The reported failure: "Champions League" matches no path, and the fuzzy
    # shortlist is ambiguous — which is precisely why Match IDs decide it.
    assert resolve_tournament_ids("Champions League", index) == []
    cands = candidate_tournament_ids("Champions League", index)
    assert len(cands) > 1, f"expected an ambiguous shortlist, got {cands}"

    # A TRUNCATED name must still shortlist the right tournament (the earlier
    # whole-word check missed this: "engl" is not the word "england").
    assert resolve_tournament_ids("Championship- Engl", index) == []
    assert 19000000018 in candidate_tournament_ids("Championship- Engl", index)
    assert 19000000018 in candidate_tournament_ids("Champ- Engl", index)
    # ...but a prefix must anchor at a word start, not match anywhere.
    assert candidate_tournament_ids("ngland Championship", index) == []

    # Group matching: only the candidate holding EVERY Match ID wins.
    right, wrong = cands[0], cands[1]
    codes = {"1111", "2222", "3333"}

    def fetch(ids):     # the candidate with all three codes, plus a partial decoy
        return {right: codes | {"9999"}, wrong: {"1111", "2222"}}

    tid, detail = resolve_by_match_ids("Champions League", codes, index, fetch)
    assert tid == right, (tid, detail)
    assert detail["matched"] == 3

    # AMBIGUOUS name (>1 candidate) + an ID absent everywhere -> reject, since a
    # partial hit cannot break the tie. The log gets told what was missing.
    tid, detail = resolve_by_match_ids("Champions League", codes | {"4444"}, index, fetch)
    assert tid is None
    assert detail["missing"] == ["4444"], detail

    # UNAMBIGUOUS name (exactly 1 candidate) + a partial hit -> accept it. This is
    # the real Champions League case: #1002 is not listed on the site at all, and
    # rejecting would skip all 10 rows instead of naming the one bad row.
    single = candidate_tournament_ids("Championship- Engl", index)
    assert len(single) == 1, single
    tid, detail = resolve_by_match_ids(
        "Championship- Engl", {"1001", "1002"}, index,
        lambda ids: {single[0]: {"1001"}})           # holds 1 of the 2
    assert tid == single[0], (tid, detail)
    assert detail["matched"] == 1 and detail["missing"] == ["1002"], detail

    # ...but a single candidate holding NOTHING is not evidence of anything.
    tid, _ = resolve_by_match_ids("Championship- Engl", {"1001"}, index,
                                  lambda ids: {single[0]: {"9999"}})
    assert tid is None

    # Guards: no codes, no fuzzy candidates.
    assert resolve_by_match_ids("Champions League", set(), index, fetch)[0] is None
    assert resolve_by_match_ids("Zzz Not A League", codes, index, fetch)[0] is None

    assert event_codes({"events": {"a": {"shortCode": "77"}, "b": {}}}) == {"77"}
    print("resolver self-check ok")


def candidate_tournament_ids(name, tournament_index):
    """Ids whose path CONTAINS every word of the Excel name — fallback shortlist.

    The site spells the same tournament differently per endpoint: id 19000000007
    is "international-clubs-uefa-champions-league" in /public/sports (what we
    index) but "Champions League" in the leagueview response and
    "championship-league" in the UI url. An Excel "Champions League" therefore
    matches no path exactly, and the exact lookup returns nothing.

    Each Excel word must PREFIX some word of the path, so a truncated or
    abbreviated cell still matches ("Championship- Engl" -> england-championship,
    "Champions League" -> ...uefa-champions-league). Prefix rather than exact
    word, because that is how the names get mistyped in practice; prefix rather
    than plain substring, so "engl" does not match "bengaluru".

    This is deliberately loose and therefore ambiguous ("champions league" also
    hits ...champions-hockey-league). The caller resolves that with the Match IDs.
    """
    words = [w for w in normalize(str(name or "").replace("-", " ")).split() if w]
    if not words:
        return []
    ids = []
    for path, path_ids in tournament_index.items():
        path_words = path.split("-")
        if all(any(pw.startswith(w) for pw in path_words) for w in words):
            ids.extend(i for i in path_ids if i not in ids)
    return ids


def event_codes(games_json):
    """Every event shortCode ("Match ID") present in a games response."""
    return {str(e.get("shortCode") or "") for e in games_json.get("events", {}).values()} - {""}


def resolve_by_match_ids(name, codes, tournament_index, fetch_codes):
    """Pick the tournament whose fixtures hold the Excel Match IDs in `codes`.

    Used when an Excel tournament name matches no indexed path (the site spells
    one tournament differently per endpoint, so "Champions League" misses
    "international-clubs-uefa-champions-league").

    Fuzzy name matching alone is ambiguous — "champions league" also hits the
    hockey league — so when SEVERAL tournaments match the name, the winner must
    hold every Match ID; that is the only thing that breaks the tie.

    With a single candidate there is nothing to disambiguate, so a partial hit
    is accepted: an ID can be absent because the fixture is not listed (or not
    listed yet), and rejecting the tournament over that would skip every row
    under it instead of naming the one row that is wrong.

    `fetch_codes(ids)` -> {tournament_id: {shortCode, ...}}; injected so this
    stays testable and the caller owns batching/caching.

    Returns (tournament_id, detail). tournament_id is None when nothing holds
    enough; detail then reports the closest candidate for the log.
    """
    codes = {c for c in codes if c}
    if not codes:
        return None, {"reason": "no Match IDs to match on"}
    candidates = candidate_tournament_ids(name, tournament_index)
    if not candidates:
        return None, {"reason": "no tournament name resembles this one"}

    by_id = fetch_codes(candidates)
    best, best_hits = None, -1
    for tid in candidates:                      # candidate order = index order, stable
        hits = len(codes & by_id.get(tid, set()))
        if hits > best_hits:
            best, best_hits = tid, hits
        if hits == len(codes):
            return tid, {"matched": hits, "total": len(codes), "candidates": len(candidates)}

    missing = sorted(codes - by_id.get(best, set())) if best else sorted(codes)
    detail = {"matched": best_hits, "total": len(codes), "missing": missing,
              "candidates": len(candidates)}
    # Only one tournament matched the name and it holds some of the IDs: trust it
    # and let the per-row search report exactly which matches are unfindable.
    if len(candidates) == 1 and best_hits:
        return best, detail
    return None, {"reason": f"best candidate {best} held {best_hits}/{len(codes)} Match IDs", **detail}


# --- games JSON --------------------------------------------------------------
def is_enhanced_odds(market):
    """True for the 'Enhanced Odds' (Завишен Коефициент) Full Time Result market.

    Client rule: always take the regular Early-Payout market, never Enhanced Odds.
    """
    if market.get("customMarketType") == config.ENHANCED_ODDS_MARKET_TYPE:
        return True
    name = (market.get("marketName") or "").lower()
    return any(marker in name for marker in config.ENHANCED_ODDS_NAME_MARKERS)


def select_result_market(markets):
    """Pick the regular EP Full Time Result market, excluding Enhanced Odds."""
    candidates = [
        m for m in markets
        if m.get("marketTemplateId") == config.MATCH_RESULT_TEMPLATE_ID and not is_enhanced_odds(m)
    ]
    if not candidates:
        return None
    return next((m for m in candidates if m.get("isMainMarket")), candidates[0])


def build_event_index(games_json, fetch_event_markets=None):
    """Index events by normalized English home/away team names.

    Returns a list of {home, away, event, market_1x2} entries so a pick can be
    matched even when only one side's name is present.

    Some tournaments' multi-leagueview response carries ONLY the Enhanced Odds
    variant of the 1X2 market for an event, never a plain Early-Payout one —
    so the real (non-Enhanced) odds simply aren't in this response. When that
    happens and `fetch_event_markets` (e.g. common.fetch_event_markets) is
    given, it is called with the event id to pull the EP market from the
    per-event eventview API instead of skipping the event. English team names
    still come from the multi-leagueview 1X2 market's outcomes (nameEN is
    present there either way; eventview's own outcomes leave nameEN empty).
    """
    entries = []
    for event in games_json.get("events", {}).values():
        markets = event.get("markets")
        markets = list(markets.values()) if isinstance(markets, dict) else (markets or [])
        # Any 1X2 market (regular or Enhanced Odds) purely for English names —
        # multi-leagueview always populates nameEN here even when the only
        # 1X2 market present is the Enhanced Odds one.
        any_1x2 = next((m for m in markets if m.get("marketTemplateId") == config.MATCH_RESULT_TEMPLATE_ID), None)
        if not any_1x2:
            continue
        name_outcomes = any_1x2.get("outcomes")
        name_outcomes = list(name_outcomes.values()) if isinstance(name_outcomes, dict) else name_outcomes
        name_by_side = {o.get("marketTemplateOutcomeId"): o for o in name_outcomes}
        home_en = (name_by_side.get(config.OUTCOME_TEMPLATE_ID["home"]) or {}).get("nameEN", "")
        away_en = (name_by_side.get(config.OUTCOME_TEMPLATE_ID["away"]) or {}).get("nameEN", "")

        # Record any Enhanced Odds FTR market we deliberately dropped, so the
        # ticket log can prove we bet the Early-Payout market, not Enhanced.
        enhanced_skipped = [
            m.get("marketName") for m in markets
            if m.get("marketTemplateId") == config.MATCH_RESULT_TEMPLATE_ID and is_enhanced_odds(m)
        ]

        market = select_result_market(markets)
        if not market and fetch_event_markets:
            event_id = event.get("eventId") or event.get("sportEventId")
            try:
                event_markets = fetch_event_markets(event_id)
            except Exception:
                event_markets = {}
            market = next(
                (m for m in event_markets.values()
                 if m.get("marketTemplateId") == config.MATCH_RESULT_TEMPLATE_ID
                 and m.get("customMarketType") is None),
                None)
        if not market:
            continue

        outcomes = market.get("outcomes")
        outcomes = list(outcomes.values()) if isinstance(outcomes, dict) else outcomes
        by_side = {o.get("marketTemplateOutcomeId"): o for o in outcomes}
        entries.append({
            "home": normalize(home_en),
            "away": normalize(away_en),
            "event": event,
            "market": market,
            "by_side": by_side,
            "enhanced_skipped": enhanced_skipped,
            "short_code": str(event.get("shortCode") or ""),  # Excel "Match ID" (#2255)
            "start_time": str(event.get("startTime") or ""),
        })
    return entries


def resolve_event_for_pick(games_json, pick, fetch_event_markets=None):
    """Find and resolve a pick directly via its Excel Match ID (shortCode).

    This is the fast path for rows that carry a tournament_id column (scraper
    output). Steps:
      1. Scan the leagueview events for the one whose shortCode matches the
         pick's match_id — a single O(n) pass, no fuzzy name matching.
      2. Try to select the plain EP Full Time Result market from leagueview.
      3. Only if the event is Enhanced-only, call fetch_event_markets for
         THAT ONE event — never for every event in the tournament.
      4. Return the same entry-dict shape as build_event_index entries so
         build_selection works completely unchanged downstream.

    Returns None when:
      - The pick has no match_id (caller falls back to name-based lookup).
      - No event with that shortCode exists in games_json.
      - No usable EP market can be found even after the eventview call.
    """
    code = match_id_code(pick)
    if not code:
        return None  # no shortCode — caller must fall back to name matching

    # Step 1: find the event by shortCode (O(n) scan, n = events in tournament)
    events = games_json.get("events", {})
    event = next(
        (e for e in events.values() if str(e.get("shortCode") or "") == code),
        None,
    )
    if event is None:
        return None

    # Step 2: gather markets from the leagueview response
    markets = event.get("markets")
    markets = list(markets.values()) if isinstance(markets, dict) else (markets or [])

    # English team names come from any 1X2 market (Enhanced or plain).
    # multi-leagueview always populates nameEN here even when the only
    # 1X2 market present is the Enhanced Odds one.
    any_1x2 = next(
        (m for m in markets if m.get("marketTemplateId") == config.MATCH_RESULT_TEMPLATE_ID),
        None,
    )
    name_outcomes = any_1x2.get("outcomes") if any_1x2 else []
    name_outcomes = (
        list(name_outcomes.values()) if isinstance(name_outcomes, dict) else (name_outcomes or [])
    )
    name_by_side = {o.get("marketTemplateOutcomeId"): o for o in name_outcomes}
    home_en = (name_by_side.get(config.OUTCOME_TEMPLATE_ID["home"]) or {}).get("nameEN", "")
    away_en = (name_by_side.get(config.OUTCOME_TEMPLATE_ID["away"]) or {}).get("nameEN", "")

    # Record any Enhanced Odds FTR market we deliberately skipped, so the
    # ticket log can prove we bet the Early-Payout market, not Enhanced.
    enhanced_skipped = [
        m.get("marketName") for m in markets
        if m.get("marketTemplateId") == config.MATCH_RESULT_TEMPLATE_ID and is_enhanced_odds(m)
    ]

    # Step 3: try plain EP market from leagueview; if absent, call eventview
    # for THIS event only — never for every event in the tournament.
    market = select_result_market(markets)
    if not market and fetch_event_markets:
        event_id = event.get("eventId") or event.get("sportEventId")
        try:
            event_markets = fetch_event_markets(event_id)
        except Exception:
            event_markets = {}
        market = next(
            (m for m in event_markets.values()
             if m.get("marketTemplateId") == config.MATCH_RESULT_TEMPLATE_ID
             and m.get("customMarketType") is None),
            None,
        )
    if not market:
        return None

    # Step 4: build the entry dict — identical shape to build_event_index entries.
    outcomes = market.get("outcomes")
    outcomes = list(outcomes.values()) if isinstance(outcomes, dict) else (outcomes or [])
    by_side = {o.get("marketTemplateOutcomeId"): o for o in outcomes}
    return {
        "home": normalize(home_en),
        "away": normalize(away_en),
        "event": event,
        "market": market,
        "by_side": by_side,
        "enhanced_skipped": enhanced_skipped,
        "short_code": str(event.get("shortCode") or ""),
        "start_time": str(event.get("startTime") or ""),
    }


def match_id_code(pick):
    """The Excel 'Match ID' (#2255) as a bare code, or '' when absent."""
    return str(pick.get("match_id") or "").lstrip("#").strip()


def find_event(pick, event_index):
    """Find the event for a pick.

    Prefers the Excel "Match ID", which is the site's per-event `shortCode` —
    an exact identifier. That disambiguates the case where one tournament slug
    maps to several ids and the same fixture appears under more than one.
    Falls back to English team-name matching when no code is available.
    """
    code = match_id_code(pick)
    if code:
        exact = [e for e in event_index if e.get("short_code") == code]
        if exact:
            return exact[0]

    ph, pa = normalize(pick["home_team"]), normalize(pick["away_team"])

    def team_matches(a, b):
        return bool(a) and bool(b) and (a in b or b in a)

    def score(entry):
        return int(team_matches(ph, entry["home"])) + int(team_matches(pa, entry["away"]))

    best = max(event_index, key=score, default=None)
    if not best:
        return None
    # Require BOTH team names to match when we have both (avoids betting on the
    # wrong game that merely shares one team). Fall back to a single-team match
    # only when the Excel row didn't give us both names.
    need = 2 if (ph and pa) else 1
    return best if score(best) >= need else None


def build_selection(pick, entry):
    """Build the create-draft `selections[]` object for a matched pick.

    Returns (selection_dict, outcome_id) or (None, None) if the outcome is
    missing / inactive.
    """
    event = entry["event"]
    market = entry["market"]
    template_out_id = config.OUTCOME_TEMPLATE_ID[pick["side"]]
    outcome = entry["by_side"].get(template_out_id)
    if not outcome or not outcome.get("isActive", True):
        return None, None

    outcome_id = str(outcome["id"])
    selection = {
        "eventTitle": event.get("eventTitle"),
        "sportEventId": event.get("sportEventId"),
        "marketName": market.get("marketName"),
        "outcomeTitle": outcome.get("title"),
        "outcomeId": outcome_id,
        "odds": outcome.get("odds"),
        "americanOdds": outcome.get("americanOdds"),
        "fractionalOdds": outcome.get("fractionalOdds"),
        "hongKongOdds": outcome.get("hongKongOdds"),
        "malayOdds": outcome.get("malayOdds"),
        "marketId": market.get("marketId"),
        "sportId": event.get("sportId"),
        "marketTemplateId": market.get("marketTemplateId"),
        "marketStatus": market.get("marketStatus"),
        "isActive": outcome.get("isActive", True),
        "sportType": "REGULAR",
        "isLive": bool(event.get("live")),
        "tournamentId": event.get("tournamentId"),
        "categoryId": event.get("categoryId"),
        "marketTemplateOutcomeId": template_out_id,
        "specifier": outcome.get("specifier"),
        "eventPath": event.get("eventPath"),
        "sportEventType": "match",
        "homeTeam": event.get("homeTeam"),
        "awayTeam": event.get("awayTeam"),
        "startTime": event.get("startTime"),
        "tournamentName": event.get("tournamentName"),
        "marketTemplateName": market.get("marketTemplateName"),
        "id": outcome_id,
        "isBanker": False,
        "isChecked": True,
    }
    return selection, outcome_id


def combined_odds(selections):
    """Product of all selection odds (the accumulator's total odds)."""
    total = 1.0
    for s in selections:
        total *= float(s["odds"])
    return total


def round_up_to_5(value):
    """Round up to the next multiple of 5 (32..35 -> 35, 36..40 -> 40)."""
    return math.ceil(value / 5) * 5


def stake_for(total_odds):
    """stake = STAKE_BUDGET / total_odds, rounded up to the next multiple of 5."""
    return round_up_to_5(config.STAKE_BUDGET / total_odds)


def enrich_selections_from_bets(cb_response):
    """Build create-draft selections from the create-bets response outcomes.

    The betslip API returns fully-populated outcome objects (~54 fields); the
    site sends THESE back as create-draft selections. Hand-built subsets get
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


if __name__ == "__main__":
    _self_check()
