# ⚡ Event Intelligence Agent System
> Auto-sources UK & Ireland Tech, FinTech, and AI events daily — finds attending companies, stores everything in TiDB Cloud, and visualises it in a live dashboard.

---

## Architecture

```
Daily Cron (07:00 London)
      │
      ▼
[Agent 1: Event Finder]
  Claude + web_search
  Finds UK/IE events → TiDB events table
      │
      ▼
[Agent 2: Company Researcher]
  Claude + web_search
  Finds sponsors/exhibitors per event → companies + event_companies tables
      │
      ▼
[Dashboard API]  ←─── [Dashboard UI]
  Express + TiDB        index.html
```

---

## Quick Start

### 1. Prerequisites
- Node.js 18+
- TiDB Cloud account (free tier works)
- Anthropic API key

### 2. Install
```bash
npm install
```

### 3. Configure
```bash
cp .env.example .env
# Edit .env with your keys
```

Get TiDB credentials from: https://tidbcloud.com → your cluster → Connect → Node.js

### 4. Set up the database
```bash
node db/setup.js
```
Creates 4 tables: `events`, `companies`, `event_companies`, `agent_runs`

### 5. First run (pipeline)
```bash
npm run run-once
```
This runs Agent 1 (event finder) then Agent 2 (company researcher) immediately.
Expect ~5-15 minutes for a full run.

### 6. Start the dashboard
```bash
# Terminal 1 — API server
npm run dashboard

# Open in browser
open dashboard/index.html
```

### 7. Start the daily scheduler
```bash
# Terminal 1 (keep running) — runs every day at 07:00 London time
npm start
```

---

## Project Structure

```
event-agent/
├── agents/
│   ├── agent1_eventFinder.js       # Finds events via web search
│   └── agent2_companyResearcher.js # Finds companies per event
├── db/
│   └── setup.js                    # TiDB schema + connection pool
├── scheduler/
│   ├── index.js                    # Cron scheduler (npm start)
│   └── runOnce.js                  # Manual trigger (npm run run-once)
├── dashboard/
│   ├── server.js                   # Express API for dashboard
│   └── index.html                  # Dashboard UI (open in browser)
├── .env.example
└── package.json
```

---

## NPM Scripts

| Command | Description |
|---|---|
| `npm start` | Start the daily cron scheduler |
| `npm run run-once` | Run the full pipeline once immediately |
| `npm run agent1` | Run only Agent 1 (event discovery) |
| `npm run agent2` | Run only Agent 2 (company research) |
| `npm run setup-db` | Create/verify TiDB tables |
| `npm run dashboard` | Start the dashboard API server |

---

## Filters / Target Profile

| Filter | Value |
|---|---|
| Countries | UK 🇬🇧, Ireland 🇮🇪 |
| Industries | Tech & SaaS, Finance & FinTech, AI |
| Min. size | 100 attendees |
| Format | In-person & hybrid |
| Horizon | Next 6 months |

---

## TiDB Schema

### `events`
Primary key: `id` (slug: `event-name-yyyy-mm`)
Key fields: name, location, dates, industry, event_type, expected_attendees

### `companies`
Primary key: `id` (domain slug: `company-com`)
Key fields: name, website, linkedin_url, employee_count, revenue_range

### `event_companies` (join table)
Links events ↔ companies with `role` (Sponsor/Exhibitor/Speaker/Partner) and `tier`

### `agent_runs`
Audit log of every pipeline execution

---

## Customisation

### Add more industries
In `agents/agent1_eventFinder.js`, update `USER_QUERIES` and the system prompt industry list.

### Change schedule
In `.env`: `CRON_SCHEDULE=0 7 * * *` (cron syntax, Europe/London timezone)

### Run more events through Agent 2
In `agent2_companyResearcher.js`, change the `limit` in `getUnresearchedEvents(pool, 15)`.

---

## Cost Estimate

Each pipeline run uses approximately:
- Agent 1: ~6 searches × ~3000 tokens = ~18K tokens
- Agent 2: ~15 events × ~2000 tokens = ~30K tokens
- **Total per day: ~50K tokens** → ~$0.15/day on claude-sonnet-4

---

## Troubleshooting

**TiDB connection refused**
- Check `TIDB_HOST` — use the exact string from TiDB Cloud console
- TiDB Cloud requires SSL — `ssl: { rejectUnauthorized: true }` is already set

**No events returned**
- Check your `ANTHROPIC_API_KEY` is valid
- Run `npm run agent1` directly to see verbose output

**Dashboard shows "API offline"**
- Make sure `npm run dashboard` is running in a separate terminal
- Default port is 3001 — check nothing else is using it
