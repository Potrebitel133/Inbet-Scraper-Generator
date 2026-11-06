# Combo Ticket Bot – User Guide

## 1. Start the application

1. Keep `ComboTicketBot.exe` in its own folder.
2. Keep the Excel workbook in the same folder, unless you plan to select it manually.
3. Double-click `ComboTicketBot.exe`.
4. The dashboard opens and displays the selected workbook.

If Windows shows a security warning, choose **More info** and then **Run anyway** only if the file came from your trusted source.

## 2. Select the Excel workbook

Click **Choose File** and select the workbook to use.

The bot checks every existing sheet whose name is numeric, such as `1`, `2`, `4`, or `15`. Missing sheet numbers are ignored. An existing empty sheet is still checked and will produce validation errors.

## 3. Choose the ticket type

Above the buttons, tick how many matches each ticket has: **5, 6, 7, 8, 9 or 10**.
Use one type per run. To switch type, let the run finish, tick the new type and
click **Generate** again.

Keep one workbook per type. A run stops before anything is placed if any sheet in
the workbook does not match the ticked type.

## 4. Excel sheet format

Matches always start in row 2, and the rows are fixed per type:

| Type | Match rows |
|---|---|
| 5 matches | A2 to I6 |
| 6 matches | A2 to I7 |
| 7 matches | A2 to I8 |
| 8 matches | A2 to I9 |
| 9 matches | A2 to I10 |
| 10 matches | A2 to I11 |

The ticket's total odds (**Product**) is always in cell **I12**, for every type.

If a sheet has more or fewer matches than the ticked type, it is flagged before the
run starts, for example: `there are matches below row 6, so this sheet has more than
5 matches. Is the right ticket type selected?`

The expected columns are:

| Column | Required value |
|---|---|
| Match ID | `#` followed by digits, for example `#3887` |
| Day | Match date |
| Time | Match time |
| Match | `Home Team - Away Team` |
| Home Odds | Numeric value |
| Draw Odds | Numeric value |
| Away Odds | Numeric value |
| Tournament | Tournament name |
| Min Odd | Numeric value equal to Home, Draw, or Away Odds |

Example Match value:

```text
Arsenal - Chelsea
```

## 5. Validation before running

Validation runs across all available numeric sheets before any API call is made.

If there are errors, one popup groups them by sheet. Correct every listed issue and click **Generate** again.

Typical messages include:

- `Sheet must contain exactly 8 matches; found 7.`
- `Match ID is empty; enter it like #3887`
- `Match Name is empty.`
- `Match must contain both teams, separated by ' - '`
- `Home Odds is required and must be numeric`
- `Min Odd must equal Home, Away, or Draw`

## 6. Stake: fixed or formula

Each ticket type has its own stake setting. It is saved automatically and comes back
the next time you open the program.

- **Fixed stake ticked:** every ticket uses the amount in the box, exactly as typed
  (for example `100`).
- **Fixed stake not ticked:** the stake comes from the **Formula** box, for example:

```text
20000/(I12-1)
```

`I12` is the sheet's Product cell. Change `20000` (or the whole formula) as you like.
You can also use `ODDS`, the live total odds the site confirms just before the ticket
is made, for example `20000/ODDS`. The formula result is rounded up to the next 5.

Example with `20000/(I12-1)`:

```text
I12 = 74.96   ->  20000 / 73.96 = 270.4  ->  stake 275
```

A green tick next to the formula means it is valid. A red `not valid` means it has a
typo; only numbers, `I12`, `ODDS`, `+ - * /` and brackets are allowed.

## 7. Generate tickets

1. Tick the ticket type and set the stake.
2. Click **Generate**.
3. The confirm window lists every ticket with its stake (for example
   `Ticket 5-22: stake 275`). Check it, then confirm the live run.
4. The ticket list clears for the new run.
5. Newly created tickets appear one by one as they are generated.
6. A failed sheet does not create a partial ticket.

Historical tickets remain available through the **Run** dropdown after the run finishes.

## 8. Dashboard information

- **Tickets**: tickets created in the selected run
- **Average Odds**: average combined odds of displayed tickets
- **Success**: successfully created tickets
- **Failed**: sheets that did not create a ticket
- **Activity**: live processing and error messages

Select a ticket to view its voucher, actual stake, combined odds, QR code, and matches.

## 9. QR codes and records

Created tickets and QR codes are saved in the application folder. Each QR file is
named **type-sheet**: sheet 22 of a 5-match run is `5-22.png`, sheet 9 of a 10-match
run is `10-9.png`. The same name is shown in the ticket list and on the PDF pages. Do not delete these folders while the dashboard is running.

Use **QR to PDF** to export the displayed ticket QR codes to a PDF.

## 10. Common issues

### The old tickets are showing

Click **Refresh**, select the current run from the **Run** dropdown, or restart the dashboard. During a new run, the current ticket list should start empty and append new tickets as they are created.

### The executable does not contain the latest changes

Close all running copies of the bot, including the process in Task Manager. The executable cannot be replaced while it is running.

### Stake not valid

Fixed stake: enter a number above 0. Formula: fix the typo shown in red, for example
a missing bracket.

### Event not found

Check that the Match ID, Match Name, tournament, and team spelling correspond to the available event data. The bot first uses Match ID and can fall back to Home/Away team matching.

### API or network failure

Check the internet connection and the API environment. Review the **Activity** panel and the log files for details.

## 11. Safe operating checklist

Before clicking **Generate**:

- Confirm the correct workbook is selected.
- Confirm the ticked ticket type matches the workbook.
- Confirm the stake setting (fixed or formula) for that type.
- Check the stakes listed in the confirm window.
- Confirm the live-run prompt before proceeding.
- Review the Activity panel and final run summary.
