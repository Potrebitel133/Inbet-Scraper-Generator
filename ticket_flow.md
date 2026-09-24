# Combo Ticket Bot Flow

This document describes the end-to-end workflow of the combo ticket bot and the key steps it performs.

## 1. Input: Excel ticket sheet

- The bot reads one numbered worksheet from the Excel workbook.
- Each worksheet is expected to contain up to 10 ticket rows.
- Each row includes:
  - Match name (`Home - Away`)
  - Home odd
  - Draw odd
  - Away odd
  - Tournament name
  - Min Odd (the chosen odd)

## 2. Determine chosen side

- For each row, the bot compares `Min Odd` with the three odds.
- It selects one of:
  - `home`
  - `draw`
  - `away`
- This logic is implemented in `resolver.pick_side()`.
- If `Min Odd` matches one of the odds exactly, that side wins.
- If not, it chooses the nearest odd by numeric distance.

## 3. Resolve tournament ID

- The bot converts the Excel tournament name into one or more slugs.
- It looks up the slug in the cached tournament index (`tournaments.json`).
- The index maps tournament slug paths to sportsbook tournament IDs.
- This is handled in `resolver.resolve_tournament_id()`.

## 4. Fetch and index tournament games

- The bot loads game data for the tournament ID from either:
  - live API (`common.fetch_tournament_games()`), or
  - cached JSON when running `--dry-run`.
- It builds an event index by normalizing English home/away team names.
- It selects the correct 1X2 market and ignores enhanced-odds markets.
- This is handled in `resolver.build_event_index()`.

## 5. Match ticket rows to real events

- For each pick, the bot finds the best event whose home/away names match.
- If both teams are available, both must match; otherwise a single-team fallback is allowed.
- If no event matches, the pick is skipped.
- Matching logic lives in `resolver.find_event()`.

## 6. Build selection payloads

- For a matched pick, the bot constructs a full selection object from event data.
- The selection includes fields such as:
  - `outcomeId`
  - `odds`
  - `eventTitle`
  - `marketId`
  - `tournamentId`
- This is done by `resolver.build_selection()`.

## 7. Calculate combined odds and stake

- The bot computes the total accumulator odds by multiplying all selected odds:
  - `total_odds = product(selection.odds)`
- It then calculates stake using the configured budget:
  - `stake = ceil(50000 / total_odds)`
- This occurs in `resolver.combined_odds()` and `resolver.stake_for()`.

## 8. Draft payload creation

- The bot builds the draft request body with `build_draft_payload()`.
- The payload contains:
  - the `selections` list
  - `bets` metadata including stake and fold system
  - fixed fields like `sender`, `oddsChange`, `language`, `stakeType`
- For a 10-leg ticket, the system is `ten_folds`.

## 9. Validate with `create_bets`

- Before creating the draft, the bot validates the betslip with:
  - `common.create_bets([outcomeId, ...])`
- This endpoint returns enriched outcome metadata and confirmed odds.
- The bot uses this response to rebuild final selection objects.
- This prevents create-draft rejection due to incomplete selection data.

## 10. Create draft ticket

- With enriched selections and final stake, the bot posts to:
  - `common.create_draft(payload)`
- This creates the actual draft betslip on the sportsbook side.
- The response should contain a voucher or ticket identifier.

## 11. Extract voucher and build QR

- The bot extracts the voucher code from the draft response using `extract_voucher()`.
- It searches likely fields such as `code`, `voucher`, `betCode`, `id`, etc.
- The QR content is generated as `N{voucher}`.
- The QR image is saved to `qr_codes/ticket_sheet_{sheet}.png`.
- This is done in `generate_qr()`.

## 12. Log the ticket

- The bot appends a record to `tickets_log.json`.
- The log includes:
  - voucher code
  - sheet number
  - stake
  - combined odds
  - QR path
  - selections and matched outcomes

## 13. Rate control

- In live mode, the bot waits 60–70 seconds between tickets.
- This reduces fingerprinting and avoids sending too many requests too fast.
- The delay is controlled by `config.MIN_INTERVAL_SECONDS` and `config.MAX_INTERVAL_SECONDS`.

## 14. Dry-run mode

- When run with `--dry-run`, the bot does not POST to live endpoints.
- It uses cached games JSON and writes draft payloads to `drafts/draft_sheet_{sheet}.json`.
- This lets you verify ticket building without submitting anything.

## API endpoints used

- `GET /api/sportsapi/public/sports` — load tournament list
- `GET /api/sportsapi/public/sport-events/v2/multi-leagueview/normalized/{SPORT_ID}` — load games and outcomes
- `POST /api/betslip/public/create-bets` — validate selections and get combined odds
- `POST /api/pretail/public/v2/betslip/create-draft` — create the draft ticket

## Improvements to consider

1. Better error reporting
   - Add structured logging instead of printing only plain text.
   - Record failed pick reasons in a separate error report.

2. Stronger matching
   - Improve tournament and team name matching to reduce false misses.
   - Use fuzzy matching or additional normalization for alternate team names.

3. Voucher extraction reliability
   - Make `extract_voucher()` more deterministic by confirming the expected response shape.
   - Log the full draft response on voucher failure for debugging.

4. Config security and flexibility
   - Move secret values out of `config.py` into environment variables.
   - Allow different input/output paths via CLI options.

5. Tests and validation
   - Add unit tests for `pick_side()`, ticket parsing, event matching, and payload building.
   - Add end-to-end dry-run tests against cached fixtures.

6. Retry and rate-limit handling
   - Add retries on transient API failures or timeouts.
   - Respect HTTP 429 / API backoff behavior if present.

7. Clean separation of concerns
   - Make `bot.py` orchestrator lighter by moving more logic into helper modules.
   - Add a dedicated module for ticket logging and QR generation.
