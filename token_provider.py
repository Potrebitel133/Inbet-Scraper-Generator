"""
token_provider.py — capture LIVE x-platform-* tokens from a real browser.

The hash is signed with a secret baked into inbetsport.com's JavaScript, so it
can't be generated offline. Instead we let the site's own JS mint valid tokens
and grab them off the wire. This also clears Cloudflare (real browser).

    pip install playwright
    playwright install chromium

    python token_provider.py            # opens browser, captures, prints tokens
    python token_provider.py --write     # also patches PLATFORM_TOKENS in run_all.py

Run this ON THE MACHINE WITH THE BULGARIAN IP. Tokens are short-lived — capture
right before a bot run.
"""

import argparse
import json
import re
import sys

PLATFORM_HEADER_KEYS = [
    "x-platform-device", "x-platform-exp", "x-platform-hash",
    "x-platform-lang", "x-platform-timezone", "x-platform-tz",
]

SITE_URL = "https://inbetsport.com/"
# We wait until a request to the retail API carries the tokens.
API_HOST = "inbet-retail-api.egt-digital.com"


def capture_tokens(headless=False, timeout_ms=60000):
    """Open the site, wait for an API call, and return its x-platform-* headers."""
    from playwright.sync_api import sync_playwright

    captured = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        def on_request(request):
            if API_HOST in request.url:
                hdrs = request.headers
                if "x-platform-hash" in hdrs:
                    for k in PLATFORM_HEADER_KEYS:
                        if k in hdrs:
                            captured[k] = hdrs[k]

        page.on("request", on_request)
        page.goto(SITE_URL, wait_until="domcontentloaded")

        # Give the SPA time to fire its API calls (and you to pass any challenge).
        page.wait_for_timeout(3000)
        try:
            page.wait_for_event(
                "request",
                predicate=lambda r: API_HOST in r.url and "x-platform-hash" in r.headers,
                timeout=timeout_ms,
            )
        except Exception:
            pass  # fall through with whatever we captured

        browser.close()

    if "x-platform-hash" not in captured:
        raise RuntimeError(
            "Did not capture tokens. Make sure you're on a Bulgarian IP, the page "
            "loaded past Cloudflare, and some sports data was requested."
        )
    return captured


def write_into_run_all(tokens, path="run_all.py"):
    """Replace the PLATFORM_TOKENS = {...} block in run_all.py with fresh values."""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    block = "PLATFORM_TOKENS = " + json.dumps(tokens, indent=4)
    new_src, n = re.subn(
        r"PLATFORM_TOKENS = \{.*?\}", block, src, count=1, flags=re.DOTALL
    )
    if n != 1:
        raise RuntimeError("Could not find PLATFORM_TOKENS block to replace.")
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_src)
    print(f"patched PLATFORM_TOKENS in {path}")


def main():
    ap = argparse.ArgumentParser(description="Capture live x-platform-* tokens.")
    ap.add_argument("--write", action="store_true", help="patch PLATFORM_TOKENS in run_all.py")
    ap.add_argument("--headless", action="store_true", help="run browser headless")
    args = ap.parse_args()

    try:
        tokens = capture_tokens(headless=args.headless)
    except ImportError:
        sys.exit("Playwright not installed. Run:\n  pip install playwright\n  playwright install chromium")

    print("Captured tokens:")
    print(json.dumps(tokens, indent=4))
    if args.write:
        write_into_run_all(tokens)


if __name__ == "__main__":
    main()
