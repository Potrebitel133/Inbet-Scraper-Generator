"""
efbet tickets: pick the right odds, book the ticket exactly as the efbet page
does, and check the booking efbet gives back.

How the efbet page makes a ticket (recorded on the client's PC, 25 Nov 2026;
tests/fixtures/efbet). efbet runs on BetConstruct: the page talks to its
"swarm" server over one WebSocket, with JSON commands:

  1. request_session {language, site_id 18762167, source 42, ...}
  2. get (source "betting"): every match with its markets. The early payout
     1X2 market is display_key "WINNER_EP" (type MatchResultEP, name
     "Краен Резултат (Ранно Изплащане)"), next to the normal "WINNER" one
     ("Краен Резултат"). Its odds are W1 / X / W2.
  3. create_bet_book_v2 {bets: [{BetType 2, Selections [{SelectionId,
     Price}], Amount}]} -> details.Id, the booking number (3535002). The
     barcode shown and printed is Code 128 of that number (barcode_efbet).
  4. get_booking_by_id_v2 {booking_id} -> the booking as efbet stored it:
     bet type, stake and every selection with its match, market and price.
     The robot reads every ticket back and compares before it saves a barcode.

efbet shows the total odds cut (not rounded) to 3 decimals: 16.526 for a
product of 16.52668. Its accumulator bonus is a % of the profit by number of
matches (get_sport_bonus_rules): 4% for 4, 5% for 5, 10% for 6, 20% for 7,
30% for 8, 40% for 9, 50% for 10; a match below 1.30 does not count.

Nothing here talks to the network: bot_efbet does that through swarm_efbet.
"""

import sys
from datetime import datetime, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper_8888"))

from scraper_8888 import day_and_time  # noqa: E402

SIDE_TYPE = {"home": "W1", "draw": "X", "away": "W2"}
SIDE_PICK = {"home": "1", "draw": "X", "away": "2"}
EARLY_PAYOUT_KEY = "WINNER_EP"
EARLY_PAYOUT_NAMES = ("ранно изплащане", "early payout")
MULTIPLE = 2                          # BetType of one combined ticket ("Множествен")
START_MARGIN_SECONDS = 60             # a match must start later than this to be ticketed
PRICE_DECIMALS = 3

# efbet's accumulator bonus (% of the profit) by number of matches, as its
# get_sport_bonus_rules said on 25 Nov 2026. Used when the rules cannot be read.
DEFAULT_BONUS = {3: 3, 4: 4, 5: 5, 6: 10, 7: 20, 8: 30, 9: 40, 10: 50, 11: 60, 12: 70, 13: 80, 14: 90, 15: 100}
DEFAULT_BONUS_MIN_ODDS = 1.3
DEFAULT_MAX_BONUS = 5000.0            # EUR


class PickError(ValueError):
    """One pick that cannot go on a ticket right now (the reason is shown to the client)."""


def clean(text):
    return " ".join(str(text or "").split())


def normalize_name(text):
    return " ".join(clean(text).casefold().replace(".", " ").split())


def _sheet_time(value):
    """The sheet's Time cell (a time, or text like '19:45') -> 'HH:MM'."""
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    text = clean(value)
    return text[:5] if len(text) >= 5 else text.zfill(5)


def _same_start(game, pick):
    """True when the sheet's Day and Time are efbet's kick-off in Bulgarian time."""
    try:
        iso = datetime.fromtimestamp(int(game["start_ts"]), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (KeyError, TypeError, ValueError):
        return False
    day, hhmm = day_and_time(iso)
    sheet_day = clean(pick.get("day"))
    return sheet_day.split(",")[-1].strip() == day.split(",")[-1].strip() and _sheet_time(pick.get("time")) == hhmm


def early_payout_market(game):
    """The match's early payout 1X2 market, or None."""
    markets = list((game.get("market") or {}).values())
    for m in markets:
        if m.get("display_key") == EARLY_PAYOUT_KEY:
            return m
    for m in markets:
        if any(n in clean(m.get("name")).casefold() for n in EARLY_PAYOUT_NAMES):
            return m
    return None


# --- picks -> legs -------------------------------------------------------------------
def resolve_pick(game, pick, now=None):
    """efbet match (with its markets) + one sheet row -> the leg that goes on the ticket.

    Raises PickError when the match cannot be ticketed right now: unknown Match
    ID, another match, started, closed, no early payout odds, or a draw picked
    where there is none. The Match ID decides; when efbet's team names differ
    from the sheet's, the kick-off time must then be the sheet's.
    """
    now = now or datetime.now(timezone.utc)
    match_id = str(pick.get("match_id") or "").lstrip("#").strip()
    if not game:
        raise PickError("not found on efbet (the match was removed or is over)")
    home, away = clean(game.get("team1_name")), clean(game.get("team2_name"))
    names = [normalize_name(n) for n in str(pick.get("match") or "").split(" - ", 1)] + ["", ""]
    note = None
    if normalize_name(home) not in names[:2] and normalize_name(away) not in names[:2]:
        if not _same_start(game, pick):
            raise PickError(f"Match ID {match_id} is '{home} - {away}' on efbet, but the sheet says "
                            f"'{pick.get('match')}'")
        note = f"efbet names it '{home} - {away}' (same Match ID and kick-off)"
    if game.get("is_live") or game.get("type") == 1:
        raise PickError("the match has already started")
    try:
        start_ts = int(game["start_ts"])
    except (KeyError, TypeError, ValueError):
        raise PickError("efbet gives no kick-off time for this match")
    if start_ts - now.timestamp() < START_MARGIN_SECONDS:
        raise PickError("the match has already started")
    if game.get("is_blocked"):
        raise PickError("efbet has closed betting on this match right now")
    market = early_payout_market(game)
    if not market:
        raise PickError("efbet does not offer the early payout odds (Ранно Изплащане) for this match right now")
    events = {e.get("type_1") or e.get("name"): e for e in (market.get("event") or {}).values()}
    side = pick["side"]
    event = events.get(SIDE_TYPE[side])
    if event is None:
        if side == "draw" and events:
            raise PickError("a draw is picked, but this match has no draw on efbet")
        raise PickError("the early payout odds are suspended on efbet right now")
    try:
        odds = float(event["price"])
    except (KeyError, TypeError, ValueError):
        odds = 0.0
    if odds <= 1.0:
        raise PickError("the early payout odds are suspended on efbet right now")
    return {
        "match_id": match_id, "match": pick.get("match"), "side": side, "pick": SIDE_PICK[side],
        "excel_odds": pick.get("min_odd"), "game_id": int(game.get("id") or match_id),
        "home": home, "away": away, "sport": game.get("sport_alias"),
        "start": datetime.fromtimestamp(start_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "market_id": int(market["id"]), "market_name": clean(market.get("name")),
        "selection_id": int(event["id"]), "selection_name": SIDE_TYPE[side], "odds": odds,
        "name_note": note,
    }


# --- odds, stake, bonus --------------------------------------------------------------
def total_odds(legs):
    """The ticket's odds as efbet shows them: the product cut to 3 decimals (16.52668 -> 16.526)."""
    product = Decimal(1)
    for leg in legs:
        product *= Decimal(str(leg["odds"]))
    return float(product.quantize(Decimal(1).scaleb(-PRICE_DECIMALS), rounding=ROUND_DOWN))


def money(value):
    """Stake as efbet takes it: whole numbers stay whole (2), others two decimals."""
    value = round(float(value), 2)
    return int(value) if value.is_integer() else value


def _cents(value):
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def bonus_rule(legs, rules=None):
    """(bonus %, highest bonus in EUR) for this ticket from efbet's rules (get_sport_bonus_rules).

    Only matches at or above the rule's minimum odds (1.30) count. When two
    rules fit (3-4 matches 3%, 4 matches 4%), the narrower one is efbet's:
    a 4-match ticket showed 4%.
    """
    if rules:
        fitting = []
        for rule in rules:
            if rule.get("BetType") not in (None, MULTIPLE):
                continue
            min_odds = float(rule.get("MinOdds") or 0)
            counted = sum(1 for leg in legs if not rule.get("IgnoreLowOddSelection") or leg["odds"] >= min_odds)
            lo, hi = rule.get("MinimumSelections") or 0, rule.get("MaximumSelections") or 99
            if lo <= counted <= hi:
                caps = [s.get("MaxBonusAmount") for s in (rule.get("MinBetStakes") or {}).get("MinStakes") or []
                        if s.get("Currency") == "EUR" and s.get("MaxBonusAmount")]
                fitting.append((lo, hi - lo, float(rule.get("AmountPercent") or 0),
                                float(caps[0]) if caps else DEFAULT_MAX_BONUS))
        if fitting:
            best = max(fitting, key=lambda r: (r[0], -r[1]))
            return best[2], best[3]
        return 0.0, DEFAULT_MAX_BONUS
    counted = sum(1 for leg in legs if leg["odds"] >= DEFAULT_BONUS_MIN_ODDS)
    return float(DEFAULT_BONUS.get(counted, 0)), DEFAULT_MAX_BONUS


def winnings(stake, odds, percent, max_bonus=DEFAULT_MAX_BONUS):
    """(possible win, bonus, final payout) as efbet shows them: bonus = % of the profit."""
    possible = float(stake) * odds
    bonus = min((possible - float(stake)) * (percent or 0) / 100, max_bonus)
    return _cents(possible), _cents(bonus), _cents(_cents(possible) + _cents(bonus))


# --- booking -------------------------------------------------------------------------
def booking_params(legs, stake):
    """The params of create_bet_book_v2, field for field as the efbet page sends them."""
    return {"bets": [{
        "BetType": MULTIPLE,
        "Selections": [{"SelectionId": int(leg["selection_id"]), "Price": leg["odds"]} for leg in legs],
        "Amount": money(stake),
    }]}


def booking_id(reply):
    """The booking number from create_bet_book_v2's reply, or raise ValueError with efbet's reason."""
    details = reply.get("details") if isinstance(reply, dict) else None
    if isinstance(reply, dict) and reply.get("result") == 0 and isinstance(details, dict) and details.get("Id"):
        return str(details["Id"])
    reason = (reply or {}).get("result_text") if isinstance(reply, dict) else None
    code = (reply or {}).get("result") if isinstance(reply, dict) else None
    detail = reason or (f"code {code}" if code not in (None, 0) else "")
    raise ValueError(f"efbet did not create the ticket{f' ({detail})' if detail else ''}")


def parse_booking(reply):
    """get_booking_by_id_v2's reply -> the booking as efbet stored it."""
    details = reply.get("details") if isinstance(reply, dict) else None
    if not (isinstance(reply, dict) and reply.get("result") == 0 and isinstance(details, dict) and details.get("id")):
        raise ValueError("efbet did not return the booking")
    bets = []
    for bet in details.get("bets") or []:
        bets.append({
            "bet_type": bet.get("bet_type"),
            "amount": bet.get("amount"),
            "selections": [{"selection_id": int(s["selection_id"]), "market_id": int(s.get("market_id") or 0),
                            "match_id": int(s.get("match_id") or 0), "price": s.get("price")}
                           for s in bet.get("selections") or []],
        })
    return {"code": str(details["id"]), "bets": bets}


def check_booking(code, legs, stake, booking):
    """Compare the booking efbet stored with what the robot asked for -> (problems, notes).

    problems: anything that makes the ticket wrong (number, bet type, stake, a
    match, market or pick missing or different); no barcode is saved then.
    notes: a price efbet stored differently (normal: the cashier gets the odds
    of the moment when the barcode is scanned).
    """
    problems, notes = [], []
    if booking.get("code") != str(code):
        problems.append(f"efbet returned booking {booking.get('code') or 'nothing'} instead of {code}")
    bets = booking.get("bets") or []
    if len(bets) != 1:
        problems.append(f"efbet has {len(bets)} bets in the booking, expected 1")
        return problems, notes
    bet = bets[0]
    if bet.get("bet_type") != MULTIPLE:
        problems.append(f"bet type on efbet is {bet.get('bet_type')}, expected {MULTIPLE} (Множествен)")
    if bet.get("amount") is None or abs(float(bet["amount"]) - float(stake)) > 0.005:
        problems.append(f"stake on efbet is {bet.get('amount')}, expected {money(stake)}")
    stored = {s["selection_id"]: s for s in bet.get("selections") or []}
    if len(stored) != len(legs):
        problems.append(f"efbet has {len(stored)} matches on the ticket, expected {len(legs)}")
    for leg in legs:
        s = stored.get(int(leg["selection_id"]))
        name = f"{leg['home']} - {leg['away']}"
        if s is None:
            problems.append(f"{name} ({leg['pick']}) is not on the ticket")
            continue
        if s["match_id"] != int(leg["game_id"]):
            problems.append(f"{name}: efbet has match {s['match_id']}, expected {leg['game_id']}")
        if s["market_id"] != int(leg["market_id"]):
            problems.append(f"{name}: efbet has market {s['market_id']}, expected the early payout "
                            f"market {leg['market_id']}")
        if s.get("price") is not None and abs(float(s["price"]) - leg["odds"]) > 0.0001:
            notes.append(f"{name}: odds {leg['odds']:g} -> {float(s['price']):g} on efbet")
    return problems, notes
