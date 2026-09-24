"""
Logging setup — one call configures both outputs:

  * console   — human-readable, for whoever runs the bot
  * logs/bot.jsonl — one JSON object per line, for the (future) client dashboard

The JSONL file is the dashboard's data source: each line has ts / level / msg
plus any structured context, so the dashboard can filter INFO vs WARNING vs
ERROR/CRITICAL without parsing prose. Attach context with:

    log.warning("event not found", extra={"ctx": {"sheet": "1", "match": m}})

Levels we use (so the dashboard has a fixed contract):
  INFO      normal flow (ticket built, QR saved, stake computed)
  WARNING   recoverable — pick skipped, invalid sheet, partial ticket, retry
  ERROR     ticket failed / no voucher / API rejected — needs our attention
  CRITICAL  run-wide blocker (e.g. Cloudflare geo-block)
"""

import json
import logging
import logging.handlers
import os
import sys
import time

LOG_FILE = os.path.join("logs", "bot.jsonl")


class JsonlFormatter(logging.Formatter):
    """One compact JSON object per record — the dashboard reads these."""

    converter = time.gmtime  # UTC timestamps

    def format(self, record):
        data = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", self.converter(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "ctx") and isinstance(record.ctx, dict):
            data.update(record.ctx)  # sheet / voucher / match / reason ...
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def setup_logging(log_file=LOG_FILE, console_level=logging.INFO):
    """Configure root logging once. Safe to call more than once (idempotent)."""
    root = logging.getLogger()
    if root.handlers:  # already configured this process
        return logging.getLogger("bot")
    root.setLevel(logging.DEBUG)

    os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
    # Rotate so a long-running bot doesn't grow an unbounded log file.
    jsonl = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    jsonl.setLevel(logging.DEBUG)
    jsonl.setFormatter(JsonlFormatter())
    root.addHandler(jsonl)

    # Console handler only when there's a real stream — a windowed .exe has
    # sys.stderr == None, and StreamHandler(None) would crash on the first log.
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setLevel(console_level)
        console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        root.addHandler(console)
    return logging.getLogger("bot")


if __name__ == "__main__":
    # Self-check: JSONL line is valid JSON, carries level + ctx + UTC ts.
    log = setup_logging(log_file="logs/_selftest.jsonl")
    log.warning("event not found", extra={"ctx": {"sheet": "1", "match": "A - B"}})
    with open("logs/_selftest.jsonl", encoding="utf-8") as f:
        rec = json.loads(f.readlines()[-1])
    assert rec["level"] == "WARNING" and rec["sheet"] == "1" and rec["ts"].endswith("Z"), rec
    logging.shutdown()  # release the file handle (Windows) before deleting
    os.remove("logs/_selftest.jsonl")
    print("logsetup self-check ok:", rec)
