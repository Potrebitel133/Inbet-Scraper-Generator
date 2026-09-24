"""Standalone: POST a betslip draft.

By default it replays a single-selection example (the original captured
request). The real bot builds its 10-fold payloads in bot.build_draft_payload
and posts them via common.create_draft.

Usage: python create_draft.py [path/to/draft_payload.json]
"""

import json
import sys

import common

# Minimal single-selection example (odds/ids will be stale — for smoke testing).
EXAMPLE_PAYLOAD = {
    "bets": [
        {
            "stake": {"value": 100},
            "selectedSystem": "singles",
            "selectionRefs": [{"selectionIndex": 0, "banker": False}],
            "isFreeBet": False,
        }
    ],
    "selections": [
        {
            "eventTitle": "КА Белграно vs. Росарио Сентрал",
            "sportEventId": 50006951439,
            "marketName": "Краен Резултат",
            "outcomeTitle": "КА Белграно",
            "outcomeId": "1522212752595096846",
            "odds": 2.47,
            "marketId": "1522212752595096843",
            "sportId": 1001,
            "marketTemplateId": 100001,
            "tournamentId": 19000000155,
            "categoryId": 10048,
            "marketTemplateOutcomeId": 100001,
            "homeTeam": "КА Белграно",
            "awayTeam": "Росарио Сентрал",
            "startTime": "2026-07-24T01:30:00Z",
            "tournamentName": "Суперлига",
            "id": "1522212752595096846",
            "isBanker": False,
            "isChecked": True,
        }
    ],
    "sender": {"id": "0", "channel": "internet"},
    "oddsChange": "higher",
    "language": "bg",
    "placeType": None,
    "stakeType": "BET_STAKE",
}

if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = EXAMPLE_PAYLOAD
    result = common.create_draft(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
