"""
Talk to efbet the way the efbet page does: its BetConstruct "swarm" server,
over a WebSocket opened from inside an efbetshop.com page.

The robot opens efbetshop.com/bg in a hidden browser on this PC (Chrome, Edge
or Brave, like the 8888 robots), then, inside that page, opens its own
connection to the swarm address the site uses (conf.json -> app.swarm.socketUrl)
and starts a session like the page does. So every command goes out from the
real site, with the PC's own Bulgarian connection, exactly as when the client
clicks.

Commands used (all recorded on the client's PC, tests/fixtures/efbet):
  get                      the matches and their early payout odds
  get_sport_bonus_rules    the accumulator bonus by number of matches
  create_bet_book_v2       makes the booking (the ticket number for the barcode)
  get_booking_by_id_v2     reads a booking back
"""

import contextlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper_8888"))

SITE = "https://efbetshop.com/bg"
CONF_PATH = "/conf.json"
DEFAULT_SOCKET = "wss://eu-swarm-newm.vmemkhhgjigrjefb.com"
SITE_ID = 18762167
LANGUAGE = "eng"            # English team names, like the client's efbet scraper files
SOURCE = 42                 # the page's own value for a desktop browser
RELEASE_DATE = "09/23/2026-13:04"
TIMEOUT = 30                # seconds for one answer
BROWSER_EXECUTABLE = os.environ.get("SCRAPER8888_BROWSER") or None
PAGE_TIMEOUT = 60

_session = None             # the open browser page during a run (None in tests)
READ_ONLY = {"get", "get_booking_by_id_v2", "get_sport_bonus_rules"}   # safe to send twice


class SwarmError(RuntimeError):
    """efbet could not be reached, or it answered with an error."""


_CONNECT_JS = """
async ({confPath, fallback, session, timeoutMs, force}) => {
  const k = window.__kc;
  if (!force && k && k.ws && k.ws.readyState === 1 && k.session) return {ok: true, session: k.session, reused: true};
  if (k && k.ws) { try { k.ws.onmessage = null; k.ws.close(); } catch (e) {} }
  let url = null;
  try {
    const r = await fetch(confPath, {cache: 'no-store'});
    const c = await r.json();
    url = c && c.app && c.app.swarm && c.app.swarm.socketUrl;
  } catch (e) {}
  url = url || fallback;
  const state = {ws: null, pending: {}, n: 0, session: null, url};
  window.__kc = state;
  window.__kcSend = (command, params, ms) => new Promise((resolve) => {
    const s = window.__kc;
    if (!s || !s.ws || s.ws.readyState !== 1) { resolve({ok: false, sent: false, error: 'not connected'}); return; }
    const rid = 'kc' + (++s.n) + '_' + Date.now();
    const timer = setTimeout(() => { delete s.pending[rid]; resolve({ok: false, sent: true, error: 'no answer in time'}); }, ms);
    s.pending[rid] = (m) => { clearTimeout(timer); resolve({ok: true, reply: m}); };
    try { s.ws.send(JSON.stringify({command, params, rid})); }
    catch (e) { clearTimeout(timer); delete s.pending[rid]; resolve({ok: false, sent: false, error: String(e)}); }
  });
  try {
    state.ws = new WebSocket(url);
  } catch (e) {
    return {ok: false, error: 'cannot open ' + url + ': ' + e};
  }
  state.ws.onmessage = (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
    const p = state.pending[m.rid];
    if (p) { delete state.pending[m.rid]; p(m); }
  };
  const opened = await new Promise((resolve) => {
    const t = setTimeout(() => resolve('no answer from ' + url), timeoutMs);
    state.ws.onopen = () => { clearTimeout(t); resolve(true); };
    state.ws.onerror = () => { clearTimeout(t); resolve('connection to ' + url + ' failed'); };
  });
  if (opened !== true) return {ok: false, error: opened};
  const r = await window.__kcSend('request_session', session, timeoutMs);
  if (!r.ok) return {ok: false, error: 'session: ' + r.error};
  if (r.reply.code !== 0) return {ok: false, error: 'session refused: ' + JSON.stringify(r.reply).slice(0, 300)};
  state.session = r.reply.data || {};
  return {ok: true, session: state.session, url};
}
"""

_SEND_JS = """
async ({command, params, timeoutMs}) => {
  if (!window.__kcSend) return {ok: false, sent: false, error: 'not connected'};
  return await window.__kcSend(command, params, timeoutMs);
}
"""


def session_params(language=LANGUAGE):
    return {"language": language, "site_id": SITE_ID, "source": SOURCE, "release_date": RELEASE_DATE}


class EfbetPage:
    """A hidden browser on efbetshop.com with one swarm connection inside it."""

    def __init__(self, log=print, executable=None):
        from browser_session import BrowserSession
        self._browser = BrowserSession(SITE, "-", log=log, executable=executable, key_timeout=PAGE_TIMEOUT)
        self.log = log
        self.page = None
        self.session = {}

    def start(self):
        from browser_session import BrowserError
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
        b = self._browser
        b._pw = sync_playwright().start()
        problems = []
        for headless in (True, False):          # hidden first; off-screen window if the site insists
            try:
                b._browser = b._launch(headless)
            except BrowserError as e:
                problems.append(str(e))
                continue
            context = b._browser.new_context(locale="bg-BG", timezone_id="Europe/Sofia")
            self.page = context.new_page()
            mode = "a hidden" if headless else "an off-screen"
            self.log(f"Opening efbetshop.com in {mode} {b.browser_name} window...")
            try:
                self.page.goto(SITE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT * 1000)
                self._connect()
                self.log("  connected to efbet")
                return self
            except (PlaywrightError, SwarmError) as e:
                problems.append(str(e).splitlines()[0])
                self.log(f"  ! {problems[-1]}")
                b._browser.close()
        raise SwarmError("efbetshop.com could not be opened from this PC (" + " | ".join(problems) +
                         "). Check that the site opens normally in a browser on this PC "
                         "(Bulgarian connection, no VPN).")

    def _connect(self, force=False):
        result = self.page.evaluate(_CONNECT_JS, {"confPath": CONF_PATH, "fallback": DEFAULT_SOCKET,
                                                  "session": session_params(), "timeoutMs": TIMEOUT * 1000,
                                                  "force": force})
        if not result.get("ok"):
            raise SwarmError(f"efbet did not open a session: {result.get('error')}")
        self.session = result.get("session") or {}

    def call(self, command, params, retry=True):
        """One swarm command -> its reply's data.

        A closed or silent connection is opened again and the command sent once
        more, but a booking only when efbet surely never received it: a booking
        is never repeated (no answer -> error, nothing is guessed).
        """
        result = self.page.evaluate(_SEND_JS, {"command": command, "params": params, "timeoutMs": TIMEOUT * 1000})
        if not result.get("ok") and retry and (not result.get("sent") or command in READ_ONLY):
            self.log("  (the efbet connection stopped answering, opening it again...)")
            self._connect(force=True)
            return self.call(command, params, retry=False)
        if not result.get("ok"):
            raise SwarmError(f"efbet: {command}: {result.get('error')}")
        reply = result.get("reply") or {}
        if reply.get("code") != 0:
            raise SwarmError(f"efbet refused {command} (code {reply.get('code')}: "
                             f"{str(reply.get('msg') or reply.get('data') or '')[:200]})")
        return reply.get("data")

    def close(self):
        self._browser.close()


@contextlib.contextmanager
def connection(log=print):
    """Open efbet for the duration of a run."""
    global _session
    page = EfbetPage(log=log, executable=BROWSER_EXECUTABLE)
    try:
        page.start()
        _session = page
        yield page
    finally:
        _session = None
        page.close()


def call(command, params):
    if _session is None:
        raise SwarmError("efbet is not open")
    return _session.call(command, params)


def partner_config():
    return dict((_session.session if _session else {}).get("partner_config") or {})


# --- commands -------------------------------------------------------------------------
GAME_FIELDS = ["id", "team1_name", "team2_name", "start_ts", "is_live", "is_blocked", "type", "sport_alias",
               "region_alias"]
MARKET_FIELDS = ["id", "name", "type", "market_type", "display_key", "express_id", "base"]
EVENT_FIELDS = ["id", "price", "type_1", "name", "base"]


def games_query(match_ids):
    """The get command for these matches with their 1X2 markets (normal and early payout)."""
    return {"source": "betting",
            "what": {"game": GAME_FIELDS, "market": MARKET_FIELDS, "event": EVENT_FIELDS},
            "where": {"game": {"id": {"@in": [int(i) for i in match_ids]}},
                      "market": {"display_key": {"@in": ["WINNER", "WINNER_EP"]}}}}


def get_games(match_ids):
    """{match id (str): match with its markets} for the matches efbet still lists."""
    data = call("get", games_query(match_ids)) or {}
    games = (data.get("data") or {}).get("game") or {}
    return {str(k): v for k, v in games.items()}


def bonus_rules():
    data = call("get_sport_bonus_rules", {}) or {}
    return data.get("details") or []


def create_booking(params):
    return call("create_bet_book_v2", params)


def get_booking(booking_id):
    return call("get_booking_by_id_v2", {"booking_id": int(booking_id)})
