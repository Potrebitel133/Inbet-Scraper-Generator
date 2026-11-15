"""
The 8888 betslip, tested against the real ticket made on the client's PC
(tests/fixtures/8888/ticket, recorded 15 Nov 2026, code QS568343080480):
5 matches, 10 EUR, ACCA bonus 7 %.
"""

import copy
import itertools
import json
import os
import sys
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ticket_8888"))
sys.path.insert(0, os.path.join(ROOT, "scraper_8888"))

import slip_8888 as slip  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "8888", "ticket")
NOW = datetime(2026, 11, 15, 14, 20, tzinfo=timezone.utc)          # when the ticket was made


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


# The ticket as on sheet 17 of the client's 5-match file.
PICKS = [
    (4156205, "Олимпия Милано - Цървена Звезда", "home", 1.88),
    (4167326, "Бешикташ - Реал Мадрид", "home", 2.38),
    (3707207, "Славия Прага - Виляреал", "away", 2.1),
    (3708369, "Брюж - Ливърпул", "away", 1.65),
    (3708367, "Спортинг - Манчестър Юнайтед", "away", 2.45),
]


def pick(event_id, match, side, min_odd):
    return {"match_id": f"#{event_id}", "match": match, "side": side, "min_odd": min_odd}


def events():
    return {eid: fixture(f"event_{eid}.json") for eid, *_ in PICKS}


def legs():
    ev = events()
    return [slip.resolve_pick(ev[eid], pick(eid, m, s, o), now=NOW) for eid, m, s, o in PICKS]


def recorded():
    body = fixture("slip_request.json")
    return body["betSlip"], json.loads(body["data"])[0]


# --- picks -> the selections 8888 got --------------------------------------------------
def test_picks_resolve_to_the_selections_the_site_sent():
    bet_slip, data = recorded()
    got = legs()
    assert [leg["selection_id"] for leg in got] == [s["id"] for s in bet_slip["selections"]]
    assert [leg["odds"] for leg in got] == [data["selections"]["entities"][s["id"]]["odds"]["current"]
                                            for s in bet_slip["selections"]]
    assert [leg["pick"] for leg in got] == ["1", "1", "2", "2", "2"]
    # the regular (early payout) markets, as on the site
    assert all(any(b.get("typeId") == 2 for b in leg["bonus_types"]) for leg in got)


def test_body_is_built_exactly_like_the_sites():
    bet_slip, data = recorded()
    ids = itertools.count()
    body = slip.build_slip(legs(), 10, acca_percent=7, now_ms=bet_slip["createdAt"],
                           new_id=lambda: f"id-{next(ids)}")
    mine = body["betSlip"]
    assert set(mine) == set(bet_slip)
    for key in ("includeTranslations", "betSlipType", "createdAt", "isDemo"):
        assert mine[key] == bet_slip[key]
    assert mine["selections"] == [dict(s, source="tournament-events-table") for s in bet_slip["selections"]]
    assert [{k: v for k, v in b.items() if k != "clientId"} for b in mine["bets"]] == \
           [{k: v for k, v in b.items() if k != "clientId"} for b in bet_slip["bets"]]

    my_data = json.loads(body["data"])
    assert len(my_data) == 1 and set(my_data[0]) == set(data)
    (my_bet,), (bet,) = my_data[0]["bets"]["entities"].values(), data["bets"]["entities"].values()
    assert {k: v for k, v in my_bet.items() if k != "clientId"} == {k: v for k, v in bet.items() if k != "clientId"}
    assert my_data[0]["selections"]["ids"] == data["selections"]["ids"]
    for sid in data["selections"]["ids"]:
        mine_e, theirs = my_data[0]["selections"]["entities"][sid], data["selections"]["entities"][sid]
        assert {k: v for k, v in mine_e.items() if k != "source"} == {k: v for k, v in theirs.items() if k != "source"}
        my_src, src = json.loads(mine_e["source"]), json.loads(theirs["source"])
        for key in ("clientId", "sourceWidget"):        # the page's own click bookkeeping
            my_src.pop(key), src.pop(key, None)
        assert my_src == src


def test_winnings_match_8888s_own_figures():
    server = fixture("server_betslip.json")["betSlip"]
    plain, bonus, total = slip.winnings(10, legs(), 7)
    assert (plain, bonus, total) == (server["potentialWinWithoutBonus"], server["potentialWinBonusPart"],
                                     server["totalPotentialWin"])
    assert round(slip.total_odds(legs()), 2) == server["totalOdds"]
    assert slip.KNOWN_ACCA_PERCENT[5] == round(server["oddsBonusPercentage"] * 100)


def test_stake_formats():
    assert slip.money(10) == 10 and slip.money(10.0) == 10 and slip.money(272.5) == 272.5
    assert slip.money("12.499") == 12.5


# --- 8888's answer ---------------------------------------------------------------------
def test_ticket_code_from_the_reply():
    assert slip.slip_code(fixture("slip_response.json")) == "QS568343080480"
    with pytest.raises(ValueError, match="at most 500"):
        slip.slip_code({"success": False, "maxBetStakes": {"abc": 500}})
    with pytest.raises(ValueError, match="error.selection_suspended"):
        slip.slip_code({"success": False, "err": {"messageKey": "error.selection_suspended"}})
    with pytest.raises(ValueError, match="did not create"):
        slip.slip_code({"success": True})


def test_ticket_read_back_from_8888():
    detail = slip.parse_ticket_detail(fixture("detail_response.json"))
    assert detail["code"] == "QS568343080480" and detail["stake"] == 10 and detail["type"] == "Колонка"
    assert detail["total_odds"] == 40.56 and detail["acca_percent"] == 7 and detail["total_win"] == 433.25
    assert len(detail["legs"]) == 5
    assert {"pick": "1", "odds": 2.55, "market": "Победител (вкл. Продължение)",
            "teams": "Бешикташ vs Реал Мадрид", "date": "18/11/2026, 19:00"} in detail["legs"]
    assert slip.check_ticket("QS568343080480", legs(), 10, detail) == ([], [])


@pytest.mark.parametrize("change, expected", [
    (lambda d: d["legs"][0].update(pick="2"), "8888 has 2, expected 1"),
    (lambda d: d["legs"].pop(), "is not on the ticket"),
    (lambda d: d.update(stake=100.0), "stake on 8888 is 100.0"),
    (lambda d: d.update(code="QS1"), "returned ticket QS1"),
    (lambda d: d["legs"][1].update(market="Двоен Шанс"), "market on 8888 is 'Двоен Шанс'"),
])
def test_wrong_tickets_are_caught(change, expected):
    detail = slip.parse_ticket_detail(fixture("detail_response.json"))
    change(detail)
    problems, _ = slip.check_ticket("QS568343080480", legs(), 10, detail)
    assert any(expected in p for p in problems), problems


def test_moved_odds_are_only_a_note():
    detail = slip.parse_ticket_detail(fixture("detail_response.json"))
    detail["legs"][0]["odds"] = 2.6
    problems, notes = slip.check_ticket("QS568343080480", legs(), 10, detail)
    assert problems == [] and notes == ["Бешикташ - Реал Мадрид: odds 2.55 -> 2.6 on 8888"]


# --- picks that cannot go on a ticket ------------------------------------------------
def one(eid=4156205):
    return fixture(f"event_{eid}.json"), pick(eid, "Олимпия Милано - Цървена Звезда", "home", 1.88)


@pytest.mark.parametrize("change, reason", [
    (lambda ev, p: ev.update(isLive=True), "already started"),
    (lambda ev, p: ev.update(startTime="2026-11-15T14:20:30Z"), "already started"),
    (lambda ev, p: p.update(match="Партизан - Валенсия"), "but the sheet says"),
    (lambda ev, p: p.update(side="draw"), "basketball has no draw"),
    (lambda ev, p: ev.update(sport={"id": 71, "identifier": "boxing"}), "not football, basketball or tennis"),
    (lambda ev, p: ev["marketTypes"][0]["markets"][0].update(status=-1), "does not offer the main odds"),
])
def test_unusable_picks_are_explained(change, reason):
    ev, p = one()
    for mt in ev["marketTypes"]:            # put the main market first so the market change hits it
        if mt.get("uId") == 212:
            ev["marketTypes"].remove(mt)
            ev["marketTypes"].insert(0, mt)
            break
    change(ev, p)
    with pytest.raises(slip.PickError, match=reason):
        slip.resolve_pick(ev, p, now=NOW)


def test_suspended_side_is_explained():
    ev, p = one()
    for mt in ev["marketTypes"]:
        if mt.get("uId") == 212:
            mt["markets"][0]["selections"][0]["isActive"] = False
    with pytest.raises(slip.PickError, match="suspended or incomplete"):
        slip.resolve_pick(ev, p, now=NOW)


def test_one_renamed_team_is_still_the_same_match():
    ev, p = one()
    p["match"] = "Олимпия Милано - Ц. Звезда"
    assert slip.resolve_pick(ev, p, now=NOW)["selection_id"] == "8445580519484368069"


def test_boosted_copy_is_never_put_on_a_ticket():
    ev = fixture("event_3707207.json")
    regular = next(mt for mt in ev["marketTypes"] if mt.get("uId") == 23)
    boosted = copy.deepcopy(regular)
    boosted.update(id=10000023, bonusTypes=[{"typeId": 1, "isZeroMargin": False, "reducedMarginValue": 105}])
    for m in boosted["markets"]:
        for s in m["selections"]:
            s["id"], s["odds"] = "boosted-" + s["id"], s["odds"] + 0.1
    ev["marketTypes"].insert(0, boosted)
    leg = slip.resolve_pick(ev, pick(3707207, "Славия Прага - Виляреал", "away", 2.1), now=NOW)
    assert leg["selection_id"] == "6308848478000607373" and leg["odds"] == 2.1
