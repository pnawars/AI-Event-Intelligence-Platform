# AI Event Intelligence Platform

An agentic AI pipeline that automatically discovers tech events in EMEA, scrapes sponsor and speaker companies, enriches them with firmographic data, scores them for ICP fit, and surfaces everything in a live dashboard.

---

## What It Does

### Phase 1 — Event Sourcing
Claude searches the web for AI, data, and developer conferences in EMEA matching your ICP criteria (AI engineers, ML founders, database buyers). Events are saved to TiDB Cloud with full metadata: dates, location, attendance, ICP score, sponsorship deadlines.

### Phase 2 — Company Intelligence
For each event, the platform extracts every sponsor, speaker company, and exhibitor. Claude enriches each company with:
- **Headcount range** (1-10 → 5000+)
- **Revenue range** (<$1M → $200M+)
- **HQ country & city**
- **Industry** (AI Infrastructure, MLOps, FinTech AI, etc.)
- **ICP score 1-10** — how strong a TiDB / Db9.ai prospect they are
- **ICP notes** — 1-2 sentence rationale

### Phase 3 — Dashboard
A Flask dashboard at `http://localhost:5001` with filters, company cards, charts, and click-through company profiles.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Flask Dashboard                    │
│              http://localhost:5001                   │
│                                                     │
│  [▶▶ Run Pipeline]  [🔍 Direct Scrape]  [🤖 AI Scrape] │
└────────────┬────────────────┬────────────┬──────────┘
             │                │            │
             ▼                ▼            ▼
     pipeline_agent.py  scraper_direct  company_agent.py
     (full pipeline)    (Playwright)    (Claude API)
             │                │            │
             ▼                ▼            ▼
        agent.py         BeautifulSoup  web_search tool
     (event sourcing)   (HTML parsing) (enrichment)
             │                │            │
             └────────────────┴────────────┘
                              │
                              ▼
                    TiDB Cloud (MySQL)
                    ai_events database
                    ├── events
                    └── event_companies
```

---

## Scraping Modes

| Mode | Button | How It Works | Claude API? |
|------|--------|-------------|-------------|
| **Direct** | 🔍 Direct | Playwright headless browser → BeautifulSoup extraction → auto-discovers subpages (exhibitors, sponsors, speakers) | No |
| **AI Scrape** | 🤖 AI Scrape | Claude uses `web_search` to find and enrich companies | Yes |
| **Full Pipeline** | ▶▶ Run Pipeline | Event sourcing + company enrichment for all new events | Yes |

After a Direct scrape, the platform **automatically triggers AI enrichment** to fill in firmographics and ICP scores — no extra action needed.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| AI | Anthropic Claude (`claude-sonnet-4-6`) |
| Database | TiDB Cloud (MySQL-compatible, hosted EU) |
| Scraping | Playwright (headless Chromium) + BeautifulSoup4 |
| Backend | Flask + Flask-CORS |
| Frontend | Vanilla JS + Chart.js |
| Scheduler | Windows Task Scheduler / `schedule` library |

---

## Setup

### 1. Clone & install dependencies

```bash
git clone https://github.com/pnawars/AI-Event-Intelligence-Platform.git
cd AI-Event-Intelligence-Platform
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment

Create a `.env` file in the project root:

```env
ANTHROPIC_API_KEY=sk-ant-api03-...
TIDB_HOST=gateway01.eu-central-1.prod.aws.tidbcloud.com
TIDB_PORT=4000
TIDB_USER=your_user
TIDB_PASSWORD=your_password
TIDB_DATABASE=ai_events
```

### 3. Set up the database

```bash
python setup_companies_db.py
```

### 4. Start the dashboard

```bash
python dashboard/app.py
```

Open **http://localhost:5001**

---

## Running the Pipeline

### Option A — Dashboard (recommended)
Open http://localhost:5001 and click **▶▶ Run Pipeline**.

### Option B — Command line (once)
```bash
python run_pipeline.py --once
```

### Option C — Daily scheduler (Windows Task Scheduler)
```bash
# Run as Administrator
setup_scheduler.bat
```
This registers a daily 07:00 run of the full pipeline.

---

## Scraping a Single Event

Paste any event URL in the dashboard nav bar and click:

- **🔍 Direct** — instant, no API credits, uses Playwright
- **🤖 AI Scrape** — Claude enriches companies with real data

Example URL: `https://www.bigdataparis.com/`

---

## ICP Scoring (1-10)

Scores companies as prospects for **TiDB** (distributed SQL database) and **Db9.ai** (long-term memory for AI agents):

| Score | Signal |
|-------|--------|
| 9-10 | Builds AI agents, LLM apps, RAG systems — needs scalable DB |
| 7-8 | AI/data products, likely needs TiDB/Db9.ai at scale |
| 5-6 | General SaaS or cloud — possible with nurture |
| 3-4 | Traditional tech, no clear AI/data signals |
| 1-2 | Non-tech, consumer, hardware — poor fit |

---

## Project Structure

```
├── agent.py                 # Event sourcing agent (Claude)
├── company_agent.py         # Company enrichment agent (Claude)
├── pipeline_agent.py        # Orchestrates full pipeline
├── scraper_direct.py        # Playwright scraper (no Claude)
├── db_companies.py          # TiDB CRUD operations
├── setup_companies_db.py    # Database schema setup
├── run_pipeline.py          # CLI entry point + scheduler
├── setup_scheduler.bat      # Windows Task Scheduler setup
├── requirements.txt
├── dashboard/
│   ├── app.py               # Flask API + background job runner
│   └── templates/
│       └── index.html       # Full dashboard UI
└── logs/                    # Agent logs + last run summary
```

---

## Dashboard Features

- **Stats row** — total companies, high ICP count, events scraped, countries, industries
- **Charts** — companies by country (bar), by industry (bar), ICP distribution (doughnut)
- **Filters** — event, company type, headcount, revenue, country, industry, ICP range
- **Views** — table view + cards view with company profiles
- **Company modal** — click any company for full firmographic detail
- **Job toasts** — real-time scrape and enrichment progress
- **Pipeline panel** — last run summary, events found, companies saved

---

## Environment Notes

- `.env` is excluded from git (`.gitignore`)
- TiDB Cloud uses TLS — no additional certificate setup needed
- Playwright Chromium is ~300MB — installed once with `playwright install chromium`
- Claude API: uses `claude-sonnet-4-6` for enrichment, `claude-haiku-4-5` for event sourcing

---

## License

MIT
