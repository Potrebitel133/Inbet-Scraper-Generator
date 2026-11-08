# Combo Ticket Bot

Generates 4 to 10-fold accumulator betting tickets for inbetsport.com from an
Excel workbook, and saves a QR code per ticket for placing at a kiosk. The ticket
type (4..10 matches) is picked per run; each type has its own stake rule.

## Flow

```
Excel sheet (1..10)  ->  resolve each pick  ->  build slip  ->  draft  ->  QR
```

1. **Read ticket** — each numbered sheet is one N-fold ticket (N = ticket type 4..10,
   rows A2:I(N+1), Product in I12). Columns:
   Match, Home/Draw/Away odds, Tournament, and **Min Odd** (col I) = the chosen
   outcome (whichever of Home/Draw/Away equals it).            `resolver.read_ticket`
2. **Resolve tournament id** — Excel tournament name -> slug(s) -> id, matched
   against `tournaments.json`.                                 `resolver.resolve_tournament_id`
3. **Fetch games & resolve outcome** — pull the tournament's games, match each
   event by English team name, pick the 1X2 outcome for the chosen side, and
   read its `outcomeId`.                                       `resolver.build_selection`
4. **create-bets** — validate the slip / combined odds.        `common.create_bets`
5. **Stake** — fixed amount, or the type's formula (e.g. `20000/(I12-1)`,
   `I12` = Product, `ODDS` = live total odds) rounded up to 5.  `stake_rules`
6. **create-draft** — post the 10-selection slip + stake.      `common.create_draft`
7. **QR** — save `N<voucher>` as `<type>-<sheet>.png` (e.g. `5-22.png`).  `bot.generate_qr`
8. Wait 60–70s, next ticket.

## Files

| File | Role |
|------|------|
| `config.py` | Constants + **platform auth tokens** (single source of truth) |
| `common.py` | HTTP session, headers, one wrapper per API endpoint |
| `resolver.py` | Excel reading, tournament/event/outcome resolution, stake |
| `bot.py` | Orchestrator: the full loop + QR + 60–70s pacing |
| `stake_rules.py` | Ticket-type stake rules: fixed or safe formula (I12, ODDS), saved in settings.json |
| `utils.py` | `cleanup_tournament_name` — tournament name -> slug(s) |
| `games.py`, `create_bets.py`, `create_draft.py`, `list_all_tournements.py`, `qr_code_generator.py` | Thin standalone wrappers over `common.py` |

## Usage

```bash
# Offline: resolve + build every ticket payload from cached games, no network.
python bot.py --dry-run              # writes ./drafts/draft_sheet_N.json

# Live (needs Bulgarian IP + fresh tokens): create drafts + save QRs.
python bot.py                        # QRs -> ./qr_codes/
python bot.py --sheets 1 2           # only some sheets
python bot.py --type 5 --formula "20000/(I12-1)"   # 5-match tickets
python bot.py --type 8 --fixed-stake 100           # 8-match tickets, fixed stake

# Tests (no network)
python -m pytest -q tests
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

## Step 1 for 8888 (scraper_8888/)

`scraper_8888/` scrapes 8888punkt.bg (SportInnovation API, brand 1049641141) for the
tournaments in a links file and writes the Formatted Matches layout (columns A..I,
Draw = 10 for basketball and tennis). One request per tournament; prematch only.
Needs a normal Bulgarian connection (the site blocks VPNs). See
`scraper_8888/SCRAPER_8888_GUIDE.md`.

```bash
python scraper_8888/scraper_8888_ui.py          # window
python scraper_8888/run_scraper_8888.py --input links.xlsx --output out.xlsx
```

## Windows builds

`ci/build.ps1` installs, runs every test, builds `InbetComboTicketBot.exe` and
`Scraper8888.exe`, and starts each once with `--selftest`. GitHub Actions
(`.github/workflows/build-exe.yml`) runs it on every push; the exes are under the
run's Artifacts. Change the build in `ci/build.ps1`, not in the workflow.
