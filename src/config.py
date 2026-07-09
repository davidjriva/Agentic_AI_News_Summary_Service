import os
from dotenv import load_dotenv

load_dotenv()

FEED_URLS = [
    "https://arxiv.org/rss/cs.AI",
    "https://arxiv.org/rss/cs.LG",                                       # cs.LG = Machine Learning
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",  # AI section (verified A1: 200, 10 entries)
    "https://www.wired.com/feed/rss",                                      # full Wired feed (AI tag URL returns 0 entries)
    "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "https://openai.com/blog/rss.xml",                                    # replaces stale VentureBeat (verified A1: 307→200, 939 entries)
    "https://huggingface.co/blog/feed.xml",                               # replaces stale VentureBeat (verified A1: 200, 764 entries)
    "https://news.google.com/rss/search?q=agentic+AI",
    "https://hn.algolia.com/api/v1/search?tags=story&query=AI+agent",    # JSON, not RSS ("agentic AI" query returned months-old results)
    "https://www.langchain.com/blog",                                    # HTML scrape; no RSS feed available
]

# Non-RSS sources — the fetcher dispatches these to dedicated processors.
HN_ALGOLIA_URL = "https://hn.algolia.com/api/v1/search?tags=story&query=AI+agent"
LANGCHAIN_BLOG_URL = "https://www.langchain.com/blog"

_recipients_env = os.getenv("EMAIL_RECIPIENTS", "")
RECIPIENTS: list[str] = [r.strip() for r in _recipients_env.split(",") if r.strip()]

# Base URL of the portfolio site that hosts the subscribe/confirm/unsubscribe
# routes. Used to build per-subscriber unsubscribe links in the newsletter.
PORTFOLIO_BASE_URL: str = os.getenv("PORTFOLIO_BASE_URL", "").rstrip("/")

TOP_N: int = 10
MAX_ARTICLES_PER_SOURCE: int = 10
MAX_PER_NEWSLETTER_SOURCE: int = 3
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
LOOKBACK_HOURS: int = 72
SCORE_CACHE_TTL_DAYS: int = LOOKBACK_HOURS // 24  # 3 — aligned with lookback window

# ---------------------------------------------------------------------------
# LLM provider selection
# ---------------------------------------------------------------------------
# LLM_PROVIDER: "anthropic" (default) | "local"
#   "anthropic" — uses the Anthropic SDK (ANTHROPIC_API_KEY required)
#   "local"     — uses a local llama.cpp server via its OpenAI-compatible API
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "anthropic")
LOCAL_LLM_URL: str = os.getenv("LOCAL_LLM_URL", "http://localhost:8080")
LOCAL_LLM_MODEL: str = os.getenv("LOCAL_LLM_MODEL", "local")
RELEVANCE_THRESHOLD: int = int(os.getenv("RELEVANCE_THRESHOLD", "6"))
PROCESSOR_MAX_RETRIES: int = int(os.getenv("PROCESSOR_MAX_RETRIES", "2"))
PROCESSOR_RETRY_DELAY: float = float(os.getenv("PROCESSOR_RETRY_DELAY", "2.0"))

# ---------------------------------------------------------------------------
# Database (Supabase Postgres) — secrets come from .env
# ---------------------------------------------------------------------------
# The SQLAlchemy URL is assembled in db.py via URL.create() so the password
# (which may contain URL-reserved characters) is escaped correctly. Tests can
# override the whole URL with DATABASE_URL.
DATABASE_URL: str | None = os.getenv("DATABASE_URL")  # full override (e.g. tests)
SUPABASE_DB_HOST: str = os.getenv("SUPABASE_DB_HOST", "")
SUPABASE_DB_PORT: int = int(os.getenv("SUPABASE_DB_PORT", "5432"))
SUPABASE_DB_USER: str = os.getenv("SUPABASE_DB_USER", "postgres")
SUPABASE_DB_NAME: str = os.getenv("SUPABASE_DB_NAME", "postgres")
DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")

# ---------------------------------------------------------------------------
# Article dict schema — locked contract shared by all pipeline stages
# ---------------------------------------------------------------------------
# Produced by fetcher.py:
#   title:               str   — article headline
#   url:                 str   — canonical URL; primary dedup key
#   description:         str   — raw snippet from feed (may be empty)
#   author:              str   — empty string if unknown
#   publication:         str   — human-readable feed source name (domain)
#   published_at:        datetime — UTC-aware datetime object
#
# Added by processor.py:
#   summary:             str   — 2-3 sentence summary from Claude
#   impact_score:        int   — 1–10; Claude-assigned impact rating
#   authenticity_score:  int   — 1–10; Claude-assigned authenticity rating
#   relevance_score:     int   — 1–10; LLM-assigned relevance to agentic AI/ML
#   impact_reason:       str   — one-line rationale for impact_score
#   authenticity_reason: str   — one-line rationale for authenticity_score
#   relevance_reason:    str   — one-line rationale for relevance_score
#
# Added by ranker.py:
#   rank_score:          float — (impact_score * 0.5) + (relevance_score * 0.3) + (authenticity_score * 0.2)
#   rank:                int   — 1-based position after descending sort
# ---------------------------------------------------------------------------
