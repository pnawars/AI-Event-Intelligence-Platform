"""
pipeline_agent.py — Unified daily pipeline: event sourcing + company enrichment.

Orchestrates two phases:
  Phase 1 — Event Sourcing  : runs agent.run_agent() to discover new AI events in EMEA.
  Phase 2 — Company Enrichment: for each newly-saved event, runs company_agent.run_agent()
            to scrape sponsors/speakers and enrich company firmographics + ICP scoring.

Returns a summary dict:
  {
    "started_at":          str (ISO),
    "finished_at":         str (ISO),
    "duration_seconds":    int,
    "events_saved":        int,
    "new_events":          [{"event_url": str, "event_name": str}],
    "companies_saved":     int,
    "companies_per_event": {event_url: int},
    "errors":              [str],
  }

Usage (from run_pipeline.py or directly):
    from pipeline_agent import run_pipeline
    summary = run_pipeline()
"""

import logging
import time
from datetime import datetime

import db
import agent as event_agent
import company_agent

logger = logging.getLogger(__name__)

# Pause between company-enrichment runs to respect Tier-1 rate limits
BETWEEN_EVENTS_DELAY = 30   # seconds


def run_pipeline() -> dict:
    """
    Run the full daily pipeline and return a summary dict.
    Never raises — all errors are caught and recorded in summary["errors"].
    """
    started_at = datetime.utcnow()

    logger.info("=" * 70)
    logger.info("PIPELINE START  %s", started_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
    logger.info("=" * 70)

    summary = {
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "duration_seconds": 0,
        "events_saved": 0,
        "new_events": [],
        "companies_saved": 0,
        "companies_per_event": {},
        "errors": [],
    }

    # ── Snapshot existing event URLs before the run ────────────────────────
    existing_urls: set = set()
    try:
        existing_urls = {e["event_url"] for e in db.get_existing_events(limit=2000)}
        logger.info("[Phase 1] %d events already in DB — will diff after run.", len(existing_urls))
    except Exception as exc:
        msg = f"Could not load existing events snapshot: {exc}"
        logger.warning(msg)
        summary["errors"].append(msg)

    # ── Phase 1: Event Sourcing ────────────────────────────────────────────
    logger.info("[Phase 1] Starting event sourcing agent…")
    try:
        event_stats = event_agent.run_agent()
        summary["events_saved"] = event_stats.get("events_saved", 0)
        logger.info(
            "[Phase 1] Complete — %d new events saved | stop=%s | iterations=%d",
            event_stats.get("events_saved", 0),
            event_stats.get("stop_reason"),
            event_stats.get("iterations", 0),
        )
    except Exception as exc:
        msg = f"Event sourcing agent failed: {exc}"
        logger.exception(msg)
        summary["errors"].append(msg)

    # ── Compute which events are new ───────────────────────────────────────
    new_events: list = []
    try:
        all_now = db.get_existing_events(limit=2000)
        new_events = [
            {"event_url": e["event_url"], "event_name": e.get("event_name") or e["event_url"]}
            for e in all_now
            if e["event_url"] not in existing_urls
        ]
        summary["new_events"] = new_events
        logger.info("[Phase 1→2] %d new event(s) to enrich with company data.", len(new_events))
    except Exception as exc:
        msg = f"Could not compute new events diff: {exc}"
        logger.warning(msg)
        summary["errors"].append(msg)

    # ── Phase 2: Company Enrichment ────────────────────────────────────────
    if not new_events:
        logger.info("[Phase 2] No new events — skipping company enrichment.")
    else:
        logger.info("[Phase 2] Starting company enrichment for %d event(s)…", len(new_events))

        for idx, event in enumerate(new_events, 1):
            event_url  = event["event_url"]
            event_name = event["event_name"]

            logger.info(
                "[Phase 2] (%d/%d) Enriching: %s",
                idx, len(new_events), event_name,
            )

            try:
                co_stats = company_agent.run_agent(event_url)
                n = co_stats.get("companies_saved", 0)
                summary["companies_saved"] += n
                summary["companies_per_event"][event_url] = n
                logger.info(
                    "[Phase 2] (%d/%d) Done — %d companies saved for: %s",
                    idx, len(new_events), n, event_name,
                )
            except Exception as exc:
                msg = f"Company enrichment failed for {event_url}: {exc}"
                logger.exception(msg)
                summary["errors"].append(msg)
                summary["companies_per_event"][event_url] = 0

            # Pause between events — avoids hammering the API back-to-back
            if idx < len(new_events):
                logger.debug("Pausing %ds before next event…", BETWEEN_EVENTS_DELAY)
                time.sleep(BETWEEN_EVENTS_DELAY)

    # ── Final summary ──────────────────────────────────────────────────────
    finished_at = datetime.utcnow()
    duration_s  = round((finished_at - started_at).total_seconds())
    summary["finished_at"]      = finished_at.isoformat()
    summary["duration_seconds"] = duration_s

    logger.info("=" * 70)
    logger.info(
        "PIPELINE COMPLETE | events_saved=%d | new_events=%d | companies_saved=%d | errors=%d | duration=%ds",
        summary["events_saved"],
        len(new_events),
        summary["companies_saved"],
        len(summary["errors"]),
        duration_s,
    )
    if summary["errors"]:
        logger.warning("Errors during this pipeline run:")
        for err in summary["errors"]:
            logger.warning("  • %s", err)
    logger.info("=" * 70)

    return summary
