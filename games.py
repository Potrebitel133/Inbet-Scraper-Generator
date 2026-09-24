"""Standalone: fetch games + markets + outcome IDs for a tournament.

Usage: python games.py [tournamentId ...]   (default: argentina-superliga)
"""

import sys

import common

DEFAULT_TOURNAMENT_IDS = ["19000000155"]  # argentina-superliga

if __name__ == "__main__":
    tournament_ids = sys.argv[1:] or DEFAULT_TOURNAMENT_IDS
    common.fetch_tournament_games(tournament_ids, save_to="tournaments_games_with_bet.json")
    print("saved tournaments_games_with_bet.json for", tournament_ids)
