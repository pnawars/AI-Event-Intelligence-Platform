"""
run_pipeline.py — Entry point for the unified daily AI Event Pipeline.

Usage:
    python run_pipeline.py          # Run once immediately, then schedule daily at 07:00
    python run_pipeline.py --once   # Run once and exit (for Windows Task Scheduler)
    python run_pipeline.py --debug  # Enable DEBUG logging
"""

import argparse
import json
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import schedule
from dotenv import load_dotenv

load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "pipeline.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=7,
        ),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)

# Path where the last-run summary JSON is written (dashboard reads this)
SUMMARY_FILE = LOG_DIR / "pipeline_last_run.json"


def _save_summary(summary: dict) -> None:
    """Persist the pipeline run summary so the Flask dashboard can display it."""
    try:
        with open(SUMMARY_FILE, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, default=str)
        logger.debug("Summary written to %s", SUMMARY_FILE)
    except Exception as exc:
        logger.warning("Could not write summary file: %s", exc)


def job() -> None:
    """Run the full pipeline and persist the summary."""
    logger.info("=" * 60)
    logger.info("Daily pipeline job starting — %s", datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
    logger.info("=" * 60)

    # Import here so logging is configured before modules initialise
    from pipeline_agent import run_pipeline

    try:
        summary = run_pipeline()
        _save_summary(summary)
        logger.info(
            "Job complete | events_saved=%d | new_events=%d | companies_saved=%d | errors=%d",
            summary.get("events_saved", 0),
            len(summary.get("new_events", [])),
            summary.get("companies_saved", 0),
            len(summary.get("errors", [])),
        )
    except Exception:
        logger.exception("Pipeline job failed with unhandled exception")


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified AI Event Pipeline")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run the pipeline once and exit (use with Windows Task Scheduler)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG-level logging",
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.once:
        job()
        sys.exit(0)

    # Scheduler mode: run immediately, then every day at the configured time
    run_time = os.getenv("DAILY_RUN_TIME", "07:00")
    logger.info(
        "Scheduler mode — daily run at %s. Running immediately on startup…", run_time
    )

    job()   # Run right away so you can verify everything works

    schedule.every().day.at(run_time).do(job)
    logger.info("Scheduler active. Next scheduled run at %s.", run_time)

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
