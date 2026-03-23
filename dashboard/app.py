"""
dashboard/app.py — Flask dashboard for the Event Intelligence Agent.

Run from the project root:
    python dashboard/app.py

Then open http://localhost:5001
"""

import sys
import os
import threading
import uuid
import logging
import logging.handlers
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from dotenv import load_dotenv

import db_companies

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------------
# In-memory job tracker
# ---------------------------------------------------------------------------

_jobs: dict = {}   # job_id -> {status, event_url, started_at, companies_saved, error}
_jobs_lock = threading.Lock()


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


@app.route("/api/companies")
def get_companies():
    try:
        min_score = request.args.get("min_icp_score")
        rows = db_companies.get_all_companies(
            event_url=request.args.get("event_url") or None,
            company_type=request.args.get("company_type") or None,
            headcount_range=request.args.get("headcount_range") or None,
            hq_country=request.args.get("hq_country") or None,
            industry=request.args.get("industry") or None,
            min_icp_score=int(min_score) if min_score else None,
        )
        return jsonify(rows)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/filters")
def get_filters():
    try:
        return jsonify(db_companies.get_filter_options())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


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
