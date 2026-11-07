"""
Central configuration for the combo-ticket bot.

Everything that changes between runs or that may need refreshing lives here so
there is a single source of truth (the individual scripts used to each carry
their own copy of the headers).
"""

# --- API ---------------------------------------------------------------------
BASE_URL = "https://inbet-retail-api.egt-digital.com"

SPORT_ID = 1001  # Soccer / Футбол

# 1X2 ("Final Result" / Full Time Result) market and its three outcome slots.
MATCH_RESULT_TEMPLATE_ID = 100001
OUTCOME_TEMPLATE_ID = {
    "home": 100001,  # "1"
    "draw": 100002,  # "X"
    "away": 100003,  # "2"
}
# Client rule: leagues can carry BOTH an Early-Payout (EP) and an "Enhanced Odds"
# (Завишен Коефициент) Full Time Result market. Always take the regular EP one,
# never the Enhanced Odds one — it is tagged customMarketType == "ENHANCED_ODDS".
ENHANCED_ODDS_MARKET_TYPE = "ENHANCED_ODDS"
ENHANCED_ODDS_NAME_MARKERS = ("завишен", "enhanced")

# --- Ticket rules (see client requirements) ----------------------------------
STAKE_BUDGET = 20000          # stake = ceil(STAKE_BUDGET / combined_odds)
MAX_STAKE_BUDGET = 10000000   # safety limit for user-entered stake budgets
# selectedSystem name for an N-fold accumulator (site's BetSlip.Systems enum).
# Confirmed against create-bets (10 legs -> betType TEN_FOLDS -> "ten_folds").
FOLD_SYSTEM = {
    1: "singles", 2: "doubles", 3: "trebles", 4: "four_folds", 5: "five_folds",
    6: "six_folds", 7: "seven_folds", 8: "eight_folds", 9: "nine_folds",
    10: "ten_folds", 11: "eleven_folds", 12: "twelve_folds", 13: "thirteen_folds",
    14: "fourteen_folds", 15: "fifteen_folds",
}
TICKET_SHEETS = [str(i) for i in range(1, 11)]  # sheets "1".."10"

# Ticket types: how many matches each ticket sheet holds. The client picks ONE
# type per run in the dashboard. Rows are fixed: type N always uses A2:I(N+1)
# (4 -> A2:I5, 10 -> A2:I11), and the ticket's total odds ("Product") is always
# in PRODUCT_CELL, whatever the type.
MATCH_TYPES = (4, 5, 6, 7, 8, 9, 10)
DEFAULT_MATCH_TYPE = 10
LAST_TICKET_ROW = 11          # rows below a type's last row, up to here, must be empty
PRODUCT_CELL = "I12"

# Stake rule per type, editable in the dashboard and saved in SETTINGS_FILE:
#   fixed stake   -> the amount typed, used as is
#   formula       -> e.g. 20000/(I12-1); I12 = the sheet's Product cell,
#                    ODDS = live total odds; result rounded up to the next 5
SETTINGS_FILE = "settings.json"
DEFAULT_STAKE_FORMULA = "20000/(I12-1)"
DEFAULT_FIXED_STAKE = 100
MIN_INTERVAL_SECONDS = 40     # wait between tickets (anti-fingerprinting)
MAX_INTERVAL_SECONDS = 60

# --- Files -------------------------------------------------------------------
EXCEL_FILE = "PreFOrmattingOdds Goals MoreMatchesMainFOrtestOriginal - Copy.xlsm"
TOURNAMENTS_FILE = "tournaments.json"
QR_OUTPUT_DIR = "qr_codes"
TICKETS_LOG = "tickets_log.json"   # local ledger: voucher -> the bets on it (also stops duplicates)
IN_PROGRESS_FILE = "tickets_in_progress.json"  # voucher received, QR/log not yet written

# --- Platform auth tokens -----------------------------------------------------
# The site signs each request (reversed from main.js, verified against live
# captures):
#     exp  = round((now_ms + 30*60*1000) / 1000)              # now + 30 min
#     hash = base64url( MD5(PLATFORM_SECRET + str(exp)) )     # '='->'', '+'->'-', '/'->'_'
# So tokens are generated locally by common.make_platform_tokens() — no browser
# or manual paste needed. The API is still Cloudflare-geoblocked, so a Bulgarian
# IP remains required to actually reach it.
PLATFORM_SECRET = "amazingsecret"
PLATFORM_TTL_SECONDS = 30 * 60
PLATFORM_STATIC = {
    "x-platform-device": "desktop",
    "x-platform-lang": "bg",
    "x-platform-timezone": "Europe/Sofia",
    "x-platform-tz": "180",
}
