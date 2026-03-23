"""
scraper_direct.py — Zero-AI direct scraper for event pages.

Uses a real headless browser (Playwright) so JavaScript-rendered sites
(React / Vue / Angular SPAs like bigdataparis.com) are fully loaded before
extraction.  Falls back to requests + BeautifulSoup for simple static sites.

Setup (one-time):
    pip install playwright beautifulsoup4
    playwright install chromium

Usage:
    from scraper_direct import scrape_event
    result = scrape_event("https://www.bigdataparis.com/")
"""

import logging
import re
from datetime import date
from urllib.parse import urlparse, urljoin

import db_companies

logger = logging.getLogger(__name__)

# ── ICP keyword scoring (no Claude needed) ─────────────────────────────────

_HIGH_ICP   = ["ai", "llm", "gpt", "generative", "machine learning", "deep learning",
               "vector", "embedding", "rag", "agent", "mlops", "databricks", "snowflake",
               "openai", "anthropic", "hugging", "nvidia", "cuda"]
_MED_ICP    = ["data", "analytics", "database", "cloud", "platform", "api", "devops",
               "kubernetes", "microservice", "saas", "software", "engineer", "developer",
               "bigquery", "kafka", "spark", "hadoop", "lakehouse", "warehouse"]
_LOW_ICP    = ["consulting", "media", "marketing", "pr ", "event", "telecom",
               "insurance", "retail", "manufacturing", "energy", "government"]


def _icp_score(name: str) -> int:
    n = name.lower()
    if any(k in n for k in _HIGH_ICP):
        return 8
    if any(k in n for k in _MED_ICP):
        return 7
    if any(k in n for k in _LOW_ICP):
        return 4
    return 5


# ── Page fetching ──────────────────────────────────────────────────────────

def _fetch_with_playwright(url: str) -> str:
    """Open URL in headless Chromium, wait for network idle, return full HTML."""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=30_000)
            # Extra wait for lazy-loaded content
            page.wait_for_timeout(3_000)
            html = page.content()
        except PWTimeout:
            # networkidle timed out — grab whatever loaded
            html = page.content()
        finally:
            browser.close()
    return html


def _fetch_with_requests(url: str) -> str:
    import requests
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.text


def _fetch_html(url: str) -> tuple[str, str]:
    """
    Returns (html, method_used).
    Tries Playwright first; falls back to requests.
    """
    try:
        html = _fetch_with_playwright(url)
        return html, "playwright"
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("Playwright failed (%s), falling back to requests", exc)

    html = _fetch_with_requests(url)
    return html, "requests"


# ── Subpage discovery ──────────────────────────────────────────────────────

_SUBPAGE_PAT = re.compile(
    r"(exhibitor|sponsor|partner|speaker|exposant|partenaire|interven|keynote)",
    re.I,
)


def _discover_subpages(html: str, base_url: str) -> list[str]:
    """
    Find internal links whose href or text looks like a sponsor/exhibitor/speaker
    listing page.  Returns up to 5 unique absolute URLs on the same host.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(base_url).netloc
    seen: set[str] = set()
    results: list[str] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        txt  = a.get_text(strip=True)
        if not _SUBPAGE_PAT.search(href) and not _SUBPAGE_PAT.search(txt):
            continue
        abs_url = urljoin(base_url, href)
        parsed  = urlparse(abs_url)
        # Same host only, no anchors/query-only
        if parsed.netloc != base_host:
            continue
        if not parsed.path or parsed.path == urlparse(base_url).path:
            continue
        key = parsed.scheme + "://" + parsed.netloc + parsed.path
        if key not in seen and key != base_url.rstrip("/"):
            seen.add(key)
            results.append(abs_url)
        if len(results) >= 6:
            break

    return results


# ── Company extraction ─────────────────────────────────────────────────────

_SPONSOR_CLASSES = re.compile(
    r"(sponsor|partner|exhibitor|supporter|gold|silver|platinum|bronze|media.partner"
    r"|exposant|partenaire)",
    re.I,
)
_SPEAKER_CLASSES = re.compile(
    r"(speaker|keynote|presenter|panelist|facilitator|intervenant)",
    re.I,
)

# Generic/junk phrases that appear as img alt text on many event sites
_JUNK_PATTERNS = re.compile(
    r"(document\s+image|image\s+du\s+produit|logo\s+de\s+la|carousel|cover\s+image"
    r"|organisation\s+logo|powered\s+by|opt.out\s+icon|cookie|privacy|onetrust"
    r"|placeholder|loading|spinner|arrow|button|icon|banner|background|hero"
    r"|decoration|divider|separator"
    r"|speaker\s+image|photo\s+de|image\s+de"           # speaker photo alts
    r"|voir\s+tous|call\s+for\s+speaker|site\s+web"     # French nav phrases
    r"|'s\s+speaker|'s\s+image|'s\s+photo)",            # "Name's Speaker Image"
    re.I,
)

_SKIP_WORDS = {
    "", "and", "the", "in", "of", "at", "for", "to", "by", "with",
    "logo", "image", "icon", "photo", "pic", "picture",
    "click here", "learn more", "visit", "website",
    "register", "sign up", "buy ticket", "book now",
}
_MIN_LEN, _MAX_LEN = 2, 80


def _is_valid_name(text: str) -> bool:
    t = text.strip()
    if not (_MIN_LEN < len(t) < _MAX_LEN):
        return False
    if t.lower() in _SKIP_WORDS:
        return False
    # Skip junk generic phrases
    if _JUNK_PATTERNS.search(t):
        return False
    # Skip if it looks like a URL or path
    if t.startswith(("http", "/", "www.")):
        return False
    # Skip if mostly numbers/punctuation
    alpha = sum(1 for c in t if c.isalpha())
    if alpha < 2:
        return False
    # Skip person names (e.g. "Adrien CHENAILLER's Speaker Image" — ASCII or Unicode apostrophe)
    if re.search(r"[\u2019']\s*s\s+\w", t):
        return False
    # Skip anything that ends with "Image", "Photo", "Picture" (speaker headshots)
    if re.search(r"\b(image|photo|picture|portrait)\s*$", t, re.I):
        return False
    return True


def _extract_companies(html: str, page_url: str, default_type: str = "sponsor") -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    event_host = urlparse(page_url).netloc
    found: dict[str, dict] = {}   # name_lower → record

    def add(name: str, ctype: str, website: str | None = None):
        name = name.strip()
        if not _is_valid_name(name):
            return
        key = name.lower()
        if key not in found:
            found[key] = {"name": name, "type": ctype, "website": website}

    # ── Strategy 1: JSON-LD structured data ──────────────────────────────
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json
            data = json.loads(script.string or "")
            for s in (data if isinstance(data, list) else [data]):
                for sponsor in s.get("sponsor", []):
                    if isinstance(sponsor, dict):
                        add(sponsor.get("name", ""), "sponsor",
                            sponsor.get("url") or sponsor.get("sameAs"))
                for performer in s.get("performer", []):
                    if isinstance(performer, dict):
                        org = performer.get("memberOf", {})
                        add(org.get("name", ""), "speaker")
        except Exception:
            pass

    # ── Strategy 2: Sections with sponsor/exhibitor class names ──────────
    for el in soup.find_all(True):
        classes = " ".join(el.get("class", []))
        if not _SPONSOR_CLASSES.search(classes):
            continue
        # Logos: img alt text
        for img in el.find_all("img"):
            add(img.get("alt", ""), default_type)
        # Links to external company sites
        for a in el.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and urlparse(href).netloc != event_host:
                txt = a.get_text(" ", strip=True)
                img = a.find("img")
                name = txt or (img.get("alt", "") if img else "")
                add(name, default_type, href)

    # ── Strategy 3: Speaker sections ──────────────────────────────────────
    for el in soup.find_all(True):
        classes = " ".join(el.get("class", []))
        if not _SPEAKER_CLASSES.search(classes):
            continue
        for sub in el.find_all(True):
            sub_classes = " ".join(sub.get("class", [])).lower()
            if any(k in sub_classes for k in ("company", "org", "employer", "affiliation", "title")):
                add(sub.get_text(" ", strip=True), "speaker")

    # ── Strategy 4: All img alt texts in logo-grid sections ───────────────
    for section in soup.find_all(["section", "div", "article", "aside", "ul"]):
        imgs = section.find_all("img", alt=True)
        # Only dense logo grids (>= 4 images in same container)
        if len(imgs) >= 4:
            for img in imgs:
                alt = img.get("alt", "").strip()
                if _is_valid_name(alt) and alt[0].isupper():
                    add(alt, default_type)

    # ── Strategy 5: Headings inside named sections ─────────────────────
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5"]):
        txt = heading.get_text(strip=True).lower()
        if any(k in txt for k in ("sponsor", "partner", "exhibitor", "supporter",
                                   "exposant", "partenaire")):
            for sib in heading.find_next_siblings(["p", "li", "a", "div", "span"]):
                t = sib.get_text(" ", strip=True)
                if len(t) < 60:
                    add(t, default_type)

    return list(found.values())


# ── Main entry point ───────────────────────────────────────────────────────

def scrape_event(event_url: str, event_name: str | None = None) -> dict:
    """
    Scrape an event page for sponsors, speakers, and exhibitors.
    Automatically discovers and follows subpages (exhibitors, sponsors, speakers).
    Save to TiDB.  Returns summary dict.
    """
    logger.info("Direct scrape: %s", event_url)
    all_companies: dict[str, dict] = {}  # name_lower → record
    method = "playwright"

    # ── Step 1: Fetch homepage ─────────────────────────────────────────────
    try:
        home_html, method = _fetch_html(event_url)
        logger.info("Fetched homepage %d bytes via %s", len(home_html), method)
    except Exception as exc:
        msg = f"Fetch failed: {exc}"
        logger.error(msg)
        return {"saved": 0, "found": 0, "method": "error", "error": msg, "companies": []}

    # ── Step 2: Discover subpages ──────────────────────────────────────────
    subpages = _discover_subpages(home_html, event_url)
    logger.info("Discovered %d subpages: %s", len(subpages), subpages)

    # ── Step 3: Scrape homepage + each subpage ─────────────────────────────
    pages_to_scrape: list[tuple[str, str]] = [(event_url, "sponsor")]

    for url in subpages:
        path_lower = urlparse(url).path.lower()
        if any(k in path_lower for k in ("speaker", "intervenant", "keynote")):
            ctype = "speaker"
        elif any(k in path_lower for k in ("exhibitor", "exposant")):
            ctype = "exhibitor"
        else:
            ctype = "sponsor"
        pages_to_scrape.append((url, ctype))

    for page_url, default_type in pages_to_scrape:
        if page_url != event_url:
            try:
                html, _ = _fetch_html(page_url)
                logger.info("Scraped subpage %s (%d bytes)", page_url, len(html))
            except Exception as exc:
                logger.warning("Subpage fetch failed %s: %s", page_url, exc)
                continue
        else:
            html = home_html

        try:
            companies = _extract_companies(html, page_url, default_type)
            logger.info("Extracted %d companies from %s", len(companies), page_url)
            for c in companies:
                key = c["name"].lower()
                if key not in all_companies:
                    all_companies[key] = c
        except Exception as exc:
            logger.warning("Extraction failed for %s: %s", page_url, exc)

    companies = list(all_companies.values())
    logger.info("Total unique companies extracted: %d", len(companies))

    if not companies:
        return {
            "saved": 0,
            "found": 0,
            "method": method,
            "warning": (
                "No companies found. "
                "The page may require login or a different scraping strategy."
            ),
            "companies": [],
        }

    # ── Step 4: Save to TiDB ───────────────────────────────────────────────
    saved = 0
    errors = []
    for c in companies:
        try:
            r = db_companies.save_company({
                "event_url":    event_url,
                "event_name":   event_name,
                "company_name": c["name"],
                "company_type": c["type"],
                "website_url":  c.get("website"),
                "icp_score":    _icp_score(c["name"]),
                "icp_notes":    f"Auto-scraped via {method} (no AI enrichment yet)",
            })
            if r.get("success"):
                saved += 1
        except Exception as exc:
            errors.append(str(exc))

    logger.info("Saved %d / %d companies", saved, len(companies))

    return {
        "saved":     saved,
        "found":     len(companies),
        "method":    method,
        "companies": [c["name"] for c in companies],
        "errors":    errors,
    }
