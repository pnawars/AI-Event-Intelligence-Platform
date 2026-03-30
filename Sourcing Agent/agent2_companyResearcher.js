/**
 * agents/agent2_companyResearcher.js
 * Agent 2: For each event in the DB, researches companies attending/sponsoring,
 * including LinkedIn, website, revenue, company size, and role at the event.
 */
import 'dotenv/config';
import Anthropic from '@anthropic-ai/sdk';
import { getPool } from '../db/setup.js';

const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

// ─── Prompt ───────────────────────────────────────────────────────────────────
function buildSystemPrompt(event) {
  return `You are a B2B company intelligence researcher.

You are researching companies connected to this event:
Event: "${event.name}"
Date: ${event.start_date}
Location: ${event.location_city}, ${event.location_country}
Industry: ${event.industry}
URL: ${event.url || 'N/A'}

Search for:
1. Sponsors of this event (Gold, Silver, Bronze sponsors)
2. Exhibitors or vendors with stands/booths
3. Key speakers and their companies
4. Event partners and media partners

For EACH company found, return a JSON object with EXACTLY these fields:
{
  "company_id": "<domain-slug>",          // e.g. "salesforce-com", "monzo-com"
  "name": "Company Full Name",
  "website": "https://www.company.com",
  "linkedin_url": "https://www.linkedin.com/company/company-name",
  "industry": "FinTech",                  // company's own industry
  "hq_country": "UK",
  "hq_city": "London",
  "employee_count": "1000-5000",          // band: 1-10 | 11-50 | 51-200 | 201-1000 | 1001-5000 | 5000+
  "revenue_range": "$10M-$50M",           // band: <$1M | $1M-$10M | $10M-$50M | $50M-$250M | $250M+ | unknown
  "description": "One sentence about what this company does.",
  "role": "Sponsor",                      // Sponsor | Exhibitor | Speaker | Partner | Organiser
  "tier": "Gold Sponsor"                  // sponsor tier or null
}

RULES:
- Only include companies you find actual evidence for at this event.
- Try to find LinkedIn company pages (linkedin.com/company/...).
- Estimate revenue and size from public sources (Crunchbase, LinkedIn, news).
- Return ONLY a valid JSON array: [ {...}, {...} ]
- No markdown, no explanation, no preamble. Pure JSON array only.
- Aim for 5-20 companies per event.`;
}

// ─── Slug helper ──────────────────────────────────────────────────────────────
function domainSlug(website, name) {
  if (website) {
    try {
      const domain = new URL(website).hostname.replace(/^www\./, '');
      return domain.replace(/\./g, '-');
    } catch {}
  }
  return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

// ─── DB upserts ───────────────────────────────────────────────────────────────
async function upsertCompany(pool, c) {
  const sql = `
    INSERT INTO companies
      (id, name, website, linkedin_url, industry, hq_country, hq_city,
       employee_count, revenue_range, description)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON DUPLICATE KEY UPDATE
      name          = VALUES(name),
      website       = VALUES(website),
      linkedin_url  = COALESCE(VALUES(linkedin_url), linkedin_url),
      employee_count = COALESCE(VALUES(employee_count), employee_count),
      revenue_range = COALESCE(VALUES(revenue_range), revenue_range),
      description   = COALESCE(VALUES(description), description),
      updated_at    = CURRENT_TIMESTAMP
  `;
  await pool.execute(sql, [
    c.company_id, c.name, c.website || null, c.linkedin_url || null,
    c.industry || null, c.hq_country || null, c.hq_city || null,
    c.employee_count || null, c.revenue_range || null, c.description || null,
  ]);
}

async function upsertEventCompany(pool, eventId, companyId, role, tier, notes) {
  const sql = `
    INSERT INTO event_companies (event_id, company_id, role, tier, notes)
    VALUES (?, ?, ?, ?, ?)
    ON DUPLICATE KEY UPDATE
      tier  = COALESCE(VALUES(tier), tier),
      notes = COALESCE(VALUES(notes), notes)
  `;
  await pool.execute(sql, [eventId, companyId, role || 'Attendee', tier || null, notes || null]);
}

// ─── Parse JSON from Claude response ─────────────────────────────────────────
function extractJSON(text) {
  const start = text.indexOf('[');
  const end   = text.lastIndexOf(']');
  if (start === -1 || end === -1) return [];
  try {
    return JSON.parse(text.slice(start, end + 1));
  } catch {
    return [];
  }
}

// ─── Events to process ───────────────────────────────────────────────────────
async function getUnresearchedEvents(pool, limit = 20) {
  // Get events that have no companies researched yet (or were updated recently)
  const [rows] = await pool.execute(`
    SELECT e.*
    FROM events e
    LEFT JOIN event_companies ec ON e.id = ec.event_id
    WHERE ec.event_id IS NULL
      AND e.start_date >= CURDATE()
    ORDER BY e.start_date ASC
    LIMIT ?
  `, [limit]);
  return rows;
}

// ─── Main ─────────────────────────────────────────────────────────────────────
export async function runAgent2() {
  const startTime = Date.now();
  console.log('\n🏢 Agent 2: Company Researcher starting...');

  const pool = await getPool();
  const events = await getUnresearchedEvents(pool, 15);

  if (events.length === 0) {
    console.log('   ℹ️  No new events to research. All events already have company data.');
    await pool.end();
    return { companies: 0, event_companies: 0 };
  }

  console.log(`   📋 Processing ${events.length} events...`);

  let totalCompanies = 0;

  for (const event of events) {
    console.log(`\n  🔎 Researching: ${event.name} (${event.start_date})`);
    try {
      const response = await client.messages.create({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 4000,
        system: buildSystemPrompt(event),
        tools: [{ type: 'web_search_20250305', name: 'web_search' }],
        messages: [{
          role: 'user',
          content: `Find all companies (sponsors, exhibitors, speakers, partners) for "${event.name}". Search: "${event.name} sponsors exhibitors 2025" and "${event.name} exhibitor list"`,
        }],
      });

      const text = response.content
        .filter(b => b.type === 'text')
        .map(b => b.text)
        .join('\n');

      const companies = extractJSON(text);

      for (const c of companies) {
        // Ensure company_id is set
        c.company_id = c.company_id || domainSlug(c.website, c.name);

        await upsertCompany(pool, c);
        await upsertEventCompany(pool, event.id, c.company_id, c.role, c.tier, null);
        totalCompanies++;
      }

      console.log(`     ↳ Found ${companies.length} companies`);

      // Polite delay
      await new Promise(r => setTimeout(r, 3000));

    } catch (err) {
      console.error(`     ⚠️  Failed: ${err.message}`);
    }
  }

  const duration = Date.now() - startTime;

  await pool.execute(
    `INSERT INTO agent_runs (agent, status, companies_found, duration_ms) VALUES (?, ?, ?, ?)`,
    ['agent2', 'success', totalCompanies, duration]
  );

  await pool.end();

  console.log(`\n✅ Agent 2 done in ${(duration / 1000).toFixed(1)}s`);
  console.log(`   🏢 ${totalCompanies} company-event relationships added`);

  return { companies: totalCompanies };
}

// Run directly when invoked as script
if (process.argv[1].endsWith('agent2_companyResearcher.js')) {
  runAgent2().catch(err => {
    console.error('❌ Agent 2 failed:', err);
    process.exit(1);
  });
}
