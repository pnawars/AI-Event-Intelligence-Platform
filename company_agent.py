"""
company_agent.py — Claude agentic loop for the Event Intelligence Agent.

Given an event URL, Claude:
  1. Scrapes the event page (via web_search) to find sponsors, speakers, exhibitors.
  2. Enriches each company: headcount, revenue range, HQ country, industry, website.
  3. Scores each company 1-10 for ICP fit against the TiDB / Db9.ai buyer profile.
  4. Saves each company to the event_companies table in TiDB.

Tools:
  - web_search            : built-in Anthropic tool (server-side, no tool_result needed)
  - get_existing_companies: queries TiDB for already-saved companies at this event URL
  - save_company          : inserts an enriched company into TiDB
"""

import json
import logging
import os
import time
from datetime import date

import anthropic
from dotenv import load_dotenv

import db_companies

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 5,
    },
    {
        "name": "get_existing_companies",
        "description": (
            "Return a list of companies already saved for this event URL "
            "(id, company_name, company_type, icp_score). "
            "Call this at the very start so you don't re-process known companies."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_url": {
                    "type": "string",
                    "description": "The event URL being processed.",
                }
            },
            "required": ["event_url"],
        },
    },
    {
        "name": "save_company",
        "description": (
            "Save an enriched company to the database. "
            "Call this once per company after enrichment and scoring. "
            "icp_score must be an integer 1-10. "
            "headcount_range must be one of: 1-10, 11-50, 51-200, 201-1000, 1001-5000, 5000+. "
            "revenue_range must be one of: <$1M, $1M-$10M, $10M-$50M, $50M-$200M, $200M+. "
            "company_type must be one of: sponsor, speaker, exhibitor."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_url": {
                    "type": "string",
                    "description": "The event URL where this company was found.",
                },
                "event_name": {
                    "type": "string",
                    "description": "Full name of the event.",
                },
                "company_name": {
                    "type": "string",
                    "description": "Official company name.",
                },
                "company_type": {
                    "type": "string",
                    "enum": ["sponsor", "speaker", "exhibitor"],
                    "description": "How this company appears at the event.",
                },
                "website_url": {
                    "type": "string",
                    "description": "Official company website URL.",
                },
                "headcount_range": {
                    "type": "string",
                    "enum": ["1-10", "11-50", "51-200", "201-1000", "1001-5000", "5000+"],
                    "description": "Approximate employee headcount band.",
                },
                "revenue_range": {
                    "type": "string",
                    "enum": ["<$1M", "$1M-$10M", "$10M-$50M", "$50M-$200M", "$200M+"],
                    "description": "Approximate annual revenue band.",
                },
                "hq_country": {
                    "type": "string",
                    "description": "Country where the company is headquartered.",
                },
                "industry": {
                    "type": "string",
                    "description": (
                        "Primary industry or sub-vertical, e.g. "
                        "'AI Infrastructure', 'Database', 'MLOps', 'FinTech', "
                        "'Developer Tools', 'Cloud', 'Cybersecurity', 'Healthcare AI'."
                    ),
                },
                "icp_score": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": (
                        "ICP fit score 1-10 for TiDB/Db9.ai. "
                        "10 = perfect buyer (AI infra/DB company, our ICP build AI agents). "
                        "7-9 = strong fit. 4-6 = partial fit. 1-3 = weak/negative signals."
                    ),
                },
                "icp_notes": {
                    "type": "string",
                    "description": (
                        "1-2 sentences explaining the score. "
                        "Why is this company a good or poor fit as a TiDB/Db9.ai prospect or partner? "
                        "Consider: do they build AI products? Do they need scalable DB/memory infra? "
                        "Are their engineers/buyers likely at this event?"
                    ),
                },
            },
            "required": [
                "event_url", "company_name", "company_type",
                "icp_score", "icp_notes",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the Event Intelligence Agent for TiDB and Db9.ai.

YOUR MISSION: Given an event URL, find every sponsor, speaker company, and exhibitor listed on that event's website. Enrich each company with firmographic data, then score them for ICP fit.

COMPANY CONTEXT:
- TiDB: distributed SQL / HTAP database for high-scale transactional + analytical workloads.
- Db9.ai: long-term memory and database infrastructure for AI agents (RAG, LLM chains, autonomous agents).
- We sell to: startups → enterprise building AI agents, LLM apps, RAG systems, AI-native SaaS.

ICP SCORING (1-10) — Score each company as a PROSPECT for TiDB/Db9.ai:
9-10: Company builds AI agents, LLM apps, or RAG systems AND needs scalable DB/memory infra. Or: AI infrastructure / database company that is a direct peer/partner/competitor signal.
7-8: Company builds AI products or data-heavy applications likely needing TiDB/Db9.ai.
5-6: General SaaS or cloud company with some AI usage — possible prospect with nurture.
3-4: Traditional tech or enterprise without clear AI/data scale signals.
1-2: Non-tech, consumer, hardware-only, or pure academia — poor fit.

POSITIVE SIGNALS (increase score):
- Builds AI agents, chatbots, LLM applications, RAG pipelines
- Uses or builds MLOps / AI infrastructure / vector databases
- Python-native or cloud-native engineering culture
- Handles high-volume transactional or time-series data
- FinTech, HealthTech, Developer Tools, Enterprise SaaS, Logistics AI verticals
- CTO / VP Eng / Head of AI title holders are their buyers

NEGATIVE SIGNALS (reduce score):
- Pure consumer B2C product with no engineering audience
- Hardware / semiconductor / physical manufacturing
- Traditional enterprise IT with no AI or data engineering angle
- Non-technical audience (sales, marketing, HR conferences)
- Government / public sector without AI engineering focus

WORKFLOW:
1. Call get_existing_companies(event_url) to see what's already saved.
2. Use web_search to scrape the event page for sponsors, speakers, exhibitors.
3. For each company not yet saved: use web_search to enrich (headcount, revenue, HQ, industry, website).
4. Call save_company for each enriched company.
5. Aim to process ALL companies found. Do not stop early.
6. If headcount/revenue is unknown after searching, omit those fields — never guess.

DATA RULES:
- company_type: use 'sponsor' for sponsors/partners, 'speaker' for speaker companies, 'exhibitor' for exhibitors/booths.
- If a person is listed as a speaker, use their employer as the company.
- Skip individuals who are self-employed / freelancers with no clear company.
- website_url: official company homepage only.
- industry: be specific — prefer 'AI Infrastructure' over 'Technology'.

Today: {today_date}
Event URL: {event_url}"""

# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

def _dispatch_tool(tool_name: str, tool_input: dict) -> str:
    """Route Claude's tool calls to db_companies functions. Returns JSON string."""
    try:
        if tool_name == "get_existing_companies":
            result = db_companies.get_existing_companies(tool_input["event_url"])
            return json.dumps({"count": len(result), "companies": result}, default=str)

        elif tool_name == "save_company":
            result = db_companies.save_company(tool_input)
            return json.dumps(result)

        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

    except Exception as exc:
        logger.error("Tool dispatch error for %s: %s", tool_name, exc)
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Agentic loop
# ---------------------------------------------------------------------------

MAX_ITERATIONS = 80
INTER_ITER_DELAY = 15       # Seconds between iterations (Tier-1 rate limit)
MAX_MESSAGES = 14           # Prune conversation window to control token usage


def _prune_messages(messages: list) -> list:
    """Keep first user message + last (MAX_MESSAGES - 1) messages."""
    if len(messages) <= MAX_MESSAGES:
        return messages
    return [messages[0]] + messages[-(MAX_MESSAGES - 1):]


def run_agent(event_url: str) -> dict:
    """
    Run one complete event intelligence session for the given event URL.
    Returns stats: {iterations, stop_reason, companies_saved}.
    """
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    today = date.today().isoformat()
    system = SYSTEM_PROMPT.format(today_date=today, event_url=event_url)

    messages = [
        {
            "role": "user",
            "content": (
                f"Process this event: {event_url}\n\n"
                "Find all sponsors, speakers, and exhibitors on the event page. "
                "Enrich each company with firmographic data and score ICP fit. "
                "Save every company you find to the database."
            ),
        }
    ]

    companies_saved = 0
    stop_reason = "unknown"
    iteration = 1

    for iteration in range(1, MAX_ITERATIONS + 1):
        logger.info(
            "Agent iteration %d | messages in context: %d",
            iteration, len(messages),
        )

        messages = _prune_messages(messages)

        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                system=system,
                tools=TOOLS,
                messages=messages,
            )
        except anthropic.RateLimitError as exc:
            wait = min(120 * (2 ** min(iteration - 1, 3)), 960)
            logger.warning(
                "Rate limit (iteration %d) — sleeping %ds. Error: %s",
                iteration, wait, exc,
            )
            time.sleep(wait)
            continue
        except anthropic.BadRequestError as exc:
            logger.error("Bad request error: %s", exc)
            stop_reason = "bad_request"
            break

        messages.append({"role": "assistant", "content": response.content})
        stop_reason = response.stop_reason

        if stop_reason == "end_turn":
            logger.info("Agent finished (end_turn) after %d iterations", iteration)
            for block in response.content:
                if hasattr(block, "text") and block.text:
                    logger.info("Agent summary:\n%s", block.text)
            break

        if stop_reason == "tool_use":
            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

                # web_search is resolved server-side — do NOT return a tool_result for it
                if block.name == "web_search":
                    logger.debug("web_search call (handled server-side)")
                    continue

                logger.info(
                    "Tool call: %s | %s",
                    block.name,
                    json.dumps(block.input)[:140],
                )

                result_str = _dispatch_tool(block.name, block.input)

                if block.name == "save_company":
                    try:
                        parsed = json.loads(result_str)
                        if parsed.get("success"):
                            companies_saved += 1
                    except Exception:
                        pass

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

            if tool_results:
                messages.append({"role": "user", "content": tool_results})

        elif stop_reason == "max_tokens":
            # Model ran out of output tokens mid-response — ask it to continue
            logger.warning("max_tokens hit on iteration %d — sending continue prompt", iteration)
            messages.append({
                "role": "user",
                "content": "Please continue from where you left off — keep saving companies.",
            })

        else:
            logger.warning("Unexpected stop_reason: %s", stop_reason)
            break

        logger.debug("Sleeping %ds before next iteration", INTER_ITER_DELAY)
        time.sleep(INTER_ITER_DELAY)

    else:
        logger.warning("Reached MAX_ITERATIONS (%d) — stopping", MAX_ITERATIONS)
        stop_reason = "max_iterations"

    stats = {
        "iterations": iteration,
        "stop_reason": stop_reason,
        "companies_saved": companies_saved,
    }
    logger.info("Run complete: %s", stats)
    return stats
