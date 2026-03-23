"""
setup_companies_db.py — Creates the event_companies table in TiDB Cloud.

Run once before the first company agent run:
    python setup_companies_db.py
"""

import ssl
import os
import sys
import pymysql
import pymysql.cursors
from dotenv import load_dotenv

load_dotenv()


def get_root_connection():
    """Connect without specifying a database so we can CREATE DATABASE if needed."""
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


DDL_CREATE_DB = (
    "CREATE DATABASE IF NOT EXISTS ai_events "
    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
)

DDL_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS event_companies (
    id               INT UNSIGNED     AUTO_INCREMENT PRIMARY KEY,
    event_url        VARCHAR(512)     NOT NULL
                       COMMENT 'URL of the event page that was scraped',
    event_name       VARCHAR(255)     DEFAULT NULL,
    company_name     VARCHAR(255)     NOT NULL,
    company_type     VARCHAR(50)      NOT NULL
                       COMMENT 'sponsor | speaker | exhibitor',
    website_url      VARCHAR(512)     DEFAULT NULL,
    headcount_range  VARCHAR(50)      DEFAULT NULL
                       COMMENT '1-10 | 11-50 | 51-200 | 201-1000 | 1001-5000 | 5000+',
    revenue_range    VARCHAR(50)      DEFAULT NULL
                       COMMENT '<$1M | $1M-$10M | $10M-$50M | $50M-$200M | $200M+',
    hq_country       VARCHAR(100)     DEFAULT NULL,
    hq_city          VARCHAR(100)     DEFAULT NULL,
    industry         VARCHAR(200)     DEFAULT NULL,
    icp_score        TINYINT UNSIGNED NOT NULL
                       COMMENT '1-10; 10 = perfect ICP fit for TiDB/Db9.ai',
    icp_notes        TEXT             DEFAULT NULL,
    date_added       DATE             NOT NULL,
    created_at       TIMESTAMP        DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uq_company_event (event_url(255), company_name(200)),
    INDEX idx_event_url   (event_url(255)),
    INDEX idx_icp_score   (icp_score),
    INDEX idx_company_type (company_type),
    INDEX idx_hq_country  (hq_country),
    INDEX idx_industry    (industry(100))
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
"""

DDL_MIGRATE_HQ_CITY = "ALTER TABLE event_companies ADD COLUMN hq_city VARCHAR(100) DEFAULT NULL AFTER hq_country"


def setup():
    print("Connecting to TiDB Cloud...")
    try:
        conn = get_root_connection()
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    try:
        with conn.cursor() as cur:
            print("Ensuring database ai_events exists...")
            cur.execute(DDL_CREATE_DB)

            print("Switching to ai_events...")
            cur.execute("USE ai_events")

            print("Creating event_companies table (if not exists)...")
            cur.execute(DDL_CREATE_TABLE)

            # Migration: add hq_city to existing tables that predate this column
            try:
                cur.execute(DDL_MIGRATE_HQ_CITY)
                print("Added hq_city column.")
            except Exception as e:
                if "Duplicate column name" in str(e) or "hq_city" in str(e).lower():
                    print("hq_city column already present — skipping migration.")
                else:
                    raise

            print("\nSchema verification:")
            cur.execute("SHOW COLUMNS FROM event_companies")
            for col in cur.fetchall():
                print(f"  {col['Field']:25s} {col['Type']}")

        print("\nSetup complete. Table event_companies is ready.")
    finally:
        conn.close()


if __name__ == "__main__":
    setup()
