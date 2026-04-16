# Agentic AI News Summary Service

An automated newsletter service that fetches AI and technology news from RSS feeds, summarizes each article using Claude, and delivers a polished NYT/editorial-inspired HTML email newsletter.

## Features

- **AI-powered summarization** — Uses Claude (Haiku) with prompt caching to produce concise, NYT-style 2–3 sentence summaries
- **Multi-source RSS aggregation** — Pulls from TechCrunch, The Verge, VentureBeat, Wired, Ars Technica (configurable)
- **Topic filtering** — Focuses on AI, machine learning, LLMs, robotics, and related keywords
- **Editorial newsletter design** — Georgia serif headlines, typographic hierarchy, NYT red accents, email-safe HTML
- **Automated delivery** — SMTP email delivery with plain-text fallback; daily scheduling via `schedule`
- **HTML preview** — Saves rendered newsletters to `output/` even without email configured

## Project Structure

```
├── src/
│   ├── models.py               # Article, NewsletterSection, Newsletter dataclasses
│   ├── news_fetcher.py         # RSS feed fetching and topic filtering
│   ├── summarizer.py           # Claude API summarization with prompt caching
│   ├── newsletter_generator.py # Section categorization + Jinja2 rendering
│   └── email_sender.py         # SMTP delivery
├── templates/
│   └── newsletter.html         # NYT-inspired Jinja2 HTML template
├── output/                     # Generated newsletters (gitignored)
├── data/                       # Issue counter state (gitignored)
├── config.py                   # Configuration with defaults
├── main.py                     # Pipeline orchestrator and scheduler
├── requirements.txt
└── .env.example
```

## Setup

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```env
ANTHROPIC_API_KEY=sk-ant-...
```

For email delivery, also set SMTP credentials and recipients.

### 3. Run

```bash
python main.py
```

The pipeline will:
1. Fetch articles from configured RSS feeds
2. Filter for AI/tech relevance
3. Summarize with Claude
4. Render the newsletter HTML
5. Save to `output/newsletter_YYYY-MM-DD.html`
6. Send via email (if SMTP is configured)

### Scheduled daily delivery

```python
from main import schedule_daily_run
schedule_daily_run()  # runs at SEND_TIME from .env (default 08:00)
```

## Configuration

All settings are controlled via environment variables (see `.env.example`).

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | **Required.** Your Anthropic API key |
| `NEWS_SOURCES` | 5 default feeds | Comma-separated RSS feed URLs |
| `NEWSLETTER_TITLE` | `AI & Tech Weekly Briefing` | Newsletter masthead title |
| `NEWSLETTER_TAGLINE` | `Your curated digest…` | Tagline below the title |
| `MAX_ARTICLES_PER_SECTION` | `5` | Articles per standard section |
| `SEND_TIME` | `08:00` | Daily send time (24h, local timezone) |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP port (STARTTLS) |
| `SMTP_USER` | — | SMTP username |
| `SMTP_PASSWORD` | — | SMTP password / app password |
| `SMTP_FROM` | _(SMTP_USER)_ | From address |
| `RECIPIENT_EMAILS` | — | Comma-separated recipient addresses |

## Newsletter Design

The HTML template (`templates/newsletter.html`) implements NYT/editorial conventions:

- **Masthead** — Title in Georgia serif, tagline, issue number, date
- **Top Stories** — Featured section on warm off-white background, large 24px headlines
- **Standard sections** — AI & Machine Learning, Industry & Business with red section labels
- **In Brief** — Compact digest for shorter items
- **Footer** — Unsubscribe link, copyright

The layout uses email-safe `<table>` structure with inline CSS for compatibility with Outlook and other email clients. Max-width is 680px with an 8px spacing scale.

## License

MIT
