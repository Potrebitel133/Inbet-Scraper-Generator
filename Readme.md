# Combo Ticket Bot

Generates 10-fold accumulator betting tickets for inbetsport.com from an Excel
workbook, and saves a QR code per ticket for placing at a kiosk.

## Flow

```
Excel sheet (1..10)  ->  resolve each pick  ->  build slip  ->  draft  ->  QR
```

1. **Read ticket** — each numbered sheet is one 10-fold ticket. Columns:
   Match, Home/Draw/Away odds, Tournament, and **Min Odd** (col I) = the chosen
   outcome (whichever of Home/Draw/Away equals it).            `resolver.read_ticket`
2. **Resolve tournament id** — Excel tournament name -> slug(s) -> id, matched
   against `tournaments.json`.                                 `resolver.resolve_tournament_id`
3. **Fetch games & resolve outcome** — pull the tournament's games, match each
   event by English team name, pick the 1X2 outcome for the chosen side, and
   read its `outcomeId`.                                       `resolver.build_selection`
4. **create-bets** — validate the slip / combined odds.        `common.create_bets`
5. **Stake** — `ceil(50000 / combined_odds)`.                  `resolver.stake_for`
6. **create-draft** — post the 10-selection slip + stake.      `common.create_draft`
7. **QR** — save `N<voucher>` as a PNG.                        `bot.generate_qr`
8. Wait 60–70s, next ticket.

## Files

| File | Role |
|------|------|
| `config.py` | Constants + **platform auth tokens** (single source of truth) |
| `common.py` | HTTP session, headers, one wrapper per API endpoint |
| `resolver.py` | Excel reading, tournament/event/outcome resolution, stake |
| `bot.py` | Orchestrator: the full loop + QR + 60–70s pacing |
| `utils.py` | `cleanup_tournament_name` — tournament name -> slug(s) |
| `games.py`, `create_bets.py`, `create_draft.py`, `list_all_tournements.py`, `qr_code_generator.py` | Thin standalone wrappers over `common.py` |

## Usage

```bash
# Offline: resolve + build every ticket payload from cached games, no network.
python bot.py --dry-run              # writes ./drafts/draft_sheet_N.json

# Live (needs Bulgarian IP + fresh tokens): create drafts + save QRs.
python bot.py                        # QRs -> ./qr_codes/
python bot.py --sheets 1 2           # only some sheets
```

## Known blockers (must resolve before live runs)

- **Bulgarian IP required.** The API is behind Cloudflare and geo-blocks other
  countries (HTTP 403 "Attention Required"). A non-BG IP fails before auth.
- **Platform tokens are short-lived.** `x-platform-hash` / `x-platform-exp` are
  signatures minted by the site's JavaScript (EGT "X-Nave") and expire within
  minutes. Refresh `config.PLATFORM_TOKENS` from a live browser session, or
  drive the site with a headless browser (Playwright) so its JS mints them.
- **Two payload details need one real capture to confirm:**
  `config.COMBO_SYSTEM` (accumulator system name) and the voucher field name in
  the create-draft response (`bot.extract_voucher`).
