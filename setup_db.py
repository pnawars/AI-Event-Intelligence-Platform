"""
setup_db.py — One-time setup: creates the ai_events database and events table
in TiDB Cloud.

Run once before the first agent run:
    python setup_db.py
"""

import ssl
import os
import sys
import pymysql
import pymysql.cursors
from dotenv import load_dotenv

load_dotenv()


def get_root_connection():
    """Connect without specifying a database so we can CREATE DATABASE."""
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    return pymysql.connect(
        host=os.getenv("TIDB_HOST", "gateway01.eu-central-1.prod.aws.tidbcloud.com"),
        port=int(os.getenv("TIDB_PORT", 4000)),
        user=os.getenv("TIDB_USER"),
        password=os.getenv("TIDB_PASSWORD"),
        ssl=ssl_ctx,
        autocommit=True,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


DDL_CREATE_DB = "CREATE DATABASE IF NOT EXISTS ai_events CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"

DDL_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    id                    INT UNSIGNED     AUTO_INCREMENT PRIMARY KEY,
    event_name            VARCHAR(255)     NOT NULL,
    event_type            VARCHAR(80)      NOT NULL
                            COMMENT 'conference|meetup|summit|workshop|webinar|hackathon',
    dates                 VARCHAR(120)     NOT NULL
                            COMMENT 'human-readable range, e.g. "14-16 Oct 2025"',
    location              VARCHAR(200)     NOT NULL
                            COMMENT 'City, Country or "Virtual"',
    event_url             VARCHAR(512)     NOT NULL,
    description           TEXT,
    estimated_attendance  INT UNSIGNED     DEFAULT NULL,
    audience_job_functions VARCHAR(512)   DEFAULT NULL
                            COMMENT 'comma-separated roles',
    audience_seniority    VARCHAR(255)     DEFAULT NULL,
    audience_industries   VARCHAR(512)     DEFAULT NULL,
    icp_fit_score         TINYINT UNSIGNED NOT NULL
                            COMMENT '1-10; 10 = perfect ICP fit',
    icp_fit_notes         TEXT,
    sponsorship_available TINYINT(1)       DEFAULT NULL
                            COMMENT '1=yes, 0=no, NULL=unknown',
    submission_deadline   DATE             DEFAULT NULL,
    source_url            VARCHAR(512)     DEFAULT NULL,
    date_added            DATE             NOT NULL,
    status                VARCHAR(50)      NOT NULL DEFAULT 'active'
                            COMMENT 'active|cancelled|postponed|past',
    created_at            TIMESTAMP        DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uq_event_url   (event_url(255)),
    INDEX      idx_score      (icp_fit_score),
    INDEX      idx_deadline   (submission_deadline),
    INDEX      idx_date_added (date_added),
    INDEX      idx_status     (status),
    INDEX      idx_location   (location(100))
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
"""


def setup():
    print("Connecting to TiDB Cloud...")
    try:
        conn = get_root_connection()
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    try:
        with conn.cursor() as cur:
            print("Creating database ai_events...")
            cur.execute(DDL_CREATE_DB)

            print("Switching to ai_events...")
            cur.execute("USE ai_events")

            print("Creating events table...")
            cur.execute(DDL_CREATE_TABLE)

            print("\nSchema verification:")
            cur.execute("SHOW COLUMNS FROM events")
            for col in cur.fetchall():
                print(f"  {col['Field']:30s} {col['Type']}")

        print("\nSetup complete. Database ai_events and table events are ready.")
    finally:
        conn.close()


if __name__ == "__main__":
    setup()
