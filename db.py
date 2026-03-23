"""
db.py — TiDB Cloud CRUD operations for the AI Event Sourcing Agent.

Fresh connections are created per function call to avoid TiDB Cloud's
8-hour idle timeout dropping a cached connection mid-run.
"""

import ssl
import json
import logging
import os
from datetime import datetime, date

import pymysql
import pymysql.cursors
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _get_connection() -> pymysql.connections.Connection:
    """Open a fresh TLS-secured connection to TiDB Cloud."""
    ssl_ctx = ssl.create_default_context()
    # TiDB Cloud uses Let's Encrypt / DigiCert chain — trusted by Python's
    # bundled CA store on Windows. If behind a corporate TLS-intercepting
    # proxy, call ssl_ctx.load_verify_locations(cafile="path/to/corp-ca.pem")
    # and remove the two lines below.
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    return pymysql.connect(
        host=os.getenv("TIDB_HOST", "gateway01.eu-central-1.prod.aws.tidbcloud.com"),
        port=int(os.getenv("TIDB_PORT", 4000)),
        user=os.getenv("TIDB_USER"),
        password=os.getenv("TIDB_PASSWORD"),
        database=os.getenv("TIDB_DATABASE", "ai_events"),
        ssl=ssl_ctx,
        autocommit=True,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def check_event_exists(event_url: str) -> dict:
    """
    Check if an event URL is already in the database.
    Returns {"exists": bool, "id": int|None}
    """
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM events WHERE event_url = %s LIMIT 1",
                (event_url,),
            )
            row = cur.fetchone()
            return {"exists": row is not None, "id": row["id"] if row else None}
    finally:
        conn.close()


def save_event(data: dict) -> dict:
    """
    Insert a new event. Uses INSERT IGNORE so concurrent duplicate writes
    are silently discarded rather than raising an exception.
    Returns {"success": bool, "id": int|None, "message": str}
    """
    conn = _get_connection()
    try:
        # Normalise submission_deadline to DATE or None
        raw_deadline = data.get("submission_deadline")
        deadline = None
        if raw_deadline:
            for fmt in ("%Y-%m-%d", "%d %b %Y", "%d/%m/%Y", "%m/%d/%Y"):
                try:
                    deadline = datetime.strptime(raw_deadline, fmt).date().isoformat()
                    break
                except ValueError:
                    continue

        # Normalise sponsorship_available to bool or None
        raw_sponsor = data.get("sponsorship_available")
        if isinstance(raw_sponsor, bool):
            sponsorship = raw_sponsor
        elif isinstance(raw_sponsor, str):
            sponsorship = raw_sponsor.lower() in ("yes", "true", "1")
        else:
            sponsorship = None

        with conn.cursor() as cur:
            sql = """
                INSERT IGNORE INTO events (
                    event_name, event_type, dates, location, event_url,
                    description, estimated_attendance,
                    audience_job_functions, audience_seniority, audience_industries,
                    icp_fit_score, icp_fit_notes,
                    sponsorship_available, submission_deadline,
                    source_url, date_added, status
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s
                )
            """
            cur.execute(sql, (
                data.get("event_name"),
                data.get("event_type", "conference"),
                data.get("dates"),
                data.get("location"),
                data.get("event_url"),
                data.get("description"),
                data.get("estimated_attendance"),
                data.get("audience_job_functions"),
                data.get("audience_seniority"),
                data.get("audience_industries"),
                int(data.get("icp_fit_score", 5)),
                data.get("icp_fit_notes"),
                sponsorship,
                deadline,
                data.get("source_url"),
                date.today().isoformat(),
                data.get("status", "active"),
            ))

            if cur.rowcount == 0:
                return {"success": False, "id": None, "message": "duplicate — skipped"}

            new_id = cur.lastrowid
            logger.info(
                "Saved event id=%s name=%r score=%s",
                new_id, data.get("event_name"), data.get("icp_fit_score"),
            )
            return {"success": True, "id": new_id, "message": "saved"}

    except Exception as exc:
        logger.error("save_event failed for %r: %s", data.get("event_url"), exc)
        return {"success": False, "id": None, "message": str(exc)}
    finally:
        conn.close()


def get_existing_events(limit: int = 300) -> list:
    """
    Return lightweight rows for events already stored.
    Claude uses this at the start of each run to skip re-researching known events.
    """
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, event_name, event_url, icp_fit_score, date_added
                FROM events
                ORDER BY date_added DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
            # Convert date objects to strings so json.dumps works downstream
            for row in rows:
                if isinstance(row.get("date_added"), (datetime, date)):
                    row["date_added"] = row["date_added"].isoformat()
            return rows
    finally:
        conn.close()
