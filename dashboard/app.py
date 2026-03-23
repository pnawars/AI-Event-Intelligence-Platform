"""
dashboard/app.py — Flask dashboard for the AI Event Pipeline.

Run from the project root:
    python dashboard/app.py

Then open http://localhost:5001
"""

import json
import sys
import os
import threading
import uuid
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from dotenv import load_dotenv

import db_companies

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))

SUMMARY_FILE = Path(_ROOT) / "logs" / "pipeline_last_run.json"

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------------
# In-memory job trackers
# ---------------------------------------------------------------------------

_jobs: dict = {}        # job_id -> {status, event_url, started_at, companies_saved, error}
_jobs_lock = threading.Lock()

_pipeline_job: dict = {}   # single-slot tracker for the full pipeline
_pipeline_lock = threading.Lock()


def _run_agent_thread(job_id: str, event_url: str):
    """Background thread: runs company_agent.run_agent and updates _jobs."""
    try:
        from company_agent import run_agent
        stats = run_agent(event_url)
        with _jobs_lock:
            _jobs[job_id].update({
                "status": "done",
                "companies_saved": stats.get("companies_saved", 0),
                "iterations": stats.get("iterations", 0),
                "stop_reason": stats.get("stop_reason", ""),
                "finished_at": datetime.utcnow().isoformat(),
            })
    except Exception as exc:
        logging.getLogger(__name__).exception("Agent thread failed for %s", event_url)
        with _jobs_lock:
            _jobs[job_id].update({
                "status": "error",
                "error": str(exc),
                "finished_at": datetime.utcnow().isoformat(),
            })


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/run-agent", methods=["POST"])
def start_agent():
    """Start a company-scraping agent job for a given event URL."""
    body = request.get_json(silent=True) or {}
    event_url = (body.get("event_url") or "").strip()
    if not event_url:
        return jsonify({"error": "event_url is required"}), 400

    job_id = str(uuid.uuid4())[:8]
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "event_url": event_url,
            "started_at": datetime.utcnow().isoformat(),
            "companies_saved": 0,
        }

    t = threading.Thread(target=_run_agent_thread, args=(job_id, event_url), daemon=True)
    t.start()
    return jsonify({"job_id": job_id, "status": "running"})


@app.route("/api/job-status/<job_id>")
def job_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/api/jobs")
def list_jobs():
    with _jobs_lock:
        jobs = sorted(_jobs.values(), key=lambda j: j.get("started_at", ""), reverse=True)
    return jsonify(jobs)


@app.route("/api/filters")
def get_filters():
    try:
        return jsonify(db_companies.get_filter_options())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/companies")
def get_companies():
    try:
        min_score = request.args.get("min_icp_score")
        max_score = request.args.get("max_icp_score")
        rows = db_companies.get_all_companies(
            event_url=request.args.get("event_url") or None,
            company_type=request.args.get("company_type") or None,
            headcount_range=request.args.get("headcount_range") or None,
            revenue_range=request.args.get("revenue_range") or None,
            hq_country=request.args.get("hq_country") or None,
            industry=request.args.get("industry") or None,
            min_icp_score=int(min_score) if min_score else None,
            max_icp_score=int(max_score) if max_score else None,
        )
        return jsonify(rows)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Direct scraper route (no Claude API needed — uses Playwright)
# ---------------------------------------------------------------------------

_scrape_jobs: dict = {}
_scrape_lock = threading.Lock()


def _run_direct_scrape(job_id: str, event_url: str, event_name: str) -> None:
    try:
        sys.path.insert(0, _ROOT)
        from scraper_direct import scrape_event
        result = scrape_event(event_url, event_name or None)
        found = result.get("found", 0)
        with _scrape_lock:
            _scrape_jobs[job_id].update({
                "status":    "enriching" if found > 0 else "done",
                "saved":     result.get("saved", 0),
                "found":     found,
                "method":    result.get("method", ""),
                "warning":   result.get("warning", ""),
                "companies": result.get("companies", []),
                "errors":    result.get("errors", []),
                "scraped_at": datetime.utcnow().isoformat(),
            })

        # Auto-enrich if we found companies
        if found > 0:
            try:
                from company_agent import run_enrichment
                enrich_stats = run_enrichment(event_url)
                with _scrape_lock:
                    _scrape_jobs[job_id].update({
                        "status":            "done",
                        "companies_enriched": enrich_stats.get("companies_enriched", 0),
                        "finished_at":        datetime.utcnow().isoformat(),
                    })
            except Exception as enrich_exc:
                logging.getLogger(__name__).exception("Enrichment failed for %s", event_url)
                with _scrape_lock:
                    _scrape_jobs[job_id].update({
                        "status":       "done",
                        "enrich_error": str(enrich_exc),
                        "finished_at":  datetime.utcnow().isoformat(),
                    })
        else:
            with _scrape_lock:
                _scrape_jobs[job_id]["finished_at"] = datetime.utcnow().isoformat()

    except Exception as exc:
        logging.getLogger(__name__).exception("Direct scrape failed for %s", event_url)
        with _scrape_lock:
            _scrape_jobs[job_id].update({
                "status": "error",
                "error":  str(exc),
                "finished_at": datetime.utcnow().isoformat(),
            })


@app.route("/api/scrape-direct", methods=["POST"])
def scrape_direct():
    """Scrape an event URL using Playwright (no Claude API)."""
    body = request.get_json(silent=True) or {}
    event_url  = (body.get("event_url")  or "").strip()
    event_name = (body.get("event_name") or "").strip()
    if not event_url:
        return jsonify({"error": "event_url is required"}), 400

    job_id = str(uuid.uuid4())[:8]
    with _scrape_lock:
        _scrape_jobs[job_id] = {
            "job_id":     job_id,
            "status":     "running",
            "event_url":  event_url,
            "event_name": event_name,
            "started_at": datetime.utcnow().isoformat(),
            "saved": 0, "found": 0,
        }

    t = threading.Thread(target=_run_direct_scrape, args=(job_id, event_url, event_name), daemon=True)
    t.start()
    return jsonify({"job_id": job_id, "status": "running"})


@app.route("/api/scrape-direct-status/<job_id>")
def scrape_direct_status(job_id: str):
    with _scrape_lock:
        job = _scrape_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


# ---------------------------------------------------------------------------
# Pipeline routes
# ---------------------------------------------------------------------------

def _run_pipeline_thread(job_id: str) -> None:
    """Background thread: runs the full pipeline and updates _pipeline_job."""
    try:
        sys.path.insert(0, _ROOT)
        from pipeline_agent import run_pipeline
        summary = run_pipeline()
        # Persist summary to disk so it survives restarts
        try:
            SUMMARY_FILE.parent.mkdir(exist_ok=True)
            with open(SUMMARY_FILE, "w", encoding="utf-8") as fh:
                json.dump(summary, fh, indent=2, default=str)
        except Exception:
            pass
        with _pipeline_lock:
            _pipeline_job.update({
                "job_id": job_id,
                "status": "done",
                "finished_at": datetime.utcnow().isoformat(),
                "summary": summary,
            })
    except Exception as exc:
        logging.getLogger(__name__).exception("Pipeline thread failed")
        with _pipeline_lock:
            _pipeline_job.update({
                "job_id": job_id,
                "status": "error",
                "error": str(exc),
                "finished_at": datetime.utcnow().isoformat(),
            })


@app.route("/api/run-pipeline", methods=["POST"])
def start_pipeline():
    """Trigger the full pipeline (event sourcing + company enrichment) in background."""
    with _pipeline_lock:
        if _pipeline_job.get("status") == "running":
            return jsonify({"error": "Pipeline already running", "job": _pipeline_job}), 409
        job_id = str(uuid.uuid4())[:8]
        _pipeline_job.clear()
        _pipeline_job.update({
            "job_id": job_id,
            "status": "running",
            "started_at": datetime.utcnow().isoformat(),
        })

    t = threading.Thread(target=_run_pipeline_thread, args=(job_id,), daemon=True)
    t.start()
    return jsonify({"job_id": job_id, "status": "running"})


@app.route("/api/pipeline-status")
def pipeline_status():
    """Return the current or last pipeline run status."""
    with _pipeline_lock:
        live = dict(_pipeline_job)

    # If a run is in progress, return live state
    if live.get("status") == "running":
        return jsonify(live)

    # Otherwise return the last persisted summary from disk
    try:
        if SUMMARY_FILE.exists():
            with open(SUMMARY_FILE, "r", encoding="utf-8") as fh:
                summary = json.load(fh)
            return jsonify({"status": "done", "summary": summary})
    except Exception:
        pass

    if live:
        return jsonify(live)

    return jsonify({"status": "never_run"})


if __name__ == "__main__":
    # Set up logging for the dashboard process
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(
                os.path.join(log_dir, "dashboard.log"), maxBytes=5*1024*1024, backupCount=3
            ),
            logging.StreamHandler(sys.stdout),
        ],
    )
    app.run(debug=False, port=5001)
