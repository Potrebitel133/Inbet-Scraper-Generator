"""
Shared HTTP layer: one requests.Session, one place that builds headers, and
thin wrappers around each inbet-retail-api endpoint the bot uses.

All the standalone scripts (list_all_tournements.py, games.py, create_bets.py,
create_draft.py) now delegate here so the headers/tokens live in exactly one
place (config.PLATFORM_TOKENS).
"""

import base64
import hashlib
import json
import logging
import time

import requests

import config

SESSION = requests.Session()
log = logging.getLogger("bot.http")


def make_platform_tokens():
    """Generate fresh x-platform-* headers (exp + hash) for one request.

    Mirrors the site's main.js: exp = now + TTL (seconds), hash = base64url of
    MD5(secret + exp). Verified byte-for-byte against live captured requests.
    """
    exp = round(time.time() + config.PLATFORM_TTL_SECONDS)
    digest = hashlib.md5(f"{config.PLATFORM_SECRET}{exp}".encode()).digest()
    token = base64.b64encode(digest).decode().replace("=", "").replace("+", "-").replace("/", "_")
    return {**config.PLATFORM_STATIC, "x-platform-exp": str(exp), "x-platform-hash": token}

# Static browser-ish headers shared by every request. The volatile auth values
# live in config.PLATFORM_TOKENS and are merged in by build_headers().
_BASE_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
    "cache-control": "no-cache",
    "dnt": "1",
    "origin": "https://inbetsport.com",
    "pragma": "no-cache",
    "priority": "u=1, i",
    "referer": "https://inbetsport.com/",
    "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
}


def build_headers(json_body=False):
    """Return a fresh headers dict merging base headers + freshly-signed tokens."""
    headers = dict(_BASE_HEADERS)
    headers.update(make_platform_tokens())  # fresh exp + hash per request
    if json_body:
        headers["content-type"] = "application/json"
    return headers


class CloudflareBlocked(RuntimeError):
    """Raised when Cloudflare returns its geo/IP block page instead of JSON."""


def _parse(response):
    """Return response JSON, or raise a helpful error on a Cloudflare block."""
    ctype = response.headers.get("content-type", "")
    if "application/json" not in ctype:
        if response.headers.get("server") == "cloudflare":
            raise CloudflareBlocked(
                f"HTTP {response.status_code} from Cloudflare (ray "
                f"{response.headers.get('cf-ray')}). A Bulgarian IP is required "
                f"(tokens are generated automatically)."
            )
        response.raise_for_status()
        raise RuntimeError(f"Unexpected non-JSON response ({ctype}): {response.text[:200]}")
    return response.json()


# Transient HTTP statuses worth retrying (rate-limit + gateway/server hiccups).
# A Cloudflare geo-block is NOT here on purpose — it won't fix itself, so _parse
# raises CloudflareBlocked and we surface it immediately instead of looping.
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3


def _request(method, url, **kwargs):
    """SESSION request + _parse, retrying transient failures with backoff.

    Headers are passed in freshly-signed per call; retries reuse them (30-min
    token TTL covers the few seconds of backoff).
    """
    kwargs.setdefault("timeout", 30)
    last = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            r = SESSION.request(method, url, **kwargs)
            if r.status_code not in _RETRY_STATUS:
                return _parse(r)  # success, or a non-retryable error (e.g. Cloudflare)
            last = RuntimeError(f"HTTP {r.status_code} from {url}")
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
        if attempt < _MAX_ATTEMPTS - 1:
            wait = 2 ** attempt  # ponytail: fixed 1s/2s backoff, no jitter
            log.warning("request failed (%s), retry %d/%d in %ds",
                        last, attempt + 1, _MAX_ATTEMPTS - 1, wait,
                        extra={"ctx": {"url": url, "attempt": attempt + 1}})
            time.sleep(wait)
    raise last


# --- Endpoints ---------------------------------------------------------------
def fetch_sports(save_to=None):
    """GET the full sports/category/tournament tree (was list_all_tournements.py)."""
    data = _request(
        "GET",
        f"{config.BASE_URL}/api/sportsapi/public/sports",
        headers=build_headers(),
    )
    if save_to:
        with open(save_to, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    return data


def fetch_tournament_games(tournament_ids, save_to=None):
    """GET games + markets + outcome IDs for one or more tournaments (was games.py)."""
    if isinstance(tournament_ids, (list, tuple)):
        tournament_ids = ",".join(str(t) for t in tournament_ids)
    else:
        tournament_ids = str(tournament_ids)
    params = {
        "tournamentIds": tournament_ids,
        "isLive": "true",
        "marketCount": "3",
        "viewType": "multi",
    }
    data = _request(
        "GET",
        f"{config.BASE_URL}/api/sportsapi/public/sport-events/v2/multi-leagueview/normalized/{config.SPORT_ID}",
        params=params,
        headers=build_headers(),
    )
    if save_to:
        with open(save_to, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    return data


def fetch_event_markets(event_id):
    """GET every market for one event (eventview) -- the per-event detail API.

    Some tournaments' multi-leagueview response carries ONLY the Enhanced
    Odds variant of the 1X2 market (never a plain Early-Payout one), so the
    EP odds for those events only exist here. Returns the raw "marketsData"
    dict of {marketId: market}, keyed by marketId (as returned), or {} if
    the response has none.
    """
    data = _request(
        "GET",
        f"{config.BASE_URL}/api/sportsapi/public/sport-events/eventview/normalized/{event_id}",
        headers=build_headers(),
    )
    return data.get("marketsData") or {}


def create_bets(outcome_ids):
    """POST selections to get the combined-odds calculation (was create_bets.py).

    Accepts a single outcome id or a list; returns the parsed response.
    """
    if not isinstance(outcome_ids, (list, tuple)):
        outcome_ids = [outcome_ids]
    payload = {
        "selections": [{"outcomeId": str(oid), "isBanker": False} for oid in outcome_ids],
        "includeSpecialBetTypes": True,
        "isTotalStake": False,
        "calculateTotalOdds": True,
    }
    return _request(
        "POST",
        f"{config.BASE_URL}/api/betslip/public/create-bets",
        headers=build_headers(json_body=True),
        json=payload,
    )


def create_draft(payload):
    """POST a fully-built betslip draft (was create_draft.py); returns the draft.

    ponytail: a retried POST after a Timeout could create a duplicate draft.
    Low harm — drafts aren't placed bets until scanned at a kiosk, and only one
    voucher gets scanned. Add an idempotency key if the API ever supports one.
    """
    return _request(
        "POST",
        f"{config.BASE_URL}/api/pretail/public/v2/betslip/create-draft",
        headers=build_headers(json_body=True),
        json=payload,
    )
