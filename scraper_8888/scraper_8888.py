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
def _main_market(event, sport):
    uid = SPORTS[sport]
    for mt in event.get("marketTypes") or []:
        if mt.get("uId") == uid or (mt.get("uId") is None and mt.get("id") == uid):
            for market in mt.get("markets") or []:
                if market.get("status") == 1:
                    return market
    return None


def _odds(market, sport):
    """(home, draw, away) from a main market, or None if any needed price is missing/suspended."""
    sels = [s for s in market.get("selections") or [] if s.get("isActive", True) and s.get("status", 1) == 1]
    home = draw = away = None
    for s in sels:
        t, name = s.get("typeId"), str(s.get("typeName") or s.get("name") or "").strip().upper()
        if t in HOME_TYPES or name == "1":
            home = s.get("odds")
        elif t in DRAW_TYPES or name in ("X", "Х"):
            draw = s.get("odds")
        elif t in AWAY_TYPES or name == "2":
            away = s.get("odds")
    if sport != "football":
        draw = NO_DRAW_ODDS
    if None in (home, draw, away):
        return None
    return float(home), float(draw), float(away)


def _teams(event):
    teams = [t.get("name") for t in event.get("teams") or [] if t.get("name")]
    if len(teams) >= 2:
        return teams[0], teams[1]
    home, _, away = str(event.get("name") or "").partition(" vs ")
    return home.strip(), away.strip()


def parse_tournament(data):
    """Tournament API reply -> (rows, notes). Upcoming matches with open main-market odds only."""
    rows, skipped = [], {"live": 0, "no odds": 0, "other sport": 0}
    for sport_block in data.get("sports") or []:
        sport = sport_block.get("identifier")
        for cat in sport_block.get("categories") or []:
            for tour in cat.get("tournaments") or []:
                for ev in tour.get("sportEvents") or []:
                    if sport not in SPORTS:
                        skipped["other sport"] += 1
                        continue
                    if ev.get("isLive"):
                        skipped["live"] += 1
                        continue
                    market = _main_market(ev, sport)
                    odds = _odds(market, sport) if market else None
                    if not odds:
                        skipped["no odds"] += 1
                        continue
                    home, away = _teams(ev)
                    day, hour = day_and_time(ev["startTime"])
                    rows.append({
                        "Match ID": f"#{ev['id']}", "Day": day, "Time": hour,
                        "Match": f"{home} - {away}",
                        "Home Odds": odds[0], "Draw Odds": odds[1], "Away Odds": odds[2],
                        "Tournament": tour.get("name"), "Tournament ID": tour.get("id"),
                        "_start": ev["startTime"],
                    })
    rows.sort(key=lambda r: (r["_start"], r["Match"]))
    for r in rows:
        r.pop("_start")
    notes = []
    if skipped["no odds"]:
        notes.append(f"{skipped['no odds']} match(es) skipped: main odds not offered or suspended right now")
    if skipped["other sport"]:
        notes.append(f"{skipped['other sport']} match(es) skipped: not football, basketball or tennis")
    return rows, notes


def scrape_tournament(url, on_wait=None):
    """Links-file URL -> (rows, messages). Never raises: problems come back as messages."""
    try:
        tid = tournament_id_for(url)
        data = fetch_json(f"v2/widgets/tournaments/{tid}", {"sportEventTypes": "prematch"})
    except (TournamentUrlError, ScrapeError) as e:
        return [], [str(e)]
    rows, notes = parse_tournament(data)
    if not rows and not notes:
        notes.append("no upcoming matches in this tournament right now")
    return rows, notes


def _wait(min_seconds, max_seconds, on_wait=None):
    seconds = random.uniform(min_seconds, max_seconds)
    if on_wait:
        on_wait(seconds)
    time.sleep(seconds)
