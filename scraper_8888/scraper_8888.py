"""
8888 (8888punkt.bg) odds scraper: football, basketball and tennis.

8888punkt.bg loads its odds from the SportInnovation API (cdn-bgt-api.sportinno.net),
brand id 1049641141. One request per tournament returns every upcoming match with
its main market, so there is no per-match crawling:

    GET /api/v2/widgets/tournaments/{id}?bid=1049641141&sportEventTypes=prematch

Main market per sport (matched on the market's "uId", which stays the same across
the site's display variants):
    football    23   Краен Резултат (1 X 2)         -> Home / Draw / Away
    basketball  212  Победител (вкл. Продължение)   -> Home / Away, Draw = 10
    tennis      16   Победител                      -> Home / Away, Draw = 10
Tournaments from 8888's other data provider send the same market under another
number; it is then found by its exact name (see MAIN_MARKET_NAMES). A match whose
odds are missing from the tournament list is read from its own match page
(GET /api/v2/widgets/events/{id}).

Early payout ("Ранно изплащане") on 8888 is a promotion on the top football,
tennis and basketball tournaments, not a separate odds market, so the odds are
these main-market odds; the client picks the tournaments in the links file.

Output rows use the client's template (Formatted Matches): Match ID (#event id),
Day ("Sunday, 08.11.2026"), Time ("16:00", Bulgarian time), Match ("Home - Away"),
Home/Draw/Away odds, Tournament, Tournament ID. Column K is left for the client.

The site itself blocks VPNs and foreign IPs, so the scraper is run from a normal
Bulgarian connection (the client's PC).

The odds server also wants the short-lived visitor key the 8888 page gets for
itself, so live runs go through a hidden browser (see browser_session.py):
odds_connection() opens it, and fetch_json() then asks from inside the page.
"""

import difflib
import random
import re
import contextlib
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

API = "https://cdn-bgt-api.sportinno.net/api"
SITE = "https://8888punkt.bg"
BRAND_ID = "1049641141"
SITE_HOSTS = ("8888punkt.bg", "www.8888punkt.bg")

COLUMNS = ["Match ID", "Day", "Time", "Match", "Home Odds", "Draw Odds", "Away Odds",
           "Tournament", "Tournament ID"]

SPORTS = {"football": 23, "basketball": 212, "tennis": 16}   # sport -> main market uId
NO_DRAW_ODDS = 10            # client's rule: Draw column for 2-way sports
MIN_TOURNAMENT_WAIT_SECONDS = 3
MAX_TOURNAMENT_WAIT_SECONDS = 6

# Selection typeIds seen on the API. Anything else falls back to the 1 / X / 2 name.
HOME_TYPES = {49, 27556, 166}
DRAW_TYPES = {50, 27561}
AWAY_TYPES = {51, 27557, 167}

# The same headers the 8888 site's own page sends (recorded from the browser),
# including a fresh random "trace-id" on every request.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.6",
    "Origin": SITE,
    "Referer": SITE + "/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
}
TIMEOUT = 30
RETRY_STATUS = {429, 500, 502, 503, 504}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

USE_BROWSER = True        # live runs read through the 8888 page (tests switch this off)
# None = find Chrome / Edge / Brave on the PC; tests or a special setup can point at one.
BROWSER_EXECUTABLE = os.environ.get("SCRAPER8888_BROWSER") or None
KEY_TIMEOUT = 60          # seconds to wait for the 8888 page to get its key
_browser = None           # the open BrowserSession during a run


@contextlib.contextmanager
def odds_connection(log=print):
    """Open the hidden 8888 browser for the duration of a run (no-op when USE_BROWSER is off)."""
    global _browser
    if not USE_BROWSER:
        yield
        return
    from browser_session import BrowserError, BrowserSession
    session = BrowserSession(SITE + "/", urlsplit(API).netloc + "/api", log=log,
                             executable=BROWSER_EXECUTABLE, key_timeout=KEY_TIMEOUT)
    try:
        try:
            session.start()
        except BrowserError as e:
            raise ScrapeError(str(e))
        _browser = session
        yield
    finally:
        _browser = None
        session.close()


class TournamentUrlError(ValueError):
    """A links-file entry that is not a usable 8888 tournament link."""


class ScrapeError(RuntimeError):
    """The site could not be read (blocked, offline, unexpected reply)."""


# --- time --------------------------------------------------------------------------
def _last_sunday(year, month):
    d = datetime(year, month + 1, 1) - timedelta(days=1) if month < 12 else datetime(year, 12, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def to_bulgarian_time(iso_utc):
    """'2026-11-08T14:00:00Z' -> datetime in Bulgarian time (EET/EEST, EU summer-time rule)."""
    utc = datetime.strptime(iso_utc.replace("Z", "")[:19], "%Y-%m-%dT%H:%M:%S")
    start = _last_sunday(utc.year, 3).replace(hour=1)    # summer time: last Sun of March, 01:00 UTC
    end = _last_sunday(utc.year, 10).replace(hour=1)     # until last Sun of October, 01:00 UTC
    return utc + timedelta(hours=3 if start <= utc < end else 2)


def day_and_time(iso_utc):
    local = to_bulgarian_time(iso_utc)
    return f"{WEEKDAYS[local.weekday()]}, {local:%d.%m.%Y}", f"{local:%H:%M}"


# --- links -------------------------------------------------------------------------
# Links seen on 8888punkt.bg:
#   https://8888punkt.bg/#/tournaments/67/2995/matches          -> sport 67, tournament 2995
#   https://8888punkt.bg/#/tournaments/england-premier-league-2026  -> tournament page name
_ID_PATTERNS = (
    re.compile(r"tournaments?/\d+/(\d+)", re.I),      # tournaments/<sport>/<tournament>
    re.compile(r"tournaments?[/=-](\d+)", re.I),
    re.compile(r"tournamentIds?=(\d+)", re.I),
)
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")


def parse_tournament_url(url):
    """Links-file cell -> ('id', 2936) or ('slug', 'england-premier-league-2026').

    Accepts an 8888punkt.bg tournament link (any form that names the tournament
    id, or a tournament page such as .../england-premier-league-2026), or just
    the tournament id number. Raises TournamentUrlError otherwise.
    """
    text = str(url or "").strip()
    if re.fullmatch(r"\d+(\.0)?", text):
        return "id", int(float(text))
    parts = urlsplit(text if "://" in text else "https://" + text)
    if parts.netloc.lower() not in SITE_HOSTS:
        raise TournamentUrlError(f"not an 8888punkt.bg link: {text}")
    tail = f"{parts.path}?{parts.query}#{parts.fragment}"
    for pattern in _ID_PATTERNS:
        m = pattern.search(tail)
        if m:
            return "id", int(m.group(1))
    q = parse_qs(parts.query)
    for key in ("tournament", "tournamentId", "id"):
        if q.get(key, [""])[0].isdigit():
            return "id", int(q[key][0])
    slugs = _SLUG.findall((parts.path + "/" + parts.fragment).lower())
    if slugs:
        return "slug", slugs[-1]
    raise TournamentUrlError(f"no tournament found in this link: {text}")


# --- API ---------------------------------------------------------------------------
class _Reply:
    """Same shape as a requests response, for replies read through the browser."""

    def __init__(self, status, text):
        self.status_code, self.text = status, text

    def json(self):
        import json
        return json.loads(self.text)


def _get(url, params):
    if _browser is not None:
        status, text = _browser.fetch(f"{url}?{urlencode(params)}")
        if status == 0:
            raise requests.ConnectionError(text)
        return _Reply(status, text)
    return SESSION.get(url, params=params, timeout=TIMEOUT, headers={"trace-id": str(uuid.uuid4())})


def fetch_json(path, params=None):
    params = dict(params or {}, bid=BRAND_ID)
    url = f"{API}/{path.lstrip('/')}"
    last = None
    for attempt in range(3):
        try:
            r = _get(url, params)
        except (requests.ConnectionError, requests.Timeout) as e:
            last = ScrapeError(f"no connection to the 8888 odds server ({e.__class__.__name__})")
        else:
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    raise ScrapeError("the 8888 odds server sent a reply that is not data "
                                      "(is this PC on a VPN or outside Bulgaria?)")
            if r.status_code == 401:
                raise ScrapeError("the 8888 odds server asked for a key it did not get (HTTP 401). "
                                  "The site may have changed how it loads odds: send the activity log to support.")
            if r.status_code == 403:
                raise ScrapeError("the 8888 odds server refused the request (HTTP 403). "
                                  "Use a normal Bulgarian connection, no VPN.")
            if r.status_code == 404:
                raise ScrapeError("not found on 8888 (HTTP 404): the tournament may have been removed")
            last = ScrapeError(f"the 8888 odds server answered HTTP {r.status_code}")
            if r.status_code not in RETRY_STATUS:
                raise last
        time.sleep(2 * (attempt + 1))
    raise last


SPORT_IDS = {"football": 66, "basketball": 67, "tennis": 68}
_slug_cache = {}          # page name -> (tournament id, title)
_menus_loaded = set()


def _remember_pages(nodes):
    for n in nodes or []:
        ident = (n.get("customPageUrlIdentifier") or "").lower()
        if ident:
            _slug_cache.setdefault(ident, (n["id"], n.get("title") or n.get("name") or ""))
        _remember_pages(n.get("children"))


def _load_menus():
    """Learn page name -> tournament id from the site's own lists (top list + each sport's menu)."""
    if "top" not in _menus_loaded:
        _remember_pages(fetch_json("v3/widgets/top-tournaments").get("tournaments"))
        _menus_loaded.add("top")
    for sport_id in SPORT_IDS.values():
        if sport_id not in _menus_loaded:
            _remember_pages(fetch_json(f"v2/widgets/sports/{sport_id}/tournaments").get("data"))
            _menus_loaded.add(sport_id)


def resolve_slug(slug):
    """Tournament page name (england-premier-league-2026) -> tournament id.

    Only an exact page name is used. A name the site does not list is never
    guessed (that could scrape the wrong league); the closest listed page is
    suggested in the message instead.
    """
    if slug not in _slug_cache:
        if "top" not in _menus_loaded:
            _remember_pages(fetch_json("v3/widgets/top-tournaments").get("tournaments"))
            _menus_loaded.add("top")
        if slug not in _slug_cache:
            _load_menus()
    if slug in _slug_cache:
        return _slug_cache[slug][0]
    close = difflib.get_close_matches(slug, list(_slug_cache), n=1, cutoff=0.6)
    hint = f" Did you mean '{close[0]}' ({_slug_cache[close[0]][1]})?" if close else ""
    raise ScrapeError(f"8888 has no tournament page called '{slug}' right now.{hint} "
                      "Open the tournament on the site and copy its link again.")


def tournament_id_for(url):
    kind, value = parse_tournament_url(url)
    return value if kind == "id" else resolve_slug(value)


# --- parsing -----------------------------------------------------------------------
# 8888 takes its odds from more than one data provider. Most tournaments use the
# uIds above, but some (ITF tennis, for example) send the same full-match market
# under another number and with plain "1" / "2" selections. Those are found by the
# market's exact name; a part-of-match market ("1-ви Сет - Победител",
# "1-ва Четвърт - Победител") never matches an exact name.
MAIN_MARKET_NAMES = {
    "football": ("краен резултат",),
    "basketball": ("победител (вкл. продължение)", "победител"),
    "tennis": ("победител",),
}
EVENT_PAGE_LIMIT = 25           # match pages read per tournament when the list lacks the odds
EVENT_PAGE_WAIT = (0.4, 1.0)    # seconds between those reads
DEBUG_DIR = None                # folder for the raw reply of tournaments with skipped matches


def _open_market(market_type):
    for market in market_type.get("markets") or []:
        if market.get("status") == 1:
            return market
    return None


def _main_market(event, sport):
    types = event.get("marketTypes") or []
    uid = SPORTS[sport]
    for mt in types:
        if mt.get("uId") == uid or (mt.get("uId") is None and mt.get("id") == uid):
            market = _open_market(mt)
            if market:
                return market
    for wanted in MAIN_MARKET_NAMES[sport]:
        for mt in types:
            if _clean(mt.get("name")).lower() == wanted:
                market = _open_market(mt)
                if market:
                    return market
    return None


def _odds(market, sport, teams=("", "")):
    """(home, draw, away) from a main market, or None if it is not a clean 1 X 2 / 1 2 market.

    A selection counts by its type number, its "1" / "X" / "2" name, or (some
    providers) the team's own name. Any unknown, missing, suspended or doubled
    selection means the match is skipped rather than guessed.
    """
    sels = [s for s in market.get("selections") or [] if s.get("isActive", True) and s.get("status", 1) == 1]
    if len(sels) != (3 if sport == "football" else 2):
        return None
    home_team, away_team = (_clean(t).upper() for t in teams)
    found = {}
    for s in sels:
        t, name = s.get("typeId"), _clean(s.get("typeName") or s.get("name")).upper()
        if t in HOME_TYPES or name == "1" or (home_team and name == home_team):
            side = "home"
        elif t in DRAW_TYPES or name in ("X", "Х"):
            side = "draw"
        elif t in AWAY_TYPES or name == "2" or (away_team and name == away_team):
            side = "away"
        else:
            return None
        if side in found or s.get("odds") is None:
            return None
        found[side] = s.get("odds")
    if sport != "football":
        found["draw"] = NO_DRAW_ODDS
    if set(found) != {"home", "draw", "away"}:
        return None
    return float(found["home"]), float(found["draw"]), float(found["away"])


def _clean(text):
    """'Арсенал ' -> 'Арсенал' (the site sometimes leaves extra spaces in names)."""
    return " ".join(str(text or "").split())


def _teams(event):
    teams = [_clean(t.get("name")) for t in event.get("teams") or [] if _clean(t.get("name"))]
    if len(teams) >= 2:
        return teams[0], teams[1]
    home, _, away = str(event.get("name") or "").partition(" vs ")
    return _clean(home), _clean(away)


def _row(event, sport, tour):
    """One template row for a match with open main odds, else None."""
    home, away = _teams(event)
    if not (home and away):
        return None                                   # not a match (e.g. a tournament-winner bet)
    market = _main_market(event, sport)
    odds = _odds(market, sport, (home, away)) if market else None
    if not odds:
        return None
    day, hour = day_and_time(event["startTime"])
    return {
        "Match ID": f"#{event['id']}", "Day": day, "Time": hour,
        "Match": f"{home} - {away}",
        "Home Odds": odds[0], "Draw Odds": odds[1], "Away Odds": odds[2],
        "Tournament": _clean(tour.get("name")), "Tournament ID": tour.get("id"),
        "_start": event["startTime"],
    }


def _read_tournament(data):
    """Tournament reply -> (rows, matches without odds [(event, sport, tournament)], other-sport count)."""
    rows, missing, other = [], [], 0
    for sport_block in data.get("sports") or []:
        sport = sport_block.get("identifier")
        for cat in sport_block.get("categories") or []:
            for tour in cat.get("tournaments") or []:
                for ev in tour.get("sportEvents") or []:
                    if sport not in SPORTS:
                        other += 1
                    elif not ev.get("isLive"):
                        row = _row(ev, sport, tour)
                        if row:
                            rows.append(row)
                        else:
                            missing.append((ev, sport, tour))
    return rows, missing, other


def _finish(rows, no_odds, other):
    rows.sort(key=lambda r: (r["_start"], r["Match"]))
    for r in rows:
        r.pop("_start")
    notes = []
    if no_odds:
        notes.append(f"{no_odds} match(es) skipped: main odds not offered or suspended right now")
    if other:
        notes.append(f"{other} match(es) skipped: not football, basketball or tennis")
    return rows, notes


def parse_tournament(data):
    """Tournament API reply -> (rows, notes). Upcoming matches with open main-market odds only."""
    rows, missing, other = _read_tournament(data)
    return _finish(rows, len(missing), other)


def _from_match_pages(missing, raw_pages):
    """Read each match page for matches whose odds were not in the tournament list.

    Some tournaments list their matches without the main market; the match's own
    page has every market. Returns the rows found and the matches still without odds.
    """
    rows, still_missing = [], []
    for i, (ev, sport, tour) in enumerate(missing):
        if i >= EVENT_PAGE_LIMIT:
            still_missing.extend(missing[i:])
            break
        if i:
            _wait(*EVENT_PAGE_WAIT)
        try:
            page = fetch_json(f"v2/widgets/events/{ev['id']}")
        except ScrapeError:
            still_missing.extend(missing[i:])        # one failure: the rest would fail the same way
            break
        raw_pages[str(ev["id"])] = page
        row = None
        if isinstance(page, dict) and not page.get("isLive"):
            row = _row(dict(ev, **{k: page[k] for k in ("marketTypes", "teams") if page.get(k)}), sport, tour)
        if row:
            rows.append(row)
        else:
            still_missing.append((ev, sport, tour))
    return rows, still_missing


def _save_raw(tid, data, raw_pages):
    """Keep the site's reply for a tournament with skipped matches, for checking later."""
    if not DEBUG_DIR:
        return None
    import json
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        path = os.path.join(DEBUG_DIR, f"tournament_{tid}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"tournament": data, "match_pages": raw_pages}, f, ensure_ascii=False, indent=1)
        return path
    except OSError:
        return None


def scrape_tournament(url, on_wait=None):
    """Links-file URL -> (rows, messages). Never raises: problems come back as messages."""
    try:
        tid = tournament_id_for(url)
        data = fetch_json(f"v2/widgets/tournaments/{tid}", {"sportEventTypes": "prematch"})
    except (TournamentUrlError, ScrapeError) as e:
        return [], [str(e)]
    rows, missing, other = _read_tournament(data)
    raw_pages = {}
    if missing:
        found, missing = _from_match_pages(missing, raw_pages)
        rows += found
    rows, notes = _finish(rows, len(missing), other)
    if missing:
        saved = _save_raw(tid, data, raw_pages)
        if saved:
            notes[0] += f" (site data saved in {saved})"
    if not rows and not notes:
        notes.append("no upcoming matches in this tournament right now")
    return rows, notes


def _wait(min_seconds, max_seconds, on_wait=None):
    seconds = random.uniform(min_seconds, max_seconds)
    if on_wait:
        on_wait(seconds)
    time.sleep(seconds)
