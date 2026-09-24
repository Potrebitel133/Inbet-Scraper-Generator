"""
Fetch ALL tournaments from the API, save with URLs, and check your Excel names.

IMPORTANT: the bot matches Excel tournament names by SLUG against the API's
`path` field (not the display name). This tool uses the exact same logic as
resolver.resolve_tournament_id, so what it reports is what the bot will do.

    python fetch_tournaments_with_urls.py
    python fetch_tournaments_with_urls.py "Eliteserien- Norway" "First League- Bulgaria"

Output: tournaments_full.json  (name, id, path/slug, url for every tournament)
"""

import json
import sys

import common
from utils import cleanup_tournament_name


def p(text):
    """Print safely — tournament names contain Cyrillic the console may not encode."""
    enc = sys.stdout.encoding or "utf-8"
    print(str(text).encode(enc, "replace").decode(enc))

BASE_TOURNAMENT_URL = "https://inbetsport.com/sports/soccer-1001"

# Names to check; override by passing them as command-line arguments.
DEFAULT_CHECKS = ["Superliga- Argentina", "Eliteserien- Norway", "First League- Bulgaria"]


def collect_tournaments(sports):
    """Flatten the sports tree into a list of tournament dicts."""
    out = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "tournament" and node.get("path"):
                path = node["path"]
                out.append({
                    "id": node.get("id"),
                    "name": node.get("name", "?"),
                    "path": path,                       # <- what the bot matches on
                    "url": f"{BASE_TOURNAMENT_URL}/{path}-{node.get('id')}",
                })
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(sports)
    out.sort(key=lambda t: t["path"])
    return out


def check_name(excel_name, tournaments):
    """Report whether the bot would resolve this Excel name, and suggest fixes."""
    slugs = cleanup_tournament_name(excel_name)
    by_path = {t["path"]: t for t in tournaments}

    for slug in slugs:
        if slug in by_path:
            t = by_path[slug]
            print(f"\nOK   '{excel_name}'")
            p(f"     matched slug '{slug}' -> id {t['id']}  ({t['name']})")
            return True

    print(f"\nFAIL '{excel_name}'  (tried slugs: {slugs})")
    # Suggest close paths sharing any meaningful word from the name
    words = [w for w in slugs[0].split("-") if len(w) > 3]
    seen = set()
    for w in words:
        for t in tournaments:
            if w in t["path"] and t["path"] not in seen:
                seen.add(t["path"])
                p(f"     did you mean:  path='{t['path']}'   name='{t['name']}'   id={t['id']}")
    if not seen:
        p("     no similar tournament paths found (league may not be scheduled right now)")
    return False


def main():
    print("Fetching tournaments from API...")
    tournaments = collect_tournaments(common.fetch_sports())

    out = "tournaments_full.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(tournaments, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(tournaments)} tournaments -> {out}")

    checks = sys.argv[1:] or DEFAULT_CHECKS
    print("\n" + "=" * 60)
    print("Checking Excel tournament names (same logic as the bot)")
    print("=" * 60)
    for name in checks:
        check_name(name, tournaments)


if __name__ == "__main__":
    main()
