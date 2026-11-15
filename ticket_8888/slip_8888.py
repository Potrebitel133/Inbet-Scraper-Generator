"""
8888 tickets: build the saved betslip exactly as the 8888 page builds it, and
check the ticket 8888 gives back.

How the 8888 page makes a ticket (recorded on the client's PC, 15 Nov 2026;
tests/fixtures/8888/ticket):

  1. POST {API}/v2/bet/slip-data with
        betSlip: the selection numbers + one accumulator bet (type 2) with the stake
        data:    the betslip as the page shows it (names, odds, winnings), as a
                 JSON string; the cashier's terminal loads it from the code
     -> {"success": true, "id": "QS568343080480"}
  2. The QR on the page is exactly that id (decoded from the client's screen).
  3. POST https://8888punkt.bg/tickets/detail/ (code=...) -> the ticket as 8888
     stored it (stake, every pick, odds, ACCA bonus). The robot reads it back
     and compares before it saves any QR.

Nothing here talks to the network: bot_8888 does that.
"""

import html
import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper_8888"))

from scraper_8888 import SPORTS, _clean, _teams, main_market_type, market_sides  # noqa: E402

SIDE_PICK = {"home": "1", "draw": "X", "away": "2"}
SLIP_SOURCE = "tournament-events-table"     # where the page says a pick was clicked
ACCUMULATOR = 2                             # betSlipType / bet type of one combined ticket ("Колонка")
START_MARGIN_SECONDS = 60                   # a match must start later than this to be ticketed

# 8888's ACCA bonus (% of the profit) by number of matches, as 8888 reported it.
# Only the page's display copy of the winnings uses it; 8888 works the bonus out
# itself. The robot learns missing sizes from the tickets it reads back.
KNOWN_ACCA_PERCENT = {5: 7, 6: 10}


class PickError(ValueError):
    """One pick that cannot go on a ticket right now (the reason is shown to the client)."""


def normalize_name(text):
    return " ".join(_clean(text).casefold().replace(" vs ", " - ").split())


def _iso_to_dt(iso):
    return datetime.strptime(str(iso).replace("Z", "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


# --- picks -> legs -------------------------------------------------------------------
def resolve_pick(event, pick, now=None):
    """8888 match page + one sheet row -> the leg that goes on the ticket.

    Raises PickError when the match cannot be ticketed right now: wrong Match ID,
    started, not football/basketball/tennis, main odds missing or suspended, or a
    draw picked in a sport without draws.
    """
    now = now or datetime.now(timezone.utc)
    sport = (event.get("sport") or {}).get("identifier")
    if sport not in SPORTS:
        raise PickError(f"8888 lists it as {sport or 'an unknown sport'}, not football, basketball or tennis")
    home, away = _teams(event)
    names = [n.strip() for n in str(pick.get("match") or "").split(" - ", 1)]
    wanted = [normalize_name(n) for n in names] + ["", ""]
    if normalize_name(home) not in wanted[:2] and normalize_name(away) not in wanted[:2]:
        raise PickError(f"Match ID {pick.get('match_id')} is '{home} - {away}' on 8888, "
                        f"but the sheet says '{pick.get('match')}'")
    if event.get("isLive"):
        raise PickError("the match has already started")
    start = event.get("startTime")
    if not start or (_iso_to_dt(start) - now).total_seconds() < START_MARGIN_SECONDS:
        raise PickError("the match has already started")
    market_type, market = main_market_type(event, sport)
    if not market:
        raise PickError("8888 does not offer the main odds for this match right now")
    sides = market_sides(market, sport, (home, away))
    if not sides:
        raise PickError("the main odds are suspended or incomplete on 8888 right now")
    side = pick["side"]
    if side not in sides:
        raise PickError(f"a draw is picked, but {sport} has no draw")
    selection = sides[side]
    return {
        "match_id": pick.get("match_id"), "match": pick.get("match"), "side": side,
        "pick": SIDE_PICK[side], "excel_odds": pick.get("min_odd"),
        "event_id": event["id"], "sport": sport, "sport_id": (event.get("sport") or {}).get("id"),
        "home": home, "away": away, "start": start,
        "selection_id": str(selection["id"]),
        "selection_name": _clean(selection.get("name") or selection.get("typeName")) or SIDE_PICK[side],
        "odds": float(selection["odds"]),
        "market_name": _clean(market.get("name") or market_type.get("name")),
        "market_type_name": _clean(market_type.get("name") or market.get("name")),
        "bonus_types": market_type.get("bonusTypes") or [],
    }


# --- the betslip ---------------------------------------------------------------------
def total_odds(legs):
    product = 1.0
    for leg in legs:
        product *= leg["odds"]
    return product


def money(value):
    """Stake as 8888 takes it: whole numbers stay whole (10), others two decimals."""
    value = round(float(value), 2)
    return int(value) if value.is_integer() else value


def winnings(stake, legs, acca_percent):
    """(odds winnings, ACCA bonus, total) as 8888 shows them: bonus = % of the profit."""
    plain = float(stake) * total_odds(legs)
    bonus = (plain - float(stake)) * (acca_percent or 0) / 100
    return round(plain, 2), round(bonus, 2), round(plain + bonus, 2)


def build_slip(legs, stake, acca_percent=None, now_ms=None, new_id=None):
    """The body of POST v2/bet/slip-data, field for field as the 8888 page sends it."""
    new_id = new_id or (lambda: str(uuid.uuid4()))
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    stake = money(stake)
    odds = round(total_odds(legs), 2)
    bet_slip = {
        "includeTranslations": False,
        "selections": [{"id": leg["selection_id"], "isHard": False, "isExcluded": False,
                        "source": SLIP_SOURCE} for leg in legs],
        "betSlipType": ACCUMULATOR,
        "bets": [{"betAmount": stake, "type": ACCUMULATOR, "clientId": new_id()}],
        "createdAt": now_ms,
        "isDemo": False,
    }
    bet_id = new_id()
    entities = {}
    for leg in legs:
        source = {
            "id": leg["selection_id"], "sportId": leg["sport_id"], "identifier": leg["sport"],
            "eventId": leg["event_id"], "odds": leg["odds"],
            "marketName": leg["market_name"], "marketTypeName": leg["market_type_name"],
            "eventName": f"{leg['home']} - {leg['away']}", "selectionName": leg["selection_name"],
            "disabled": False, "incompatible": False, "bank": False,
            "bonusPercentage": acca_percent or 0, "bonusTypes": leg["bonus_types"],
            "isExcluded": False, "sourceWidget": SLIP_SOURCE, "clientId": new_id(),
            "isLive": False, "keyboard": False,
        }
        entities[leg["selection_id"]] = {
            "id": leg["selection_id"],
            "name": f"{leg['selection_name']} - {leg['market_name']}",
            "event": f"{leg['home']} - {leg['away']}",
            "bank": False,
            "odds": {"initial": leg["odds"], "approved": leg["odds"], "current": leg["odds"]},
            "isBetBuilder": False,
            "source": json.dumps(source, ensure_ascii=False, separators=(",", ":")),
        }
    data = [{
        "betSlipId": new_id(),
        "betSlipType": ACCUMULATOR,
        "bets": {"ids": [bet_id], "entities": {bet_id: {
            "clientId": bet_id, "type": ACCUMULATOR, "amount": stake,
            "odds": {"initial": odds, "approved": odds, "current": odds},
            "winnings": winnings(stake, legs, acca_percent)[2],
            "count": 0, "size": 0, "isEditing": False}}},
        "selections": {"ids": [leg["selection_id"] for leg in legs], "entities": entities},
    }]
    return {"betSlip": bet_slip, "data": json.dumps(data, ensure_ascii=False, separators=(",", ":"))}


def slip_code(reply):
    """The ticket code from 8888's reply, or raise ValueError with 8888's reason."""
    if isinstance(reply, dict) and reply.get("success") and reply.get("id"):
        return str(reply["id"]).strip()
    if isinstance(reply, dict) and reply.get("maxBetStakes"):
        limit = next(iter(reply["maxBetStakes"].values()), None)
        raise ValueError(f"8888 allows at most {limit} EUR on this ticket")
    reason = ""
    if isinstance(reply, dict):
        err = reply.get("err") or reply.get("error") or {}
        reason = err.get("messageKey") or err.get("message") if isinstance(err, dict) else err
        reason = reason or reply.get("messageKey") or ""
    raise ValueError(f"8888 did not create the ticket{f' ({reason})' if reason else ''}")


# --- reading the ticket back ---------------------------------------------------------
def _text(fragment):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment))).strip()


def _number(text):
    m = re.search(r"-?\d+(?:[.,]\d+)?", text or "")
    return float(m.group(0).replace(",", ".")) if m else None


def _block(source, css_class):
    m = re.search(r'<div class="' + re.escape(css_class) + r'[^"]*"[^>]*>(.*?)</div>', source, re.S)
    return _text(m.group(1)) if m else ""


def parse_ticket_detail(reply):
    """8888's /tickets/detail/ reply -> the ticket as 8888 stored it."""
    page = reply.get("html") if isinstance(reply, dict) else None
    if not page:
        raise ValueError("8888 did not return the ticket")
    code = re.search(r'data-barcode="([^"]+)"', page)
    legs = []
    for part in page.split('<div class="bet-game-info')[1:]:
        title = re.search(r'<div class="bet-game-title">(.*?)</div>', part, re.S)
        odds = re.search(r'<span class="odds">([^<]*)</span>', part)
        legs.append({
            "pick": _text(re.sub(r"<span.*?</span>", "", title.group(1), flags=re.S)) if title else "",
            "odds": _number(odds.group(1)) if odds else None,
            "market": _block(part, "bet-game-type"),
            "teams": _block(part, "bet-game-teams"),
            "date": _block(part, "bet-game-data"),
        })
    bonus = re.search(r'Доп\. печалба\s*([\d.,]+)', _text(page))
    profits = re.findall(r'<div class="betting-profit-sum">(.*?)</div>', page, re.S)
    return {
        "code": code.group(1).strip() if code else "",
        "stake": _number(_block(page, "bet-sum")),
        "type": _block(page, "bet-type"),
        "legs": legs,
        "total_odds": _number(_block(page, "betting-coef-sum")),
        "acca_percent": _number(bonus.group(1)) if bonus else 0,
        "total_win": _number(_text(profits[-1])) if profits else None,
    }


def check_ticket(code, legs, stake, detail):
    """Compare the ticket 8888 stored with what the robot asked for -> (problems, notes).

    problems: anything that makes the ticket wrong (code, stake, a match or pick
    missing or different); no QR is saved then. notes: odds that moved between
    the robot's read and 8888's (normal, the cashier shows it at the point).
    """
    problems, notes = [], []
    if detail.get("code") != code:
        problems.append(f"8888 returned ticket {detail.get('code') or 'nothing'} instead of {code}")
    if detail.get("stake") is None or abs(detail["stake"] - float(stake)) > 0.005:
        problems.append(f"stake on 8888 is {detail.get('stake')}, expected {money(stake)}")
    stored = list(detail.get("legs") or [])
    if len(stored) != len(legs):
        problems.append(f"8888 has {len(stored)} matches on the ticket, expected {len(legs)}")
    for leg in legs:
        teams = normalize_name(f"{leg['home']} - {leg['away']}")
        match = next((s for s in stored if normalize_name(s["teams"]) == teams), None)
        if match is None:
            problems.append(f"{leg['home']} - {leg['away']} is not on the ticket")
            continue
        stored.remove(match)
        if match["pick"].upper().replace("Х", "X") != leg["pick"]:
            problems.append(f"{leg['home']} - {leg['away']}: 8888 has {match['pick']}, expected {leg['pick']}")
        if normalize_name(match["market"]) != normalize_name(leg["market_name"]):
            problems.append(f"{leg['home']} - {leg['away']}: market on 8888 is '{match['market']}', "
                            f"expected '{leg['market_name']}'")
        if match["odds"] is not None and abs(match["odds"] - leg["odds"]) > 0.0001:
            notes.append(f"{leg['home']} - {leg['away']}: odds {leg['odds']:g} -> {match['odds']:g} on 8888")
    return problems, notes
