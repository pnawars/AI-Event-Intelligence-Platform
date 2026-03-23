"""
db_companies.py — TiDB Cloud CRUD operations for the Event Intelligence Agent.

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


def save_company(data: dict) -> dict:
    """
    Insert a company discovered at an event.
    Uses INSERT IGNORE so duplicate (event_url, company_name) pairs are silently skipped.
    Returns {"success": bool, "id": int|None, "message": str}
    """
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            sql = """
                INSERT IGNORE INTO event_companies (
                    event_url, event_name, company_name, company_type,
                    website_url, headcount_range, revenue_range,
                    hq_country, industry, icp_score, icp_notes, date_added
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
            """
            cur.execute(sql, (
                data.get("event_url"),
                data.get("event_name"),
                data.get("company_name"),
                data.get("company_type", "sponsor"),
                data.get("website_url"),
                data.get("headcount_range"),
                data.get("revenue_range"),
                data.get("hq_country"),
                data.get("industry"),
                int(data.get("icp_score", 5)),
                data.get("icp_notes"),
                date.today().isoformat(),
            ))

            if cur.rowcount == 0:
                return {"success": False, "id": None, "message": "duplicate — skipped"}

            new_id = cur.lastrowid
            logger.info(
                "Saved company id=%s name=%r score=%s",
                new_id, data.get("company_name"), data.get("icp_score"),
            )
            return {"success": True, "id": new_id, "message": "saved"}

    except Exception as exc:
        logger.error("save_company failed for %r: %s", data.get("company_name"), exc)
        return {"success": False, "id": None, "message": str(exc)}
    finally:
        conn.close()


def get_existing_companies(event_url: str) -> list:
    """
    Return companies already stored for a given event URL.
    The agent calls this at the start of each run to avoid re-processing known companies.
    """
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, company_name, company_type, icp_score, date_added
                FROM event_companies
                WHERE event_url = %s
                ORDER BY icp_score DESC
                """,
                (event_url,),
            )
            rows = cur.fetchall()
            for row in rows:
                if isinstance(row.get("date_added"), (datetime, date)):
                    row["date_added"] = row["date_added"].isoformat()
            return rows
    finally:
        conn.close()


def get_all_companies(
    event_url: str = None,
    company_type: str = None,
    headcount_range: str = None,
    hq_country: str = None,
    industry: str = None,
    min_icp_score: int = None,
) -> list:
    """
    Return event_companies rows with optional filters.
    Used by the Flask dashboard.
    """
    conn = _get_connection()
    try:
        conditions = []
        params = []

        if event_url:
            conditions.append("event_url = %s")
            params.append(event_url)
        if company_type:
            conditions.append("company_type = %s")
            params.append(company_type)
        if headcount_range:
            conditions.append("headcount_range = %s")
            params.append(headcount_range)
        if hq_country:
            conditions.append("hq_country = %s")
            params.append(hq_country)
        if industry:
            conditions.append("industry LIKE %s")
            params.append(f"%{industry}%")
        if min_icp_score is not None:
            conditions.append("icp_score >= %s")
            params.append(min_icp_score)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
            SELECT * FROM event_companies
            {where}
            ORDER BY icp_score DESC, company_name ASC
        """
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            for row in rows:
                for field in ("date_added", "created_at"):
                    if isinstance(row.get(field), (datetime, date)):
                        row[field] = row[field].isoformat()
            return rows
    finally:
        conn.close()


def get_filter_options() -> dict:
    """Return distinct values for each filterable column — used to populate dashboard dropdowns."""
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            options = {}
            for col in ("event_url", "company_type", "headcount_range", "hq_country"):
                cur.execute(
                    f"SELECT DISTINCT {col} FROM event_companies WHERE {col} IS NOT NULL ORDER BY {col}"
                )
                options[col] = [r[col] for r in cur.fetchall()]

            # Industries may be comma-separated — return distinct raw values for now
            cur.execute(
                "SELECT DISTINCT industry FROM event_companies WHERE industry IS NOT NULL ORDER BY industry"
            )
            options["industry"] = [r["industry"] for r in cur.fetchall()]

            cur.execute("SELECT COUNT(*) AS total FROM event_companies")
            options["total"] = cur.fetchone()["total"]

        return options
    finally:
        conn.close()
