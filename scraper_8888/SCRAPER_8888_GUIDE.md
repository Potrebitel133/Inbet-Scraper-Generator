# 8888 Match Scraper – User Guide

Scrapes football, basketball and tennis odds from 8888punkt.bg for the tournaments
in your links file, and writes them in your usual **Formatted Matches** layout.

## 1. Before you start

- Run it on a PC with a **normal Bulgarian internet connection, no VPN**. The 8888 site
  blocks VPNs and foreign connections, and then the scraper gets no data.
- Keep `Scraper8888.exe` in its own folder. It creates a `data` folder next to itself.
- The PC needs Google Chrome, Microsoft Edge or Brave (Edge comes with every Windows PC).
  The scraper opens 8888punkt.bg in a hidden window of that browser, takes the visitor
  key the site gives every visitor, and reads the odds through it. You don't see or
  touch that window; it closes by itself at the end.

## 2. The links file

On first start the scraper creates `data\Tournament_Links_8888.xlsx` with examples.

- Column A: any name you like (for example "Premier League").
- Column B: the tournament **link** from 8888punkt.bg (open the tournament on the site and
  copy the address), or just the tournament **number** (for example `2936`).
- One tournament per row. Add, remove or update rows whenever the site changes a link.

Early payout on 8888 applies to the top tournaments, so list only those you use.

## 3. Get the data

1. Check the **Tournament links file** and **Output file** boxes (Browse to change them).
2. Click **Get All Match Data**.
3. The Activity box shows each tournament and how many matches were added. A link that
   no longer works is named there; the other tournaments still run.
4. Click **Open Folder** to find the new Excel file. Each run writes a new file.

## 4. What is in the file

Sheet **Formatted Matches**, columns A to I:

| Column | Content |
|---|---|
| A Match ID | `#` + 8888's match number |
| B Day | e.g. `Sunday, 08.11.2026` (Bulgarian time) |
| C Time | e.g. `16:00` (Bulgarian time) |
| D Match | `Home - Away` |
| E Home Odds | |
| F Draw Odds | football: the draw odds; basketball and tennis: **10** |
| G Away Odds | |
| H Tournament | tournament name as on 8888 |
| I Tournament ID | 8888's tournament number |

Columns J and K are left empty for your checks. Only matches that have not started and
whose odds are open right now are included.

## 5. Common messages

- *refused the request / VPN or outside Bulgaria*: use a normal Bulgarian connection.
- *did not hand out its key*: open 8888punkt.bg in a normal browser on the same PC; if it
  shows no odds there either, the connection or the site is the problem.
- *Could not start Chrome, Edge or Brave*: install or update one of them.
- *not found on 8888*: the tournament link changed or the tournament ended; update the row.
- *N match(es) skipped: main odds not offered or suspended*: those matches have no open
  odds at the moment; run again later.
