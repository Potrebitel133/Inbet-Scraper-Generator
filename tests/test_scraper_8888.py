"""
8888 scraper, tested against real replies recorded from 8888punkt.bg (tests/fixtures/8888).
No network: the API calls are replaced by the recorded data.
"""

import json
import os
import sys

import openpyxl
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scraper_8888"))

import run_scraper_8888 as run  # noqa: E402
import scraper_8888 as s8  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "8888")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def api(monkeypatch):
    """Serve recorded replies: tournament 1 = football, 2 = tennis, 3 = basketball; 2936 via slug."""
    calls = []
    by_id = {1: "tournament_football.json", 2: "tournament_tennis.json", 3: "tournament_basketball.json",
             2936: "tournament_football.json", 2980: "tournament_basketball.json"}

    def fake(path, params=None):
        calls.append((path, params))
        if path == "v3/widgets/top-tournaments":
            return fixture("top_tournaments.json")
        if path == "v2/widgets/sports/67/tournaments":
            return fixture("sports_67_tournaments.json")
        if path.startswith("v2/widgets/sports/"):
            return {"data": []}
        tid = int(path.rsplit("/", 1)[1])
        if tid not in by_id:
            raise s8.ScrapeError("not found on 8888 (HTTP 404): the tournament may have been removed")
        return fixture(by_id[tid])

    monkeypatch.setattr(s8, "fetch_json", fake)
    s8._slug_cache.clear()
    s8._menus_loaded.clear()
    return calls


# --- time ----------------------------------------------------------------------------
@pytest.mark.parametrize("utc, expected", [
    ("2026-11-08T14:00:00Z", ("Sunday, 08.11.2026", "16:00")),        # winter: UTC+2
    ("2026-11-07T13:30:00Z", ("Saturday, 07.11.2026", "15:30")),      # Premier League 15:30 in the template
    ("2026-07-01T13:30:00Z", ("Wednesday, 01.07.2026", "16:30")),     # summer: UTC+3
    ("2026-03-29T00:59:00Z", ("Sunday, 29.03.2026", "02:59")),        # just before the clocks go forward
    ("2026-03-29T01:00:00Z", ("Sunday, 29.03.2026", "04:00")),
    ("2026-10-25T00:59:00Z", ("Sunday, 25.10.2026", "03:59")),        # just before the clocks go back
    ("2026-10-25T01:00:00Z", ("Sunday, 25.10.2026", "03:00")),
    ("2026-12-31T22:30:00Z", ("Friday, 01.01.2027", "00:30")),        # crosses into the next day/year
])
def test_bulgarian_day_and_time(utc, expected):
    assert s8.day_and_time(utc) == expected


# --- links ---------------------------------------------------------------------------
@pytest.mark.parametrize("link, expected", [
    ("2936", ("id", 2936)),
    ("2936.0", ("id", 2936)),
    ("https://8888punkt.bg/#/sport/66/category/12/tournament/2936", ("id", 2936)),
    ("https://8888punkt.bg/#/tournaments/2936", ("id", 2936)),
    ("https://www.8888punkt.bg/sports?tournamentIds=2980", ("id", 2980)),
    ("8888punkt.bg/#/tournament-18320", ("id", 18320)),
    ("https://8888punkt.bg/#/custom/england-premier-league-2026", ("slug", "england-premier-league-2026")),
    ("https://8888punkt.bg/usa-nba", ("slug", "usa-nba")),
    # Stefan's own links file:
    ("https://8888punkt.bg/#/tournaments/champions-league-2026", ("slug", "champions-league-2026")),
    ("https://8888punkt.bg/#/tournaments/england-premier-league-2026", ("slug", "england-premier-league-2026")),
    ("https://8888punkt.bg/#/tournaments/us-nba", ("slug", "us-nba")),
    ("https://8888punkt.bg/#/tournaments/67/2995/matches", ("id", 2995)),     # 67 = basketball, not the id
])
def test_links_understood(link, expected):
    assert s8.parse_tournament_url(link) == expected


@pytest.mark.parametrize("bad", ["https://inbetsport.com/tournament/2936", "hello", "https://8888punkt.bg/#/"])
def test_bad_links_rejected(bad):
    with pytest.raises(s8.TournamentUrlError):
        s8.parse_tournament_url(bad)


# --- parsing the real replies ----------------------------------------------------------
def raw_events(name):
    t = fixture(name)["sports"][0]["categories"][0]["tournaments"][0]
    return {f"#{e['id']}": e for e in t["sportEvents"]}, t


@pytest.mark.parametrize("name, sport", [("tournament_football.json", "football"),
                                         ("tournament_tennis.json", "tennis"),
                                         ("tournament_basketball.json", "basketball")])
def test_rows_match_the_site(name, sport):
    rows, _ = s8.parse_tournament(fixture(name))
    events, tour = raw_events(name)
    assert rows, "expected matches"
    uid = s8.SPORTS[sport]
    for r in rows:
        ev = events[r["Match ID"]]
        assert not ev["isLive"]
        market = next(m for mt in ev["marketTypes"] if mt.get("uId") == uid for m in mt["markets"])
        by_type = {s["typeId"]: s["odds"] for s in market["selections"]}
        home = next(v for k, v in by_type.items() if k in s8.HOME_TYPES)
        away = next(v for k, v in by_type.items() if k in s8.AWAY_TYPES)
        assert (r["Home Odds"], r["Away Odds"]) == (home, away)
        if sport == "football":
            assert r["Draw Odds"] == next(v for k, v in by_type.items() if k in s8.DRAW_TYPES)
        else:
            assert r["Draw Odds"] == 10                          # client's rule for 2-way sports
        assert r["Match"] == f"{ev['teams'][0]['name']} - {ev['teams'][1]['name']}"
        assert (r["Tournament"], r["Tournament ID"]) == (tour["name"], tour["id"])
        assert (r["Day"], r["Time"]) == s8.day_and_time(ev["startTime"])
    starts = [events[r["Match ID"]]["startTime"] for r in rows]
    assert starts == sorted(starts)                              # in kick-off order


def test_suspended_and_live_matches_are_left_out():
    data = fixture("tournament_football.json")
    evs = data["sports"][0]["categories"][0]["tournaments"][0]["sportEvents"]
    good = [e for e in evs if not e["isLive"]]
    good[0]["isLive"] = True
    for mt in good[1]["marketTypes"]:
        for m in mt["markets"]:
            m["status"] = -1
    rows, notes = s8.parse_tournament(data)
    ids = {r["Match ID"] for r in rows}
    assert f"#{good[0]['id']}" not in ids and f"#{good[1]['id']}" not in ids
    assert any("suspended" in n for n in notes)


# --- scraping a links file end to end ---------------------------------------------------
def test_links_file_to_template(api, tmp_path):
    links = tmp_path / "links.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Tournament", "Link"])                             # header row is fine
    ws.append(["Football", "https://8888punkt.bg/#/sport/66/tournament/1"])
    ws.append(["Tennis", "https://8888punkt.bg/#/tournament/2"])
    ws.append(["Basketball", 3])
    ws.append(["Premier League page", "https://8888punkt.bg/#/custom/england-premier-league-2026"])
    ws.append(["Removed", "https://8888punkt.bg/#/tournament/999"])
    wb.save(links)
    assert run.validate_input_file(str(links)) == []

    log = []
    rows = run.scrape_all(run.read_tournament_urls(str(links)), (0, 0), log=log.append)
    out = tmp_path / "out.xlsx"
    run.write_matches(rows, str(out))

    football = s8.parse_tournament(fixture("tournament_football.json"))[0]
    tennis = s8.parse_tournament(fixture("tournament_tennis.json"))[0]
    basket = s8.parse_tournament(fixture("tournament_basketball.json"))[0]
    assert len(rows) == len(football) + len(tennis) + len(basket)   # Premier League page = same data, de-duplicated
    assert any("duplicate" in line and "-> 0 match" in line for line in log)
    assert any("not found on 8888" in line for line in log)        # a dead link is named, run carries on

    sheet = openpyxl.load_workbook(out)["Formatted Matches"]
    header = [c.value for c in sheet[1]]
    assert header == ["Match ID", "Day", "Time", "Match", "Home Odds", "Draw Odds", "Away Odds",
                      "Tournament", "Tournament ID"]
    assert sheet.max_column == 9                                   # nothing in J or K
    first = [c.value for c in sheet[2]]
    assert isinstance(first[4], float) and isinstance(first[8], int) and first[0].startswith("#")


def test_bad_rows_in_links_file_are_named(tmp_path):
    links = tmp_path / "links.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ok", 2936])
    ws.append(["wrong site", "https://inbetsport.com/tournament/5"])
    wb.save(links)
    errors = run.validate_input_file(str(links))
    assert len(errors) == 1 and errors[0].startswith("Row 2:")


def test_sample_links_file_is_valid(tmp_path):
    path = tmp_path / "sample.xlsx"
    run.write_sample_links(str(path))
    assert run.validate_input_file(str(path)) == []
    assert len(run.read_tournament_urls(str(path))) == len(run.SAMPLE_LINKS)


# --- talking to the server ---------------------------------------------------------------
class FakeResponse:
    def __init__(self, status, body=None, text="<html>blocked</html>"):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def test_server_replies(monkeypatch):
    replies = []
    monkeypatch.setattr(s8.SESSION, "get", lambda url, params=None, timeout=None, headers=None: replies.pop(0))
    monkeypatch.setattr(s8.time, "sleep", lambda s: None)

    replies[:] = [FakeResponse(200, {"ok": 1})]
    assert s8.fetch_json("x") == {"ok": 1}
    replies[:] = [FakeResponse(503), FakeResponse(200, {"ok": 2})]
    assert s8.fetch_json("x") == {"ok": 2}                         # temporary error retried
    replies[:] = [FakeResponse(403)]
    with pytest.raises(s8.ScrapeError, match="no VPN"):
        s8.fetch_json("x")
    replies[:] = [FakeResponse(401)]
    with pytest.raises(s8.ScrapeError, match="asked for a key"):
        s8.fetch_json("x")
    replies[:] = [FakeResponse(200)]
    with pytest.raises(s8.ScrapeError, match="VPN or outside Bulgaria"):
        s8.fetch_json("x")


def test_request_carries_brand_and_site(monkeypatch):
    seen = {}

    def get(url, params=None, timeout=None, headers=None):
        seen.update(url=url, params=params, headers=headers)
        return FakeResponse(200, {})

    monkeypatch.setattr(s8.SESSION, "get", get)
    s8.fetch_json("v2/widgets/tournaments/2936", {"sportEventTypes": "prematch"})
    assert seen["url"] == "https://cdn-bgt-api.sportinno.net/api/v2/widgets/tournaments/2936"
    assert seen["params"] == {"sportEventTypes": "prematch", "bid": "1049641141"}
    assert s8.SESSION.headers["Origin"] == "https://8888punkt.bg"
    import uuid
    uuid.UUID(seen["headers"]["trace-id"])                         # a fresh trace-id, like the site sends


def test_page_names_resolve_from_the_sites_own_lists(api):
    assert s8.tournament_id_for("https://8888punkt.bg/#/tournaments/england-premier-league-2026") == 2936
    assert s8.tournament_id_for("https://8888punkt.bg/#/tournaments/usa-nba") == 2980   # from the basketball menu


def test_unknown_page_name_is_not_guessed(api):
    with pytest.raises(s8.ScrapeError) as e:
        s8.tournament_id_for("https://8888punkt.bg/#/tournaments/us-nba")
    assert "Did you mean 'usa-nba'" in str(e.value) and "НБА" in str(e.value)


def test_all_links_failing_is_said_clearly(monkeypatch):
    monkeypatch.setattr(s8, "fetch_json", lambda path, params=None: (_ for _ in ()).throw(
        s8.ScrapeError("the 8888 odds server asked for a key it did not get (HTTP 401).")))
    log = []
    rows = run.scrape_all(["2936", "2980"], (0, 0), log=log.append)
    assert rows == [] and any("No tournament could be read" in line for line in log)
