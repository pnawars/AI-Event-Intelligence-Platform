/**
 * dashboard/server.js
 * Lightweight Express API — the dashboard artifact calls these endpoints
 * to pull live data from TiDB Cloud.
 *
 * Start with: npm run dashboard
 * Default port: 3001
 */
import 'dotenv/config';
import express from 'express';
import cors from 'cors';
import { getPool } from '../db/setup.js';

const app  = express();
const PORT = process.env.DASHBOARD_PORT || 3001;

app.use(cors());
app.use(express.json());

let pool;
async function db() {
  if (!pool) pool = await getPool();
  return pool;
}

// ─── GET /api/stats ────────────────────────────────────────────────────────
// Top-level KPIs: total events, total companies, next event
app.get('/api/stats', async (req, res) => {
  try {
    const conn = await db();
    const [[{ total_events }]]     = await conn.execute('SELECT COUNT(*) AS total_events FROM events WHERE start_date >= CURDATE()');
    const [[{ total_companies }]]  = await conn.execute('SELECT COUNT(*) AS total_companies FROM companies');
    const [[{ total_relations }]]  = await conn.execute('SELECT COUNT(*) AS total_relations FROM event_companies');
    const [nextEvent]              = await conn.execute(`
      SELECT name, start_date, location_city, industry
      FROM events WHERE start_date >= CURDATE()
      ORDER BY start_date ASC LIMIT 1
    `);
    const [[{ last_run }]] = await conn.execute(`
      SELECT MAX(run_at) AS last_run FROM agent_runs WHERE status = 'success'
    `);
    res.json({ total_events, total_companies, total_relations, nextEvent: nextEvent[0] || null, last_run });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── GET /api/events ──────────────────────────────────────────────────────
// Filterable event list
// Query params: industry, country, event_type, from, to, search, limit, offset
app.get('/api/events', async (req, res) => {
  try {
    const conn = await db();
    const { industry, country, event_type, from, to, search, limit = 50, offset = 0 } = req.query;

    let where = ['start_date >= CURDATE()'];
    const params = [];

    if (industry)   { where.push('industry = ?');         params.push(industry); }
    if (country)    { where.push('location_country = ?'); params.push(country); }
    if (event_type) { where.push('event_type = ?');       params.push(event_type); }
    if (from)       { where.push('start_date >= ?');      params.push(from); }
    if (to)         { where.push('start_date <= ?');      params.push(to); }
    if (search)     {
      where.push('(name LIKE ? OR description LIKE ? OR location_city LIKE ?)');
      params.push(`%${search}%`, `%${search}%`, `%${search}%`);
    }

    const whereSQL = where.length ? `WHERE ${where.join(' AND ')}` : '';
    const [rows] = await conn.execute(`
      SELECT e.*,
        COUNT(DISTINCT ec.company_id) AS company_count
      FROM events e
      LEFT JOIN event_companies ec ON e.id = ec.event_id
      ${whereSQL}
      GROUP BY e.id
      ORDER BY e.start_date ASC
      LIMIT ? OFFSET ?
    `, [...params, parseInt(limit), parseInt(offset)]);

    const [[{ total }]] = await conn.execute(
      `SELECT COUNT(*) AS total FROM events ${whereSQL}`, params
    );

    res.json({ events: rows, total, limit: parseInt(limit), offset: parseInt(offset) });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── GET /api/events/:id ──────────────────────────────────────────────────
// Single event with all associated companies
app.get('/api/events/:id', async (req, res) => {
  try {
    const conn = await db();
    const [events] = await conn.execute('SELECT * FROM events WHERE id = ?', [req.params.id]);
    if (!events.length) return res.status(404).json({ error: 'Event not found' });

    const [companies] = await conn.execute(`
      SELECT c.*, ec.role, ec.tier, ec.discovered_at AS relation_date
      FROM event_companies ec
      JOIN companies c ON ec.company_id = c.id
      WHERE ec.event_id = ?
      ORDER BY ec.role, c.name
    `, [req.params.id]);

    res.json({ event: events[0], companies });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── GET /api/companies ───────────────────────────────────────────────────
// Filterable company list
app.get('/api/companies', async (req, res) => {
  try {
    const conn = await db();
    const { industry, country, revenue, employees, search, limit = 50, offset = 0 } = req.query;

    let where = [];
    const params = [];

    if (industry)  { where.push('c.industry LIKE ?');        params.push(`%${industry}%`); }
    if (country)   { where.push('c.hq_country = ?');         params.push(country); }
    if (revenue)   { where.push('c.revenue_range = ?');      params.push(revenue); }
    if (employees) { where.push('c.employee_count = ?');     params.push(employees); }
    if (search)    {
      where.push('(c.name LIKE ? OR c.description LIKE ?)');
      params.push(`%${search}%`, `%${search}%`);
    }

    const whereSQL = where.length ? `WHERE ${where.join(' AND ')}` : '';
    const [rows] = await conn.execute(`
      SELECT c.*,
        COUNT(DISTINCT ec.event_id) AS event_count,
        GROUP_CONCAT(DISTINCT ec.role ORDER BY ec.role SEPARATOR ', ') AS roles
      FROM companies c
      LEFT JOIN event_companies ec ON c.id = ec.company_id
      ${whereSQL}
      GROUP BY c.id
      ORDER BY event_count DESC, c.name ASC
      LIMIT ? OFFSET ?
    `, [...params, parseInt(limit), parseInt(offset)]);

    res.json({ companies: rows, limit: parseInt(limit), offset: parseInt(offset) });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── GET /api/charts/by-industry ─────────────────────────────────────────
app.get('/api/charts/by-industry', async (req, res) => {
  try {
    const conn = await db();
    const [rows] = await conn.execute(`
      SELECT industry, COUNT(*) AS count
      FROM events
      WHERE start_date >= CURDATE()
      GROUP BY industry ORDER BY count DESC
    `);
    res.json(rows);
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── GET /api/charts/by-month ────────────────────────────────────────────
app.get('/api/charts/by-month', async (req, res) => {
  try {
    const conn = await db();
    const [rows] = await conn.execute(`
      SELECT DATE_FORMAT(start_date, '%Y-%m') AS month, COUNT(*) AS count
      FROM events
      WHERE start_date >= CURDATE() AND start_date <= DATE_ADD(CURDATE(), INTERVAL 12 MONTH)
      GROUP BY month ORDER BY month ASC
    `);
    res.json(rows);
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── GET /api/charts/by-city ─────────────────────────────────────────────
app.get('/api/charts/by-city', async (req, res) => {
  try {
    const conn = await db();
    const [rows] = await conn.execute(`
      SELECT location_city AS city, COUNT(*) AS count
      FROM events
      WHERE start_date >= CURDATE()
      GROUP BY location_city ORDER BY count DESC LIMIT 10
    `);
    res.json(rows);
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── GET /api/charts/company-roles ───────────────────────────────────────
app.get('/api/charts/company-roles', async (req, res) => {
  try {
    const conn = await db();
    const [rows] = await conn.execute(`
      SELECT role, COUNT(*) AS count
      FROM event_companies
      GROUP BY role ORDER BY count DESC
    `);
    res.json(rows);
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── Start (local only — Vercel uses the export below) ───────────────────────
if (process.env.VERCEL !== '1') {
  app.listen(PORT, () => {
    console.log(`\n📊 Dashboard API running at http://localhost:${PORT}`);
    console.log(`   GET /api/stats`);
    console.log(`   GET /api/events?industry=AI&country=UK`);
    console.log(`   GET /api/events/:id`);
    console.log(`   GET /api/companies?search=fintech`);
    console.log(`   GET /api/charts/by-industry`);
    console.log(`   GET /api/charts/by-month\n`);
  });
}

export default app;
