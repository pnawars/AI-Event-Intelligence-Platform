"""
run_company.py — Entry point for the Event Intelligence Agent.

Usage:
    python run_company.py --url "https://example-event.com"
    python run_company.py --url "https://example-event.com" --debug
"""

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

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
            LOG_DIR / "company_agent.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
        ),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Event Intelligence Agent — scrapes an event page and enriches companies."
    )
    parser.add_argument(
        "--url",
        required=True,
        metavar="EVENT_URL",
        help="URL of the event page to process (e.g. https://ai-summit.com/sponsors)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    event_url = args.url.strip()

    logger.info("=" * 60)
    logger.info("Event Intelligence Agent — starting run")
    logger.info("Event URL: %s", event_url)
    logger.info("=" * 60)

    from company_agent import run_agent

    try:
        stats = run_agent(event_url)
        logger.info(
            "Run complete | iterations=%d | companies_saved=%d | stop=%s",
            stats["iterations"],
            stats["companies_saved"],
            stats["stop_reason"],
        )
        print(
            f"\nDone. {stats['companies_saved']} companies saved "
            f"({stats['iterations']} iterations, stop={stats['stop_reason']})"
        )
    except Exception:
        logger.exception("Agent run failed with unhandled exception")
        sys.exit(1)


if __name__ == "__main__":
    main()
