from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Article:
    id: str
    title: str
    url: str
    source: str
    published_at: datetime
    raw_content: str
    summary: str = ""
    category: str = "technology"
    image_url: Optional[str] = None
    author: Optional[str] = None
    read_time_minutes: int = 3

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published_at": self.published_at.isoformat(),
            "published_at_display": self.published_at.strftime("%B %-d, %Y"),
            "raw_content": self.raw_content,
            "summary": self.summary,
            "category": self.category,
            "image_url": self.image_url,
            "author": self.author,
            "read_time_minutes": self.read_time_minutes,
        }


@dataclass
class NewsletterSection:
    """
    section_type controls layout hints for the template renderer:
      - "featured"  → hero card, large image, full summary
      - "standard"  → regular article cards with summary
      - "brief"     → compact list, headline + one-liner only
    """
    title: str
    articles: List[Article]
    section_type: str = "standard"


@dataclass
class Newsletter:
    title: str
    tagline: str
    date: datetime
    sections: List[NewsletterSection]
    issue_number: int = 1

    @property
    def article_count(self) -> int:
        return sum(len(s.articles) for s in self.sections)

    @property
    def date_display(self) -> str:
        return self.date.strftime("%B %-d, %Y")
