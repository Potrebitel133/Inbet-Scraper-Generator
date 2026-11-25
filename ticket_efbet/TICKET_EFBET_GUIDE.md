# efbet Ticket Generator: quick guide

`TicketEfbet.exe` makes one efbet ticket (booking number + barcode) per sheet
of the ticket type you choose, the same way the efbet site does when you click
"ВЗЕМЕТЕ НОМЕР НА ЗАЛОГ". The window, stake rules, Check only, Stop/Resume and
the PDF export work like the 8888 robot.

## 1. Before you start

- Put `TicketEfbet.exe` in its own folder, for example `C:\Users\user\TicketEfbet`.
  It keeps its tickets, barcodes and logs there (never mixed with the other robots).
- Use a normal Bulgarian connection and have Chrome, Edge or Brave installed.
  The robot opens efbetshop.com in a hidden browser window.

## 2. The workbook

Your efbet workbook as you make it, for example `261126Kristi.xlsx`:

- one sheet per ticket, named **type-number**: `5-19` is ticket 19 of the
  5-match type, `8-3` ticket 3 of the 8-match type. All types can be in one file;
- in each sheet, the matches in rows 2 to N+1 (Match ID = efbet's match number,
  Match, Home/Draw/Away Odds, Tournament, **Min Odd** = the side you pick), and
  the Product in **I12**; nothing below the matches;
- other sheets (like `Matches`) are ignored.

If Min Odd equals two sides, the robot picks one of them at random and shows
which one in the log and in the ticket.

## 3. Making tickets (one type at a time)

1. **Choose File**: your workbook.
2. **Matches per ticket**: the type to make now, for example 5. The robot takes
   only the sheets of that type (5-2, 5-9, 5-13, 5-19 ...).
3. **Stake**: tick *Fixed stake* and type the amount, or write a formula with
   `I12` (Product) or `ODDS` (live odds), rounded up to the next 5. Each type
   keeps its own setting. efbet takes 0.25 to 1943 EUR per ticket.
4. **Check only** (optional): reads every match on efbet now and tells you which
   tickets are ready and which are not, with the reason. Nothing is created.
5. **Generate**: shows the list of tickets and stakes, then creates them, one
   every 40 to 60 seconds (you can change the seconds at the top).
6. **Barcode to PDF**: all barcodes of the run in one PDF for your phone, one
   ticket per page with its number and matches.
7. Choose the next type (for example 8) and repeat.

Each ticket is named like its sheet, for example `5-19`. The barcode holds the
booking number, exactly like the barcode on the efbet page.

## 4. What the robot checks on every ticket

- Each Match ID exists on efbet, is the sheet's match (same team name, or the
  same kick-off time when efbet writes the names differently), has not started,
  and its **early payout odds (Ранно Изплащане)** are open. The normal
  "Краен Резултат" odds are never used.
- After efbet books the ticket, the robot reads the booking back from efbet and
  compares every match, market, pick and the stake. Only then is the barcode
  saved. If anything differs, no barcode is saved and the log says why.
- Odds that moved since your sheet are fine: the log lists them. The cashier
  gets the odds of the moment the barcode is scanned.
- The log shows efbet's total odds, bonus (4% for 4 matches, 5% for 5, 10% for
  6, 20% for 7, 30% for 8, 40% for 9, 50% for 10) and payout for every ticket.

## 5. Never twice, Stop, Resume

A ticket with the same matches and picks is never made twice: not on a second
run, not after Stop, closing the window or a crash. A ticket cut off by a crash
is checked on efbet and finished at the start of the next run.

## 6. Common messages

- *the match has already started*: remove it from the sheet or use a new file.
- *efbet does not offer the early payout odds*: efbet has no Ранно Изплащане
  market for that match right now.
- *Match ID ... is 'A - B' on efbet, but the sheet says ...*: the Match ID belongs
  to another match; copy the row again from the scraper's file.
- *stake ... is outside what efbet takes on one ticket*: change the stake for that type.
- *efbetshop.com could not be opened*: check the connection (Bulgarian, no VPN).

Files next to the exe: `tickets_log_efbet.json` (tickets made), `barcodes_efbet\`
(barcode images), `logs\bot_efbet.jsonl` (activity), `settings_efbet.json`
(your stake settings).
