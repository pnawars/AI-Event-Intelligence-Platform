"""
agent.py — Claude agentic loop for AI Event Sourcing.

Claude uses four tools in a self-directed loop:
  - web_search         : built-in Anthropic tool (server-side, no tool_result needed)
  - check_event_exists : queries TiDB by event_url
  - save_event         : inserts a new event into TiDB
  - get_existing_events: loads existing events so Claude avoids re-researching them

The loop runs until Claude issues end_turn or MAX_ITERATIONS is hit.
"""

import json
import logging
import os
from datetime import date

import anthropic
from dotenv import load_dotenv

import db

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    # Built-in Anthropic web search — Anthropic resolves this server-side.
    # Application code must NOT return a tool_result for web_search blocks.
    # max_uses=3 keeps each turn well within Tier-1 token-per-minute limits.
    {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 3,
    },
    {
        "name": "get_existing_events",
        "description": (
            "Return a summary list (id, event_name, event_url, icp_fit_score, date_added) "
            "of all events already stored in the database. "
            "Call this at the very start of each run so you don't re-research known events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum rows to return. Default 300.",
                    "default": 300,
                }
            },
            "required": [],
        },
    },
    {
        "name": "check_event_exists",
        "description": (
            "Check whether a specific event URL is already stored in the database. "
            "Always call this before save_event to prevent duplicates. "
            "Returns {exists: bool, id: int|null}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_url": {
                    "type": "string",
                    "description": (
                        "The canonical homepage or registration URL of the event. "
                        "Use the official URL without UTM parameters or redirects."
                    ),
                }
            },
            "required": ["event_url"],
        },
    },
    {
        "name": "save_event",
        "description": (
            "Persist a new event to the database. "
            "Only call this after check_event_exists returned exists=false. "
            "icp_fit_score must be an integer 1-10. "
            "dates should be human-readable, e.g. '14-16 Oct 2025' or '5 Nov 2025'. "
            "submission_deadline should be ISO format YYYY-MM-DD or omitted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_name": {
                    "type": "string",
                    "description": "Full official name of the event.",
                },
                "event_type": {
                    "type": "string",
                    "enum": ["conference", "meetup", "summit", "workshop", "webinar", "hackathon"],
                },
                "dates": {
                    "type": "string",
                    "description": "Human-readable date or range, e.g. '14-16 Oct 2025'.",
                },
                "location": {
                    "type": "string",
                    "description": "City, Country — or 'Virtual' or 'Hybrid: City, Country'.",
                },
                "event_url": {
                    "type": "string",
                    "description": "Official event homepage URL (not an aggregator link).",
                },
                "description": {
                    "type": "string",
                    "description": "2-3 sentences: what the event is, who runs it, focus area.",
                },
                "estimated_attendance": {
                    "type": "integer",
                    "description": "Expected attendance. Omit entirely if not publicly stated — never guess.",
                },
                "audience_job_functions": {
                    "type": "string",
                    "description": "Comma-separated primary job roles of attendees.",
                },
                "audience_seniority": {
                    "type": "string",
                    "description": "Comma-separated seniority levels, e.g. 'C-suite, VP, Director, Senior IC'.",
                },
                "audience_industries": {
                    "type": "string",
                    "description": "Comma-separated industries represented at the event.",
                },
                "icp_fit_score": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "ICP fit score 1-10. 10 = perfect fit for TiDB/Db9.ai.",
                },
                "icp_fit_notes": {
                    "type": "string",
                    "description": (
                        "1-2 sentences explaining the score. "
                        "Scores <=6 must explain what is missing. "
                        "Prefix with URGENT if sponsorship/speaking deadline is within 30 days."
                    ),
                },
                "sponsorship_available": {
                    "type": "boolean",
                    "description": "True if sponsorship packages are available, false if not. Omit if unknown.",
                },
                "submission_deadline": {
                    "type": "string",
                    "description": "Sponsorship or speaking submission deadline in YYYY-MM-DD format. Omit if unknown.",
                },
                "source_url": {
                    "type": "string",
                    "description": "URL where this event was discovered (can be an aggregator or search result).",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "cancelled", "postponed"],
                    "description": "Default 'active'.",
                },
            },
            "required": [
                "event_name", "event_type", "dates", "location",
                "event_url", "icp_fit_score", "icp_fit_notes",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an AI Event Sourcing Agent for TiDB (distributed SQL/HTAP database) and Db9.ai (memory/DB infra for AI agents). Find and save relevant tech events to the database daily.

ICP: CTOs, VP Eng, Head of AI/ML/Data, AI-native founders, AI/ML/backend engineers at companies building AI agents, LLM apps, RAG systems, or AI-native SaaS.
Negative signals (reduce score): pure academic, B2C consumer, non-technical audience, hardware-only focus.

GEOGRAPHY: EMEA primary (UK, Germany, Netherlands, Nordics, France, Switzerland, Poland, Israel, UAE). Large global AI events (>1000 attendees) as secondary.

SCORING (icp_fit_score 1-10):
9-10: AI/Data summit, EMEA, CTO/VP audience, sponsorship open
7-8: Strong AI/data theme, EMEA or large global event
5-6: General tech with AI track, or solid AI event outside EMEA (explain gap)
3-4: Peripheral data/AI relevance (explain gap)
1-2: Mostly negative signals (explain gap)

EVENT TYPES (priority order): AI engineering/agent conferences, LLM/GenAI conferences, MLOps/AI infrastructure, database/data engineering, developer conferences with AI tracks, AI startup summits, KubeCon/cloud-native, vertical AI (FinTech/HealthTech), open source with AI community, EMEA city meetups (London, Berlin, Amsterdam, Paris, Stockholm).

WORKFLOW:
1. Call get_existing_events to see what's stored.
2. Search with web_search (max 3 searches per turn; you'll get multiple turns).
3. For each event: call check_event_exists, then save_event if new.
4. Only save events within 18 months of today ({today_date}).
5. Prefix icp_fit_notes with "URGENT: " if deadline within 30 days.
6. Run until 10+ new events saved or searches exhausted.

DATA RULES: event_url = official homepage only; dates = "14-16 Oct 2025" format; submission_deadline = YYYY-MM-DD or omit; estimated_attendance = omit if not public (never guess); description = 2-3 sentences.

Today: {today_date}"""

# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

def _dispatch_tool(tool_name: str, tool_input: dict) -> str:
    """Route Claude's tool calls to db.py functions. Returns JSON string."""
    try:
        if tool_name == "get_existing_events":
            result = db.get_existing_events(tool_input.get("limit", 300))
            return json.dumps({"count": len(result), "events": result}, default=str)

        elif tool_name == "check_event_exists":
            result = db.check_event_exists(tool_input["event_url"])
            return json.dumps(result)

        elif tool_name == "save_event":
            result = db.save_event(tool_input)
            return json.dumps(result)

        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

    except Exception as exc:
        logger.error("Tool dispatch error for %s: %s", tool_name, exc)
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Agentic loop
# ---------------------------------------------------------------------------

MAX_ITERATIONS = 60        # Safety ceiling — one iteration per tool-use batch
INTER_ITER_DELAY = 20      # Seconds to sleep between iterations (Tier-1 rate limit)
MAX_MESSAGES = 12          # Prune conversation when it exceeds this many messages


def _prune_messages(messages: list) -> list:
    """
    Keep the conversation window small to prevent input token explosion
    from accumulated web search results.
    Preserves: first user message + last (MAX_MESSAGES - 1) messages.
    """
    if len(messages) <= MAX_MESSAGES:
        return messages
    return [messages[0]] + messages[-(MAX_MESSAGES - 1):]


def run_agent() -> dict:
    """
    Run one complete event sourcing session.
    Returns a stats dict: {iterations, stop_reason, events_saved}.
    """
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    today = date.today().isoformat()
    system = SYSTEM_PROMPT.format(today_date=today)

    messages = [
        {
            "role": "user",
            "content": (
                f"Today is {today}. Run the daily event sourcing job. "
                "Find and store as many new relevant AI/tech events as possible, "
                "covering all high-priority categories and EMEA regions."
            ),
        }
    ]

    events_saved = 0
    stop_reason = "unknown"

    for iteration in range(1, MAX_ITERATIONS + 1):
        logger.info("Agent iteration %d (messages in context: %d)", iteration, len(messages))

        # Prune conversation to keep input tokens manageable
        messages = _prune_messages(messages)

        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                system=system,
                tools=TOOLS,
                messages=messages,
            )
        except anthropic.RateLimitError as exc:
            import time
            # Exponential backoff: 2m, 4m, 8m … capped at 16m
            wait = min(120 * (2 ** min(iteration - 1, 3)), 960)
            logger.warning("Rate limit hit (iteration %d) — sleeping %ds. Error: %s", iteration, wait, exc)
            time.sleep(wait)
            continue
        except anthropic.BadRequestError as exc:
            logger.error("Bad request error: %s", exc)
            stop_reason = "bad_request"
            break

        # Append assistant response to the conversation
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

                # IMPORTANT: web_search is resolved server-side by Anthropic.
                # We must NOT send a tool_result for it — doing so causes a 400 error.
                if block.name == "web_search":
                    logger.debug("web_search call (handled server-side)")
                    continue

                logger.info(
                    "Tool call: %s | %s",
                    block.name,
                    json.dumps(block.input)[:120],
                )

                result_str = _dispatch_tool(block.name, block.input)

                # Track saves for the summary
                if block.name == "save_event":
                    try:
                        parsed = json.loads(result_str)
                        if parsed.get("success"):
                            events_saved += 1
                    except Exception:
                        pass

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    }
                )

            # Only append a user message if we have local tool results to return.
            # If all tool_use blocks were web_search (server-side), the next
            # API call will include search results automatically — no user message needed.
            if tool_results:
                messages.append({"role": "user", "content": tool_results})

        else:
            logger.warning("Unexpected stop_reason: %s", stop_reason)
            break

        # Pace calls to stay within Tier-1 token-per-minute budget
        import time
        logger.debug("Sleeping %ds before next iteration", INTER_ITER_DELAY)
        time.sleep(INTER_ITER_DELAY)

    else:
        logger.warning("Reached MAX_ITERATIONS (%d) — stopping", MAX_ITERATIONS)
        stop_reason = "max_iterations"

    stats = {
        "iterations": iteration,
        "stop_reason": stop_reason,
        "events_saved": events_saved,
    }
    logger.info("Run complete: %s", stats)
    return stats
