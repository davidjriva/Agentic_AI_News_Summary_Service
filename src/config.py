import os
from dotenv import load_dotenv

load_dotenv()

FEED_URLS = [
    "https://arxiv.org/rss/cs.AI",
    "https://techcrunch.com/tag/artificial-intelligence/feed",
    "https://venturebeat.com/category/ai/feed",
    "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
    "https://www.wired.com/tag/artificial-intelligence/feed/rss",
    "https://www.anthropic.com/news/rss.xml",
    "https://news.google.com/rss/search?q=agentic+AI",
    "https://www.reddit.com/r/MachineLearning/.rss",
    "https://hn.algolia.com/api/v1/search?tags=story&query=agentic+AI",  # JSON, not RSS
]

# The last URL (HN Algolia) is a JSON API, not an RSS feed.
# The fetcher must handle it separately.
HN_ALGOLIA_URL = "https://hn.algolia.com/api/v1/search?tags=story&query=agentic+AI"

_recipients_env = os.getenv("EMAIL_RECIPIENTS", "")
RECIPIENTS: list[str] = [r.strip() for r in _recipients_env.split(",") if r.strip()]

TOP_N: int = 15
MAX_ARTICLES_PER_SOURCE: int = 10
CLAUDE_MODEL: str = "claude-haiku-4-5-20251001"
LOOKBACK_HOURS: int = 12
