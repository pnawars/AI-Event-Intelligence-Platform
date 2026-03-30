/**
 * db/setup.js
 * Run once: node db/setup.js
 * Creates all tables in TiDB Cloud if they don't exist.
 */
import 'dotenv/config';
import mysql from 'mysql2/promise';

export async function getConnection() {
  return mysql.createConnection({
    host:     process.env.TIDB_HOST,
    port:     parseInt(process.env.TIDB_PORT || '4000'),
    user:     process.env.TIDB_USER,
    password: process.env.TIDB_PASSWORD,
    database: process.env.TIDB_DATABASE,
    ssl: { rejectUnauthorized: true },
    connectTimeout: 30000,
  });
}

export async function getPool() {
  return mysql.createPool({
    host:              process.env.TIDB_HOST,
    port:              parseInt(process.env.TIDB_PORT || '4000'),
    user:              process.env.TIDB_USER,
    password:          process.env.TIDB_PASSWORD,
    database:          process.env.TIDB_DATABASE,
    ssl:               { rejectUnauthorized: true },
    connectionLimit:   10,
    connectTimeout:    30000,
    waitForConnections: true,
  });
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS events (
  id              VARCHAR(255) PRIMARY KEY,          -- slug: event-name-yyyy-mm
  name            VARCHAR(500) NOT NULL,
  description     TEXT,
  url             VARCHAR(1000),
  location_city   VARCHAR(255),
  location_country VARCHAR(10) DEFAULT 'UK',         -- UK | IE
  venue           VARCHAR(500),
  start_date      DATE,
  end_date        DATE,
  industry        VARCHAR(100),                      -- Tech & SaaS | Finance & FinTech | AI
  event_type      VARCHAR(100),                      -- Conference | Summit | Expo | Meetup | Hackathon
  expected_attendees INT,
  ticket_price_range VARCHAR(100),
  organiser       VARCHAR(500),
  source_url      VARCHAR(1000),
  discovered_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_industry (industry),
  INDEX idx_start_date (start_date),
  INDEX idx_location (location_country, location_city)
);

CREATE TABLE IF NOT EXISTS companies (
  id              VARCHAR(255) PRIMARY KEY,          -- slug from website domain
  name            VARCHAR(500) NOT NULL,
  website         VARCHAR(1000),
  linkedin_url    VARCHAR(1000),
  industry        VARCHAR(255),
  hq_country      VARCHAR(100),
  hq_city         VARCHAR(255),
  employee_count  VARCHAR(100),                      -- "50-200", "1000+" etc
  revenue_range   VARCHAR(100),                      -- "$1M-$10M" etc
  description     TEXT,
  discovered_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_name (name),
  INDEX idx_industry (industry)
);

CREATE TABLE IF NOT EXISTS event_companies (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  event_id        VARCHAR(255) NOT NULL,
  company_id      VARCHAR(255) NOT NULL,
  role            VARCHAR(100),                      -- Sponsor | Exhibitor | Speaker | Attendee | Organiser
  tier            VARCHAR(100),                      -- Gold Sponsor | Silver | etc
  notes           TEXT,
  discovered_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_event_company_role (event_id, company_id, role),
  INDEX idx_event  (event_id),
  INDEX idx_company (company_id),
  FOREIGN KEY (event_id)   REFERENCES events(id)    ON DELETE CASCADE,
  FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agent_runs (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  run_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  agent           VARCHAR(50),                       -- agent1 | agent2
  status          VARCHAR(50),                       -- success | error
  events_found    INT DEFAULT 0,
  companies_found INT DEFAULT 0,
  error_message   TEXT,
  duration_ms     INT
);
`;

async function setup() {
  console.log('🔌 Connecting to TiDB Cloud...');
  const conn = await getConnection();

  // Run each statement individually (mysql2 doesn't support multi-statement by default)
  const statements = SCHEMA
    .split(';')
    .map(s => s.trim())
    .filter(s => s.length > 0);

  for (const stmt of statements) {
    await conn.execute(stmt);
    const tableName = stmt.match(/TABLE IF NOT EXISTS (\w+)/)?.[1];
    if (tableName) console.log(`  ✅ Table ready: ${tableName}`);
  }

  await conn.end();
  console.log('\n✅ Database setup complete!');
}

// Run directly when invoked as script
if (process.argv[1].endsWith('setup.js')) {
  setup().catch(err => {
    console.error('❌ Setup failed:', err.message);
    process.exit(1);
  });
}
