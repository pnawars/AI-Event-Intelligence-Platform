/**
 * agents/agent1_eventFinder.js
 * Agent 1: Searches the web for UK & Ireland Tech/FinTech/AI events,
 * structures the results, and upserts them into TiDB Cloud.
 */
import 'dotenv/config';
import Anthropic from '@anthropic-ai/sdk';
import { getPool } from '../db/setup.js';

const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

// ─── Prompt ───────────────────────────────────────────────────────────────────
function buildSystemPrompt() {
  const today = new Date().toISOString().split('T')[0];
  const sixMonthsOut = new Date(Date.now() + 180 * 86400000).toISOString().split('T')[0];

  return `You are an expert event research agent. Today is ${today}.

Your job: find upcoming business events in the UK and Ireland across these industries:
- Tech & SaaS
- Finance & FinTech  
- AI (Artificial Intelligence, Machine Learning, LLMs)

Search for events happening between today and ${sixMonthsOut}.

For EACH event you find, extract a JSON object with EXACTLY these fields:
{
  "id": "<slugified-event-name>-<yyyy-mm>",        // e.g. "turing-fest-2025-08"
  "name": "Full event name",
  "description": "2-3 sentence description",
  "url": "https://official-event-website.com",
  "location_city": "London",
  "location_country": "UK",                         // UK or IE (Ireland)
  "venue": "ExCeL London or TBC",
  "start_date": "2025-09-15",                       // ISO date
  "end_date": "2025-09-16",
  "industry": "Tech & SaaS",                        // MUST be one of: Tech & SaaS | Finance & FinTech | AI
  "event_type": "Conference",                        // Conference | Summit | Expo | Meetup | Hackathon | Awards
  "expected_attendees": 2000,                        // integer estimate, null if unknown
  "ticket_price_range": "£500-£1500",               // string or null
  "organiser": "Organisng company name",
  "source_url": "https://where-you-found-this.com"
}

RULES:
- Only events physically in the UK or Ireland (in-person or hybrid). No online-only events.
- Minimum event size: 100 expected attendees.
- Do NOT invent events. Only report events you can find evidence for via search.
- Return ONLY a valid JSON array: [ {...}, {...} ]
- No markdown, no explanation, no preamble. Pure JSON array only.`;
}

const USER_QUERIES = [
  'upcoming AI conferences UK Ireland 2025 2026',
  'FinTech summit conference UK London 2025 2026',
  'Tech SaaS expo conference UK Ireland 2025 2026',
  'machine learning AI summit London Edinburgh Dublin 2025',
  'financial technology conference London 2025 2026',
  'startup tech conference UK 2025 2026',
];

// ─── Slug helper ──────────────────────────────────────────────────────────────
function toSlug(str) {
  return str.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

// ─── DB upsert ────────────────────────────────────────────────────────────────
async function upsertEvents(pool, events) {
  let inserted = 0;
  let updated = 0;

  const sql = `
    INSERT INTO events
      (id, name, description, url, location_city, location_country, venue,
       start_date, end_date, industry, event_type, expected_attendees,
       ticket_price_range, organiser, source_url)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON DUPLICATE KEY UPDATE
      name              = VALUES(name),
      description       = VALUES(description),
      url               = VALUES(url),
      location_city     = VALUES(location_city),
      venue             = VALUES(venue),
      start_date        = VALUES(start_date),
      end_date          = VALUES(end_date),
      industry          = VALUES(industry),
      event_type        = VALUES(event_type),
      expected_attendees = VALUES(expected_attendees),
      ticket_price_range = VALUES(ticket_price_range),
      organiser         = VALUES(organiser),
      source_url        = VALUES(source_url),
      updated_at        = CURRENT_TIMESTAMP
  `;

  for (const ev of events) {
    // Ensure ID is always a slug
    const id = ev.id || `${toSlug(ev.name)}-${ev.start_date?.substring(0, 7) || 'tbd'}`;
    const [result] = await pool.execute(sql, [
      id, ev.name, ev.description, ev.url,
      ev.location_city, ev.location_country || 'UK', ev.venue,
      ev.start_date || null, ev.end_date || null,
      ev.industry, ev.event_type, ev.expected_attendees || null,
      ev.ticket_price_range || null, ev.organiser || null, ev.source_url || null,
    ]);
    if (result.insertId > 0 || result.affectedRows === 1) inserted++;
    else updated++;
  }

  return { inserted, updated };
}

// ─── Parse JSON from Claude response ─────────────────────────────────────────
function extractJSON(text) {
  // Find first '[' and last ']' — strip any accidental prose
  const start = text.indexOf('[');
  const end   = text.lastIndexOf(']');
  if (start === -1 || end === -1) return [];
  try {
    return JSON.parse(text.slice(start, end + 1));
  } catch {
    return [];
  }
}

// ─── Main ─────────────────────────────────────────────────────────────────────
export async function runAgent1() {
  const startTime = Date.now();
  console.log('\n🔍 Agent 1: Event Finder starting...');

  const pool = await getPool();
  let totalInserted = 0;
  let totalUpdated  = 0;
  const seen = new Set(); // deduplicate across search batches

  for (const query of USER_QUERIES) {
    console.log(`  🌐 Searching: "${query}"`);
    try {
      const response = await client.messages.create({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 4000,
        system: buildSystemPrompt(),
        tools: [{ type: 'web_search_20250305', name: 'web_search' }],
        messages: [{ role: 'user', content: query }],
      });

      // Collect all text blocks from the response
      const text = response.content
        .filter(b => b.type === 'text')
        .map(b => b.text)
        .join('\n');

      const events = extractJSON(text);
      const fresh  = events.filter(e => !seen.has(e.id || e.name));
      fresh.forEach(e => seen.add(e.id || e.name));

      if (fresh.length > 0) {
        const { inserted, updated } = await upsertEvents(pool, fresh);
        totalInserted += inserted;
        totalUpdated  += updated;
        console.log(`     ↳ Found ${fresh.length} events → ${inserted} new, ${updated} updated`);
      } else {
        console.log(`     ↳ No new events extracted`);
      }

      // Polite delay between searches
      await new Promise(r => setTimeout(r, 2000));

    } catch (err) {
      console.error(`     ⚠️  Search failed: ${err.message}`);
    }
  }

  const duration = Date.now() - startTime;

  // Log the run
  await pool.execute(
    `INSERT INTO agent_runs (agent, status, events_found, duration_ms) VALUES (?, ?, ?, ?)`,
    ['agent1', 'success', totalInserted + totalUpdated, duration]
  );

  await pool.end();

  console.log(`\n✅ Agent 1 done in ${(duration / 1000).toFixed(1)}s`);
  console.log(`   📅 ${totalInserted} new events | ${totalUpdated} updated`);

  return { inserted: totalInserted, updated: totalUpdated };
}

// Run directly when invoked as script
if (process.argv[1].endsWith('agent1_eventFinder.js')) {
  runAgent1().catch(err => {
    console.error('❌ Agent 1 failed:', err);
    process.exit(1);
  });
}
