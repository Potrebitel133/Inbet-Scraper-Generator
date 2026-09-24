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

## 3. Excel sheet format

Each ticket sheet must contain exactly 10 matches in rows 2–11. The expected columns are:

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

## 4. Validation before running

Validation runs across all available numeric sheets before any API call is made.

If there are errors, one popup groups them by sheet. Correct every listed issue and click **Generate** again.

Typical messages include:

- `Sheet must contain exactly 10 matches; found 9.`
- `Match ID is empty; enter it like #3887`
- `Match Name is empty.`
- `Match must contain both teams, separated by ' - '`
- `Home Odds is required and must be numeric`
- `Min Odd must equal Home, Away, or Draw`

## 5. Target payout and actual stake

The dashboard field is a **target payout**, currently labelled **Stake budget**.

The bot calculates the actual stake using:

```text
Actual stake = Target payout / Combined odds
```

For example:

```text
Target payout: 50,000
Combined odds: 250
Actual stake: 200
```

The allowed target payout is from `1` to `10,000,000`. The default value is `50,000`.

## 6. Generate tickets

1. Enter the target payout.
2. Click **Generate**.
3. Confirm the live run when prompted.
4. The ticket list clears for the new run.
5. Newly created tickets appear one by one as they are generated.
6. A failed sheet does not create a partial ticket.

Historical tickets remain available through the **Run** dropdown after the run finishes.

## 7. Dashboard information

- **Tickets**: tickets created in the selected run
- **Average Odds**: average combined odds of displayed tickets
- **Success**: successfully created tickets
- **Failed**: sheets that did not create a ticket
- **Activity**: live processing and error messages

Select a ticket to view its voucher, actual stake, combined odds, QR code, and matches.

## 8. QR codes and records

Created tickets and QR codes are saved in the application folder. Do not delete these folders while the dashboard is running.

Use **QR to PDF** to export the displayed ticket QR codes to a PDF.

## 9. Common issues

### The old tickets are showing

Click **Refresh**, select the current run from the **Run** dropdown, or restart the dashboard. During a new run, the current ticket list should start empty and append new tickets as they are created.

### The executable does not contain the latest changes

Close all running copies of the bot, including the process in Task Manager. The executable cannot be replaced while it is running.

### Invalid stake budget

Enter a number between `1` and `10,000,000`.

### Event not found

Check that the Match ID, Match Name, tournament, and team spelling correspond to the available event data. The bot first uses Match ID and can fall back to Home/Away team matching.

### API or network failure

Check the internet connection and the API environment. Review the **Activity** panel and the log files for details.

## 10. Safe operating checklist

Before clicking **Generate**:

- Confirm the correct workbook is selected.
- Confirm each available numeric sheet has exactly 10 matches.
- Confirm the target payout is correct.
- Confirm the live-run prompt before proceeding.
- Review the Activity panel and final run summary.
