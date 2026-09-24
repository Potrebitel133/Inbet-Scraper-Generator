"""Standalone: refresh tournaments.json (the full sports/tournament tree)."""

import common
import config

if __name__ == "__main__":
    common.fetch_sports(save_to=config.TOURNAMENTS_FILE)
    print(f"saved {config.TOURNAMENTS_FILE}")
