"""
The efbet connection through a real browser, against a fake efbetshop.com page
and a fake swarm server that answer like the real ones (recorded 25 Nov 2026,
tests/fixtures/efbet): the page's conf.json names the swarm address, the
robot opens its own WebSocket from inside the page, starts a session and sends
its commands. Needs Chrome/Chromium and the websockets package (skipped otherwise).
"""

import asyncio
import contextlib
import copy
import json
import os
import shutil
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright")
pytest.importorskip("websockets")
from websockets.asyncio.server import serve  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ticket_efbet"))

import swarm_efbet as swarm  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "efbet")
LOCAL_CHROME = shutil.which("google-chrome") or shutil.which("chromium")
pytestmark = pytest.mark.skipif(not LOCAL_CHROME and sys.platform != "win32", reason="no browser")


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


class FakeSwarm:
    def __init__(self):
        self.received = []            # (command, params)
        self.sessions = []
        self.origins = []
        self.close_after = None       # drop the connection after this many commands (once)
        self.ignore = set()           # commands never answered
        self.ignore_once = set()      # commands not answered the first time (a silent connection)
        self.refuse = set()           # commands answered with an error code
        self.numbers = iter(range(3535002, 3535100))
        future = datetime.now(timezone.utc) + timedelta(days=3)
        self.games = load("games_reply.json")["data"]["data"]["game"]
        for g in self.games.values():
            g["start_ts"] = int(future.timestamp())

    def answer(self, msg):
        command, params, rid = msg.get("command"), msg.get("params") or {}, msg.get("rid")
        self.received.append((command, params))
        if command in self.ignore:
            return None
        if command in self.ignore_once:
            self.ignore_once.discard(command)
            return None
        if command in self.refuse:
            return {"code": 12, "rid": rid, "msg": "Invalid request"}
        if command == "request_session":
            self.sessions.append(params)
            reply = load("session_reply.json")
        elif command == "get":
            ids = [str(i) for i in params["where"]["game"]["id"]["@in"]]
            reply = {"code": 0, "data": {"data": {"game": {i: copy.deepcopy(self.games[i]) for i in ids
                                                           if i in self.games}}}}
        elif command == "get_sport_bonus_rules":
            reply = load("bonus_rules_reply.json")
        elif command == "create_bet_book_v2":
            reply = load("book_reply.json")
            reply["data"]["details"]["Id"] = next(self.numbers)
        elif command == "get_booking_by_id_v2":
            reply = load("booking_reply.json")
            reply["data"]["details"]["id"] = params["booking_id"]
        else:
            reply = {"code": 2, "msg": "unknown command"}
        reply["rid"] = rid
        return reply


def start_servers(state):
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    holder = {}

    async def handler(ws):
        state.origins.append(ws.request.headers.get("Origin"))
        count = 0
        async for raw in ws:
            reply = state.answer(json.loads(raw))
            count += 1
            if reply is not None:
                await ws.send(json.dumps(reply, ensure_ascii=False))
            if state.close_after and count >= state.close_after:
                state.close_after = None
                await ws.close()
                return

    async def main():
        server = await serve(handler, "127.0.0.1", 0)
        holder["port"] = server.sockets[0].getsockname()[1]
        holder["server"] = server
        ready.set()
        await server.serve_forever()

    thread = threading.Thread(target=lambda: loop.run_until_complete(main()), daemon=True)
    thread.start()
    ready.wait(10)
    ws_url = f"ws://127.0.0.1:{holder['port']}"
    state.serve_conf = True

    class Site(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/conf.json") and state.serve_conf:
                body, kind = json.dumps({"app": {"site_id": 18762167, "swarm": {"socketUrl": ws_url}}}), "application/json"
            elif self.path.startswith("/bg"):
                body, kind = "<html><body>efbet</body></html>", "text/html"
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Type", kind); self.end_headers()
            self.wfile.write(body.encode("utf-8"))

    site = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=site.serve_forever, daemon=True).start()

    def stop():
        site.shutdown()
        loop.call_soon_threadsafe(holder["server"].close)

    return f"http://127.0.0.1:{site.server_address[1]}", ws_url, stop


@pytest.fixture
def efbet(monkeypatch):
    state = FakeSwarm()
    site_url, ws_url, stop = start_servers(state)
    state.site_url, state.ws_url = site_url, ws_url
    monkeypatch.setattr(swarm, "SITE", site_url + "/bg")
    monkeypatch.setattr(swarm, "BROWSER_EXECUTABLE", LOCAL_CHROME)
    monkeypatch.setattr(swarm, "PAGE_TIMEOUT", 15)
    monkeypatch.setattr(swarm, "TIMEOUT", 5)
    yield state
    stop()


IDS = [31500243, 31500242, 31500254, 31500257, 31500253]


def test_session_and_commands_go_out_from_the_page(efbet):
    with swarm.connection(lambda m: None):
        games = swarm.get_games(IDS)
        rules = swarm.bonus_rules()
        booked = swarm.create_booking(load("book_request.json")["params"])
        back = swarm.get_booking(booked["details"]["Id"])
        cfg = swarm.partner_config()
    assert sorted(games) == sorted(map(str, IDS))
    assert games["31500243"]["market"]["2542067097"]["display_key"] == "WINNER_EP"
    assert rules[0]["BetType"] == 2 and booked["details"]["Id"] == 3535002
    assert back["details"]["id"] == 3535002
    assert cfg["prematch_max_stake"]["EUR"] == 1943.0
    (session,) = efbet.sessions
    assert session == {"language": "eng", "site_id": 18762167, "source": 42, "release_date": swarm.RELEASE_DATE}
    assert efbet.origins == [efbet.site_url]               # the WebSocket comes from the efbet page itself
    commands = [c for c, _ in efbet.received]
    assert commands == ["request_session", "get", "get_sport_bonus_rules", "create_bet_book_v2",
                        "get_booking_by_id_v2"]
    get = efbet.received[1][1]
    assert get["where"]["market"] == {"display_key": {"@in": ["WINNER", "WINNER_EP"]}}


def test_a_dropped_connection_is_reopened(efbet):
    efbet.close_after = 2                                   # session + one command, then efbet hangs up
    log = []
    with swarm.connection(log.append):
        swarm.get_games(IDS[:1])
        swarm._session.page.wait_for_timeout(300)
        games = swarm.get_games(IDS[1:2])
    assert list(games) == ["31500242"] and len(efbet.sessions) == 2
    assert any("opening it again" in line for line in log)


def test_a_silent_connection_is_reopened_for_reading(efbet):
    efbet.ignore_once.add("get")                            # efbet stops answering, without closing
    log = []
    with swarm.connection(log.append):
        games = swarm.get_games(IDS)
    assert len(games) == 5 and len(efbet.sessions) == 2
    assert [c for c, _ in efbet.received].count("get") == 2
    assert any("opening it again" in line for line in log)


def test_a_booking_without_answer_is_never_sent_twice(efbet):
    efbet.ignore.add("create_bet_book_v2")
    with swarm.connection(lambda m: None):
        with pytest.raises(swarm.SwarmError, match="no answer in time"):
            swarm.create_booking(load("book_request.json")["params"])
    assert [c for c, _ in efbet.received].count("create_bet_book_v2") == 1


def test_an_error_code_is_reported(efbet):
    efbet.refuse.add("get")
    with swarm.connection(lambda m: None):
        with pytest.raises(swarm.SwarmError, match="code 12"):
            swarm.get_games(IDS)


def test_the_built_in_address_is_used_without_conf_json(efbet, monkeypatch):
    efbet.serve_conf = False
    monkeypatch.setattr(swarm, "DEFAULT_SOCKET", efbet.ws_url)
    with swarm.connection(lambda m: None):
        assert sorted(swarm.get_games(IDS)) == sorted(map(str, IDS))


def test_efbet_unreachable_is_a_clear_error(efbet, monkeypatch):
    efbet.serve_conf = False
    monkeypatch.setattr(swarm, "DEFAULT_SOCKET", "ws://127.0.0.1:9")
    with pytest.raises(swarm.SwarmError, match="efbetshop.com could not be opened"):
        with swarm.connection(lambda m: None):
            pass


def test_full_run_through_the_browser(efbet, monkeypatch, tmp_path):
    """The robot end to end, through the real browser code, against the fake efbet."""
    import openpyxl
    from PIL import Image

    import barcode_efbet
    import bot_efbet as bot

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bot, "_wait", lambda lo, hi, stop: 0)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "5-1"
    ws.append(["Match ID", "Day", "Time", "Match", "Home Odds", "Draw Odds", "Away Odds", "Tournament", "Min Odd"])
    product = 1.0
    for gid in IDS:
        g = efbet.games[str(gid)]
        ev = {e["type_1"]: e["price"] for m in g["market"].values() if m["display_key"] == "WINNER_EP"
              for e in m["event"].values()}
        ws.append([gid, "Friday, 27.11.2026", "21:30", f"{g['team1_name'].strip()} - {g['team2_name'].strip()}",
                   ev["W1"], ev["X"], ev["W2"], "Bundesliga", ev["W1"]])
        product *= ev["W1"]
    ws["I12"] = product
    wb.save(tmp_path / "book.xlsx")
    result = bot.run(excel=str(tmp_path / "book.xlsx"), match_type=5, stake_rule={"mode": "fixed", "fixed": 2},
                     min_interval=0, max_interval=0)
    assert result["done"] == ["1"], result
    with open(bot.FILES_EFBET["TICKETS_LOG"], encoding="utf-8") as f:
        (t,) = json.load(f)
    assert t["voucher"] == "3535002" and barcode_efbet.decode(Image.open(t["qr"])) == "3535002"
    (_, booked), = [(c, p) for c, p in efbet.received if c == "create_bet_book_v2"]
    assert booked == load("book_request.json")["params"]
