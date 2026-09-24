"""Standalone: validate a slip / get combined odds for one or more outcome IDs.

Usage: python create_bets.py [outcomeId ...]
"""

import json
import sys

import common

DEFAULT_OUTCOME_IDS = ["1522212752595096846"]

if __name__ == "__main__":
    outcome_ids = sys.argv[1:] or DEFAULT_OUTCOME_IDS
    result = common.create_bets(outcome_ids)
    print(json.dumps(result, ensure_ascii=False, indent=2))
