"""
Read the 8888 odds through a real browser, the same way the 8888 page does.

The odds server only answers requests that carry a short-lived, encrypted
visitor key ("authorization: Bearer ..."), which the 8888 page gets for itself
when it opens. So the scraper:

  1. opens 8888punkt.bg in a hidden browser on this PC (Chrome, Edge or Brave,
     whichever is installed; Edge is on every Windows PC),
  2. waits for the page's own first request to the odds server and takes the
     key from it,
  3. asks the odds server for each tournament from inside that page, with that
     key (so the request looks exactly like the site's own),
  4. if the key has expired (HTTP 401), reloads the page for a new one.

Nothing is installed: Playwright drives the browser that is already on the PC.
"""

import os
import time
import uuid

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

BRAVE_PATHS = [
    os.path.expandvars(r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe"),
    os.path.expandvars(r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe"),
]
OFFSCREEN = ["--window-position=-32000,-32000", "--window-size=1280,900"]
KEEP_HEADERS = ("authorization", "accept", "accept-language")

_FETCH_JS = """
async ({url, headers}) => {
  try {
    const r = await fetch(url, {headers});
    return {status: r.status, text: await r.text()};
  } catch (e) {
    return {status: 0, text: String(e)};
  }
}
"""


class BrowserError(RuntimeError):
    """The browser could not be started, or the site never handed out its key."""


class BrowserSession:
    def __init__(self, site_url, api_marker, log=print, executable=None, key_timeout=60):
        self.site_url = site_url
        self.api_marker = api_marker          # part of the odds server's address
        self.log = log
        self.executable = executable          # tests: a specific browser binary
        self.key_timeout = key_timeout
        self._pw = self._browser = self._page = None
        self._seen = []
        self._headers = None
        self.browser_name = None

    # --- browser -------------------------------------------------------------
    def _candidates(self):
        if self.executable:
            yield f"browser at {self.executable}", {"executable_path": self.executable}
            return
        yield "Google Chrome", {"channel": "chrome"}
        yield "Microsoft Edge", {"channel": "msedge"}
        for path in BRAVE_PATHS:
            if os.path.exists(path):
                yield "Brave", {"executable_path": path}

    def _launch(self, headless):
        problems = []
        for name, options in self._candidates():
            try:
                browser = self._pw.chromium.launch(
                    headless=headless, args=[] if headless else OFFSCREEN, **options)
                self.browser_name = name
                return browser
            except PlaywrightError as e:
                problems.append(f"{name}: {str(e).splitlines()[0]}")
        raise BrowserError("Could not start Chrome, Edge or Brave on this PC. " + " | ".join(problems))

    def launch_only(self):
        """Start a browser and open a blank page (self-test: proves the browser can be driven)."""
        self._pw = sync_playwright().start()
        self._browser = self._launch(headless=True)
        self._browser.new_page().goto("about:blank")

    def start(self):
        self._pw = sync_playwright().start()
        for headless in (True, False):          # hidden first; off-screen window if the site insists
            self._browser = self._launch(headless)
            context = self._browser.new_context(locale="bg-BG", timezone_id="Europe/Sofia")
            self._page = context.new_page()
            self._page.on("request", self._on_request)
            mode = "a hidden" if headless else "an off-screen"
            self.log(f"Opening 8888punkt.bg in {mode} {self.browser_name} window to get the site's key...")
            if self._open_site():
                self.log("  key received, reading the tournaments")
                return self
            self._browser.close()
        raise BrowserError("8888punkt.bg did not hand out its key. Check that the site opens normally "
                           "in a browser on this PC (normal Bulgarian connection, no VPN).")

    def _on_request(self, request):
        if self.api_marker in request.url:
            self._seen.append(request)

    def _open_site(self):
        self._seen.clear()
        self._headers = None
        try:
            self._page.goto(self.site_url, wait_until="domcontentloaded", timeout=self.key_timeout * 1000)
        except PlaywrightError as e:
            self.log(f"  ! could not open {self.site_url}: {str(e).splitlines()[0]}")
            return False
        deadline = time.time() + self.key_timeout
        while time.time() < deadline:
            for request in list(self._seen):
                try:
                    headers = request.all_headers()
                except PlaywrightError:
                    continue
                if headers.get("authorization"):
                    self._headers = {k: v for k, v in headers.items() if k in KEEP_HEADERS}
                    return True
            self._page.wait_for_timeout(250)
        return False

    # --- odds server -----------------------------------------------------------
    def fetch(self, url):
        """GET url from inside the 8888 page with the site's key -> (status, text).

        On HTTP 401 the page is reloaded once for a fresh key and the request retried.
        """
        for attempt in range(2):
            headers = dict(self._headers or {}, **{"trace-id": str(uuid.uuid4())})
            result = self._page.evaluate(_FETCH_JS, {"url": url, "headers": headers})
            if result["status"] != 401 or attempt:
                return result["status"], result["text"]
            self.log("  (the site's key expired, getting a new one...)")
            if not self._open_site():
                return 401, ""
        return result["status"], result["text"]

    def close(self):
        for closer in (lambda: self._browser and self._browser.close(),
                       lambda: self._pw and self._pw.stop()):
            try:
                closer()
            except Exception:  # noqa: BLE001 - closing must never fail a run
                pass
