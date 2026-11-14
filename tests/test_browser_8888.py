"""
The 8888 scraper through a real browser, against a fake 8888 site + odds server
that work like the real ones: the page gets a fresh visitor key each time it
opens, the odds server refuses anything without a valid key (HTTP 401), and
keys can expire mid-run. Needs a Chrome/Edge browser (skipped otherwise).
"""

import json
import os
import shutil
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scraper_8888"))

import run_scraper_8888 as run  # noqa: E402
import scraper_8888 as s8  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "8888")
LOCAL_CHROME = shutil.which("google-chrome") or shutil.which("chromium")


class FakeOdds:
    """Shared state of the fake site + odds server."""

    def __init__(self):
        self.valid, self.page_loads, self.served, self.trace_ids = set(), 0, [], []
        self.expire_after = None          # invalidate all keys once, after this many tournament replies
        self.page_makes_key = True

    def tournament(self, tid):
        name = {2936: "tournament_football.json", 18320: "tournament_tennis.json"}.get(tid,
                                                                                     "tournament_basketball.json")
        with open(os.path.join(FIX, name), encoding="utf-8") as f:
            return f.read()


def make_servers(state):
    class Api(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "authorization, trace-id, accept, accept-language")
            self.send_header("Access-Control-Allow-Methods", "GET")

        def do_OPTIONS(self):
            self.send_response(204); self._cors(); self.end_headers()

        def do_GET(self):
            key = self.headers.get("authorization")
            if key not in state.valid:
                self.send_response(401); self._cors(); self.end_headers(); return
            body = "{}"
            if "/widgets/tournaments/" in self.path:
                state.trace_ids.append(self.headers.get("trace-id"))
                tid = int(self.path.split("/widgets/tournaments/")[1].split("?")[0])
                state.served.append(tid)
                body = state.tournament(tid)
                if state.expire_after and len(state.served) == state.expire_after:
                    state.valid.clear()                       # every key expires now (once)
            self.send_response(200); self._cors()
            self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(body.encode("utf-8"))

    api = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    api_url = f"http://127.0.0.1:{api.server_address[1]}"

    class Site(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path != "/":                      # e.g. the browser asking for /favicon.ico
                self.send_response(404); self.end_headers(); return
            state.page_loads += 1
            script = ""
            if state.page_makes_key:
                key = f"Bearer key-{uuid.uuid4()}"
                state.valid.add(key)
                script = (f"<script>fetch('{api_url}/api/v2/menu?bid=1049641141', "
                          f"{{headers: {{authorization: '{key}', accept: 'application/json'}}}});</script>")
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(f"<html><body>8888{script}</body></html>".encode("utf-8"))

    site = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    for server in (api, site):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return api, site, api_url, f"http://127.0.0.1:{site.server_address[1]}"


@pytest.fixture
def fake_8888(monkeypatch):
    state = FakeOdds()
    api, site, api_url, site_url = make_servers(state)
    monkeypatch.setattr(s8, "USE_BROWSER", True)
    monkeypatch.setattr(s8, "API", api_url + "/api")
    monkeypatch.setattr(s8, "SITE", site_url)
    monkeypatch.setattr(s8, "BROWSER_EXECUTABLE", LOCAL_CHROME)
    monkeypatch.setattr(s8, "KEY_TIMEOUT", 8)
    yield state
    api.shutdown(); site.shutdown()


def test_reads_odds_with_the_sites_own_key(fake_8888):
    log = []
    rows = run.scrape_all(["2936", "18320"], (0, 0), log=log.append)
    football = s8.parse_tournament(json.load(open(os.path.join(FIX, "tournament_football.json"), encoding="utf-8")))[0]
    tennis = s8.parse_tournament(json.load(open(os.path.join(FIX, "tournament_tennis.json"), encoding="utf-8")))[0]
    assert rows == football + tennis
    assert fake_8888.served == [2936, 18320] and fake_8888.page_loads == 1
    assert all(uuid.UUID(t) for t in fake_8888.trace_ids) and len(set(fake_8888.trace_ids)) == 2
    assert any("key received" in line for line in log)


def test_expired_key_is_renewed_mid_run(fake_8888):
    fake_8888.expire_after = 1                 # the key dies after the first tournament
    log = []
    rows = run.scrape_all(["2936", "18320"], (0, 0), log=log.append)
    assert fake_8888.served == [2936, 18320]   # second tournament still read
    assert fake_8888.page_loads == 2           # page reopened once for a new key
    assert any("key expired" in line for line in log)
    assert len(rows) > 0


def test_site_without_key_is_reported_not_crashed(fake_8888):
    fake_8888.page_makes_key = False
    log = []
    rows = run.scrape_all(["2936"], (0, 0), log=log.append)
    assert rows == [] and fake_8888.served == []
    assert any("did not hand out its key" in line or "Could not start" in line for line in log)
