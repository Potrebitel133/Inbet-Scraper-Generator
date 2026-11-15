# 8888 Ticket Generator: quick guide

`Ticket8888.exe` makes one 8888 ticket (code + QR) per numbered sheet of your
workbook, the same way the 8888 site does when you click "Продължи". The window,
the workbook layout, the stake rules and the QR/PDF export are the same as in the
Inbet robot.

## 1. Before you start

- Put `Ticket8888.exe` in its own folder, for example `C:\Users\user\Ticket8888`.
  It keeps its tickets, QR codes and logs there (its own files, never mixed with
  the Inbet robot's).
- Use a normal Bulgarian connection (no VPN) and have Chrome, Edge or Brave
  installed. The robot opens 8888punkt.bg in a hidden browser window.

## 2. The workbook

Exactly like the Inbet workbook:

- one numbered sheet (`2`, `17`, ...) = one ticket;
- the matches in rows 2 to N+1 (N = matches per ticket), columns A to I with
  Match ID, Match, Home/Draw/Away Odds, Tournament and **Min Odd** (the side you
  pick); nothing below row N+1;
- the Product in **I12**.

If Min Odd equals two sides (for example 2.40 / 2.40), the robot picks one of
them at random and shows which one in the log and in the ticket.

## 3. Making tickets

1. **Choose File**: your workbook.
2. **Matches per ticket**: 4, 5, 6, 7, 8, 9 or 10.
3. **Stake**: tick *Fixed stake* and type the amount, or write a formula with
   `I12` (Product) or `ODDS` (live odds), rounded up to the next 5. Each ticket
   type keeps its own setting.
4. **Check only** (optional): reads every match on 8888 now and tells you which
   tickets are ready and which are not, with the reason. Nothing is created.
5. **Generate**: shows the list of tickets and stakes, then creates them, one
   every 40 to 60 seconds (you can change the seconds at the top).
6. **QR to PDF**: all QR codes of the run in one PDF for your phone.

Each ticket is named type-sheet, for example `5-17`. The QR holds the ticket
code, exactly like the QR on the 8888 page.

## 4. What the robot checks on every ticket

- Each Match ID is the match named in the sheet, it has not started, and its
  main (early payout) odds are open. The boosted-odds copy is never used.
- After 8888 creates the ticket, the robot opens it on 8888 and compares every
  match, pick and the stake. Only then is the QR saved. If anything differs, no
  QR is saved and the log says why.
- Odds that moved since your sheet are fine: the log lists them.

## 5. Never twice, Stop, Resume

A ticket with the same matches and picks is never made twice: not on a second
run, not after Stop, closing the window or a crash. A ticket cut off by a crash
is checked on 8888 and finished at the start of the next run.

## 6. Common messages

- *the match has already started*: remove it from the sheet or use a new file.
- *the main odds are suspended*: 8888 closed that market for now; try later.
- *Match ID ... is 'A - B' on 8888, but the sheet says ...*: the Match ID belongs
  to another match; copy the row again from the scraper's file.
- *8888 allows at most ... EUR on this ticket*: lower the stake for that type.
- *did not hand out its key / refused the request*: check the connection (no VPN).

Files next to the exe: `tickets_log_8888.json` (tickets made), `qr_codes_8888\`
(QR images), `logs\bot_8888.jsonl` (activity), `settings_8888.json` (your stake
settings).
