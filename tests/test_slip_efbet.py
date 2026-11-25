"""
efbet tickets against what the efbet page really did on the client's PC
(25 Nov 2026, tests/fixtures/efbet): the matches and their markets, the
booking request and reply, the booking read back, and efbet's own barcode.
"""

import copy
import json
import os
import sys
from datetime import datetime, timezone

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ticket_efbet"))

import barcode_efbet  # noqa: E402
import slip_efbet as slip  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "efbet")
NOW = datetime(2026, 11, 25, 13, 57, tzinfo=timezone.utc)      # when the client booked 3535002
ORDER = (31500243, 31500242, 31500254, 31500257, 31500253)      # as clicked, all home wins


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


GAMES = load("games_reply.json")["data"]["data"]["game"]
RULES = load("bonus_rules_reply.json")["data"]["details"]
BOOKED = load("book_request.json")["params"]
BOOK_REPLY = load("book_reply.json")["data"]
BOOKING_REPLY = load("booking_reply.json")["data"]


def game(gid):
    return copy.deepcopy(GAMES[str(gid)])


def pick(gid, side="home", match=None, day=None, time=None):
    g = GAMES[str(gid)]
    return {"match_id": gid, "match": match or f"{g['team1_name'].strip()} - {g['team2_name'].strip()}",
            "side": side, "min_odd": None, "day": day, "time": time}


def legs(side="home"):
    return [slip.resolve_pick(game(gid), pick(gid, side), now=NOW) for gid in ORDER]


def fake_legs(odds):
    return [{"odds": o} for o in odds]


# --- the barcode ------------------------------------------------------------------------
def test_barcode_is_efbets_own():
    theirs = Image.open(os.path.join(FIX, "barcode_3535002.png")).convert("L")
    ours = barcode_efbet.render("3535002").convert("L")
    assert ours.size == theirs.size == (264, 70)
    differ = {x for y in range(70) for x in range(264)
              if (ours.getpixel((x, y)) < 128) != (theirs.getpixel((x, y)) < 128)}
    assert differ <= {244}                     # efbet's last bar is one pixel wider
    assert barcode_efbet.decode(theirs) == barcode_efbet.decode(ours) == "3535002"


def test_barcode_reads_back_at_any_size_and_rejects_junk(tmp_path):
    path = barcode_efbet.save("2444215", str(tmp_path / "b.png"), module=8, height=300)
    assert barcode_efbet.decode(Image.open(path)) == "2444215"
    with pytest.raises(ValueError):
        barcode_efbet.decode(Image.new("L", (200, 50), 255))
    with pytest.raises(ValueError):
        barcode_efbet.symbols("")


# --- picks ---------------------------------------------------------------------------------
def test_the_early_payout_odds_are_picked_like_the_client_did():
    got = legs()
    assert [leg["selection_id"] for leg in got] == [s["SelectionId"] for s in BOOKED["bets"][0]["Selections"]]
    assert [leg["odds"] for leg in got] == [s["Price"] for s in BOOKED["bets"][0]["Selections"]]
    stored = BOOKING_REPLY["details"]["bets"][0]["selections"]
    assert [leg["market_id"] for leg in got] == [s["market_id"] for s in stored]
    assert {leg["market_name"] for leg in got} == {"Краен Резултат (Ранно Изплащане)"}
    assert [leg["pick"] for leg in got] == ["1"] * 5


def test_never_the_normal_1x2_market():
    leg = slip.resolve_pick(game(31500243), pick(31500243), now=NOW)
    assert leg["odds"] == 1.64                 # "Краен Резултат" has 1.70


def test_draw_and_away_picks():
    draw = slip.resolve_pick(game(31500243), pick(31500243, "draw"), now=NOW)
    away = slip.resolve_pick(game(31500243), pick(31500243, "away"), now=NOW)
    assert (draw["pick"], draw["odds"]) == ("X", 4.2) and (away["pick"], away["odds"]) == ("2", 3.7)


@pytest.mark.parametrize("change, reason", [
    (lambda g: g.update(is_live=1), "already started"),
    (lambda g: g.update(start_ts=int(NOW.timestamp()) + 30), "already started"),
    (lambda g: g.update(is_blocked=1), "closed betting"),
    (lambda g: g.update(market={k: m for k, m in g["market"].items() if m["display_key"] != "WINNER_EP"}),
     "does not offer the early payout"),
    (lambda g: [m.update(event={}) for m in g["market"].values()], "suspended"),
])
def test_matches_that_cannot_go_on_a_ticket(change, reason):
    g = game(31500243)
    change(g)
    with pytest.raises(slip.PickError, match=reason):
        slip.resolve_pick(g, pick(31500243), now=NOW)


def test_unknown_match_and_no_draw():
    with pytest.raises(slip.PickError, match="not found on efbet"):
        slip.resolve_pick(None, pick(31500243), now=NOW)
    g = game(31500243)
    for m in g["market"].values():
        m["event"] = {k: e for k, e in m["event"].items() if e["type_1"] != "X"}
    with pytest.raises(slip.PickError, match="no draw"):
        slip.resolve_pick(g, pick(31500243, "draw"), now=NOW)


def test_english_names_pass_on_the_same_match_id_and_kickoff():
    # the client's efbet scraper file: '31500243', 'Friday, 27.11.2026', '21:30' (Bulgarian time;
    # efbet's start_ts is 19:30 UTC)
    english = pick(31500243, match="Werder Bremen - Borussia Monchengladbach", day="Friday, 27.11.2026",
                   time="21:30")
    leg = slip.resolve_pick(game(31500243), english, now=NOW)
    assert leg["selection_id"] == 7474303803 and "same Match ID and kick-off" in leg["name_note"]
    wrong = dict(english, time="19:30")
    with pytest.raises(slip.PickError, match="but the sheet says"):
        slip.resolve_pick(game(31500243), wrong, now=NOW)


def test_one_team_name_is_enough():
    leg = slip.resolve_pick(game(31500242), pick(31500242, match="Кьолн - SV Elversberg"), now=NOW)
    assert leg["name_note"] is None


# --- odds, bonus, payout as efbet shows them ---------------------------------------------------
def test_total_odds_are_cut_to_three_decimals():
    assert slip.total_odds(legs()) == 16.526                              # 16.52668 on efbet: 16.526
    assert slip.total_odds(fake_legs([1.65, 1.88, 1.42, 2.30])) == 10.131


def test_bonus_and_payout_match_efbets_screen():
    assert slip.bonus_rule(legs(), RULES) == (5.0, 5000.0)
    assert slip.winnings(2, 16.526, 5.0) == (33.05, 1.55, 34.6)           # 5 matches, stake 2
    four = fake_legs([1.65, 1.88, 1.42, 2.30])
    assert slip.bonus_rule(four, RULES)[0] == 4.0                          # 3-4 is 3%, exactly 4 is 4%
    assert slip.winnings(2, 10.131, 4.0) == (20.26, 0.73, 20.99)


@pytest.mark.parametrize("count, percent", [(4, 4), (5, 5), (6, 10), (7, 20), (8, 30), (9, 40), (10, 50)])
def test_bonus_by_number_of_matches(count, percent):
    assert slip.bonus_rule(fake_legs([2.0] * count), RULES)[0] == percent
    assert slip.bonus_rule(fake_legs([2.0] * count), None)[0] == percent   # table when rules are missing


def test_a_match_below_1_30_does_not_count_for_the_bonus():
    assert slip.bonus_rule(fake_legs([2.0] * 5 + [1.25]), RULES)[0] == 5.0


def test_bonus_is_capped():
    assert slip.winnings(1000, 500.0, 50.0) == (500000.0, 5000.0, 505000.0)


# --- booking -------------------------------------------------------------------------------
def test_booking_request_is_the_pages_own():
    assert slip.booking_params(legs(), 2) == BOOKED
    assert json.dumps(slip.booking_params(legs(), 2.5)["bets"][0]["Amount"]) == "2.5"


def test_booking_number_and_refusal():
    assert slip.booking_id(BOOK_REPLY) == "3535002"
    with pytest.raises(ValueError, match="Max stake exceeded"):
        slip.booking_id({"result": 2, "result_text": "Max stake exceeded", "details": None})
    with pytest.raises(ValueError, match="did not create"):
        slip.booking_id({})


def test_the_booking_read_back_is_what_was_asked():
    booking = slip.parse_booking(BOOKING_REPLY)
    assert booking["code"] == "3535002"
    assert slip.check_booking("3535002", legs(), 2, booking) == ([], [])


def test_differences_in_the_booking_are_found():
    booking = slip.parse_booking(BOOKING_REPLY)
    wrong = copy.deepcopy(booking)
    wrong["bets"][0]["amount"] = 3.0
    wrong["bets"][0]["selections"].pop()
    wrong["bets"][0]["selections"][0]["market_id"] = 2542067088          # the normal 1X2 market
    problems, _ = slip.check_booking("3535002", legs(), 2, wrong)
    assert any("stake on efbet is 3.0" in p for p in problems)
    assert any("Щутгарт - Айнтрахт Франкфурт (1) is not on the ticket" in p for p in problems)
    assert any("market 2542067088" in p for p in problems)
    assert slip.check_booking("9999999", legs(), 2, booking)[0]          # another booking number
    moved = copy.deepcopy(booking)
    moved["bets"][0]["selections"][1]["price"] = 1.9
    problems, notes = slip.check_booking("3535002", legs(), 2, moved)
    assert not problems and notes == ["Кьолн - Елверсберг: odds 1.87 -> 1.9 on efbet"]


def test_unknown_booking():
    with pytest.raises(ValueError):
        slip.parse_booking({"result": 0, "details": {}})
