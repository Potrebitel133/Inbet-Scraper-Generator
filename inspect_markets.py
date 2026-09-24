"""
Verify the Enhanced-Odds vs Early-Payout distinction against a REAL API response.

The client rule: bet the "Full Time Result" market that has Early Payout, NEVER
"Full Time Result - Enhanced Odds". Our filter (resolver.is_enhanced_odds) keys
on customMarketType == "ENHANCED_ODDS" and the market name. This script fetches a
tournament and, for every game, lists each Full Time Result market with the exact
fields the filter uses — so you can confirm the filter picks the right one.

The World Cup tournament has games with BOTH markets (e.g. Argentina - Switzerland),
so it is the case that actually exercises the filter:

    python inspect_markets.py 19000000016     # World Cup (has Enhanced + EP)
    python inspect_markets.py                  # default: Argentina Superliga

Also saves the raw response to logs/api/games_<tid>.json for full inspection.
"""

import json
import os
import sys

import common
import config
import resolver

WORLD_CUP = "19000000016"
ARGENTINA = "19000000155"


def _as_list(v):
    return list(v.values()) if isinstance(v, dict) else (v or [])


def inspect(tid):
    data = common.fetch_tournament_games(tid)
    os.makedirs("logs/api", exist_ok=True)
    raw = f"logs/api/games_{tid}.json"
    with open(raw, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"tournament {tid}: saved raw -> {raw}\n")

    for event in _as_list(data.get("events")):
        title = event.get("eventTitle") or f'{event.get("homeTeam")} - {event.get("awayTeam")}'
        markets = _as_list(event.get("markets"))
        ftr = [m for m in markets if m.get("marketTemplateId") == config.MATCH_RESULT_TEMPLATE_ID]
        if not ftr:
            continue
        multi = "  <-- MULTIPLE FTR MARKETS" if len(ftr) > 1 else ""
        print(f"== {title} == {len(ftr)} Full Time Result market(s){multi}")
        for m in ftr:
            flag = "ENHANCED -> SKIP" if resolver.is_enhanced_odds(m) else "EarlyPayout -> USE"
            print(f"   [{flag:18}] name={m.get('marketName')!r} "
                  f"customMarketType={m.get('customMarketType')!r} "
                  f"isMainMarket={m.get('isMainMarket')} "
                  f"templateName={m.get('marketTemplateName')!r}")
        chosen = resolver.select_result_market(markets)
        print(f"   => filter would bet: {chosen.get('marketName') if chosen else None!r}\n")


if __name__ == "__main__":
    for tid in (sys.argv[1:] or [ARGENTINA]):
        inspect(tid)
