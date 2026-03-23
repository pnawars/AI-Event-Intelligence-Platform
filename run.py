"""
run.py — Entry point for the AI Event Sourcing Agent.

Usage:
    python run.py          # Run once immediately, then schedule daily
    python run.py --once   # Run once and exit (use with Windows Task Scheduler)
"""

import argparse
import logging
import logging.handlers
import os
import sys
from pathlib import Path

import schedule
import time
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "agent.log",
            maxBytes=5 * 1024 * 1024,  # 5 MB per file
            backupCount=5,
        ),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------

def job():
    logger.info("=" * 60)
    logger.info("Starting daily event sourcing run")
    logger.info("=" * 60)

    # Import here so logging is configured first
    from agent import run_agent

    try:
        stats = run_agent()
        logger.info(
            "Run complete | iterations=%d | events_saved=%d | stop=%s",
            stats["iterations"],
            stats["events_saved"],
            stats["stop_reason"],
        )
    except Exception:
        logger.exception("Agent run failed with unhandled exception")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AI Event Sourcing Agent")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (suitable for Windows Task Scheduler)",
    )
    args = parser.parse_args()

    if args.once:
        job()
        sys.exit(0)

    # Scheduler mode: run immediately, then repeat daily
    run_time = os.getenv("DAILY_RUN_TIME", "07:00")
    logger.info("Scheduler mode — daily run at %s. Running immediately on startup.", run_time)

    job()  # Run right away so you can verify it works

    schedule.every().day.at(run_time).do(job)
    logger.info("Scheduler started. Waiting for next run at %s...", run_time)

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
