import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import src.config as _cfg
from src.db import get_session
from src.models import ArticleScore
from src.processor import triage_articles, score_articles, summarize_articles


def make_article(**overrides) -> dict:
    base = {
        "title": "Claude 3 Agents Achieve New Milestones",
        "url": "https://example.com/article",
        "description": "Researchers at Anthropic have published findings showing that agentic AI systems can now autonomously complete complex multi-step tasks with high reliability.",
        "author": "Jane Smith",
        "publication": "AI Research Weekly",
        "published_at": datetime(2025, 1, 15, 10, 0, 0),
    }
    base.update(overrides)
    return base


def _make_mock_client(response_text: str):
    """Build a mock anthropic.Anthropic() client whose messages.create returns response_text."""
    mock_content_block = MagicMock()
    mock_content_block.text = response_text

    mock_response = MagicMock()
    mock_response.content = [mock_content_block]

    mock_messages = MagicMock()
    mock_messages.create.return_value = mock_response

    mock_client = MagicMock()
    mock_client.messages = mock_messages

    return mock_client


def _make_local_mock_response(content: str) -> MagicMock:
    """Build a mock requests.Response for a llama.cpp /v1/chat/completions call."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    mock_resp.raise_for_status.return_value = None
    return mock_resp


@contextmanager
def _fake_session():
    """A stand-in session whose calls are no-ops (for DB-free logic tests)."""
    yield MagicMock()


@pytest.fixture(autouse=True)
def _isolate_db(request):
    """Keep pure-logic processor tests off the database.

    Reads return an empty score cache and writes become no-ops, so these tests
    never touch a real Postgres. Tests that exercise real caching request the
    ``db`` fixture and opt out of this isolation.
    """
    if "db" in request.fixturenames:
        yield
        return
    with patch("src.processor._load_score_cache", return_value={}), \
         patch("src.processor.get_session", _fake_session):
        yield


# ---------------------------------------------------------------------------
# Test: call helpers (_build_user_content, _call_local, _call_anthropic)
# ---------------------------------------------------------------------------

class TestCallHelpers:
    def test_build_user_content_default_truncates_at_500(self):
        from src.processor import _build_user_content
        article = make_article(description="x" * 2000)
        content = _build_user_content(article)
        # default 500-char cap on description
        assert "x" * 500 in content
        assert "x" * 501 not in content

    def test_build_user_content_respects_max_desc(self):
        from src.processor import _build_user_content, SCORE_DESC_CHARS
        assert SCORE_DESC_CHARS == 1900
        article = make_article(description="y" * 2500)
        content = _build_user_content(article, SCORE_DESC_CHARS)
        assert "y" * 1900 in content
        assert "y" * 1901 not in content

    def test_stage_caps_defined(self):
        from src.processor import TRIAGE_DESC_CHARS, SCORE_DESC_CHARS
        assert TRIAGE_DESC_CHARS == 500
        assert SCORE_DESC_CHARS == 1900

    def test_local_call_includes_response_format_schema(self):
        from src.processor import _call_local, _TRIAGE_SCHEMA
        mock_resp = _make_local_mock_response('{"relevance_score": 8, "relevance_reason": "r"}')
        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            _call_local("SYS", "USER", 128, _TRIAGE_SCHEMA)
        payload = mock_post.call_args[1]["json"]
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["schema"] == _TRIAGE_SCHEMA
        assert payload["max_tokens"] == 128
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}

    def test_anthropic_call_passes_system_and_max_tokens(self):
        from src.processor import _call_anthropic
        mock_client = _make_mock_client('{"relevance_score": 8, "relevance_reason": "r"}')
        _call_anthropic(mock_client, "SYS PROMPT", "USER", 128)
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["max_tokens"] == 128
        assert kwargs["system"][0]["text"] == "SYS PROMPT"
        assert kwargs["system"][0]["cache_control"]["type"] == "ephemeral"


# ---------------------------------------------------------------------------
# Test: triage_articles
# ---------------------------------------------------------------------------

TRIAGE_RESPONSE = {"relevance_score": 9, "relevance_reason": "Directly about agentic AI systems."}


class TestTriageArticles:
    def test_relevance_merged_into_article(self):
        article = make_article()
        mock_client = _make_mock_client(json.dumps(TRIAGE_RESPONSE))
        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = triage_articles([article])
        assert results[0]["relevance_score"] == 9
        assert results[0]["relevance_reason"] == TRIAGE_RESPONSE["relevance_reason"]
        assert results[0]["title"] == article["title"]

    def test_local_provider_used_for_triage(self):
        article = make_article()
        mock_resp = _make_local_mock_response(json.dumps(TRIAGE_RESPONSE))
        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            results = triage_articles([article])
        assert results[0]["relevance_score"] == 9
        assert mock_post.call_args[1]["json"]["max_tokens"] == 128

    def test_failure_dead_letters_and_excludes(self):
        article = make_article()
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("api down")
        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.processor._write_failed") as mock_dlq:
            results = triage_articles([article])
        assert len(results) == 0
        mock_dlq.assert_called_once()

    def test_processes_multiple_articles(self):
        articles = [make_article(url=f"https://example.com/{i}") for i in range(3)]
        mock_client = _make_mock_client(json.dumps(TRIAGE_RESPONSE))
        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = triage_articles(articles)
        assert len(results) == 3
        for result in results:
            assert "relevance_score" in result


class TestTriageRetryAndDeadLetter:
    """Migrated from the old process_articles retry/dead-letter tests (Task 4)."""

    def test_retries_on_api_failure_before_giving_up(self):
        article = make_article()
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("transient error")

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 2), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.processor._write_failed") as mock_dlq:
            results = triage_articles([article])

        assert mock_client.messages.create.call_count == 3  # 1 attempt + 2 retries
        mock_dlq.assert_called_once()
        assert len(results) == 0

    def test_succeeds_on_second_attempt(self):
        article = make_article()
        call_count = {"n": 0}

        def side_effect(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("transient")
            mock_msg = MagicMock()
            mock_msg.content = [MagicMock(text=json.dumps(TRIAGE_RESPONSE))]
            return mock_msg

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = side_effect

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 2), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = triage_articles([article])

        assert len(results) == 1
        assert results[0]["relevance_score"] == TRIAGE_RESPONSE["relevance_score"]

    def test_dead_letter_receives_article_url_and_reason(self):
        article = make_article()
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("api down")

        written = {}

        def capture_dlq(article, reason, run_id):
            written["url"] = article["url"]
            written["reason"] = reason

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.processor._write_failed", side_effect=capture_dlq):
            triage_articles([article])

        assert written["url"] == article["url"]
        assert "api down" in written["reason"]


class TestTriageTqdmBar:
    """triage_articles should tick tqdm once per article."""

    def test_tqdm_called_with_correct_total(self):
        articles = [
            make_article(url="https://example.com/1"),
            make_article(url="https://example.com/2"),
        ]

        bar_mock = MagicMock()
        bar_mock.__enter__ = MagicMock(return_value=bar_mock)
        bar_mock.__exit__ = MagicMock(return_value=False)
        tqdm_cls = MagicMock(return_value=bar_mock)

        with (
            patch.object(_cfg, "LLM_PROVIDER", "local"),
            patch("requests.post", return_value=_make_local_mock_response(json.dumps(TRIAGE_RESPONSE))),
            patch("src.processor.tqdm", tqdm_cls),
        ):
            results = triage_articles(articles)

        tqdm_cls.assert_called_once_with(total=2, desc="Triage", unit="art", leave=False)
        assert bar_mock.update.call_count == 2
        assert len(results) == 2


class TestTriageCache:
    def test_cache_hit_skips_llm_when_version_matches(self, db):
        from src.processor import TRIAGE_VERSION
        url = "https://example.com/triage-cached"
        with get_session() as session:
            session.add(ArticleScore(
                url=url, relevance_score=9, relevance_reason="cached",
                triage_version=TRIAGE_VERSION,
            ))
        with patch("anthropic.Anthropic") as mock_cls, \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            results = triage_articles([make_article(url=url)])
        mock_cls.return_value.messages.create.assert_not_called()
        assert results[0]["relevance_score"] == 9

    def test_stale_version_treated_as_miss(self, db):
        url = "https://example.com/triage-stale"
        with get_session() as session:
            session.add(ArticleScore(
                url=url, relevance_score=3, relevance_reason="old",
                triage_version="OLDVERSION00",
            ))
        mock_client = _make_mock_client(json.dumps(TRIAGE_RESPONSE))
        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            results = triage_articles([make_article(url=url)])
        mock_client.messages.create.assert_called_once()
        assert results[0]["relevance_score"] == 9

    def test_cache_miss_writes_triage_version(self, db):
        url = "https://example.com/triage-write"
        from src.processor import TRIAGE_VERSION
        mock_client = _make_mock_client(json.dumps(TRIAGE_RESPONSE))
        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            triage_articles([make_article(url=url)])
        with get_session() as session:
            row = session.get(ArticleScore, url)
        assert row.relevance_score == 9
        assert row.triage_version == TRIAGE_VERSION

    def test_expired_cache_entry_treated_as_miss(self, db):
        from src.processor import TRIAGE_VERSION
        url = "https://example.com/triage-expired"
        with get_session() as session:
            session.add(ArticleScore(
                url=url, relevance_score=3, relevance_reason="stale but valid version",
                triage_version=TRIAGE_VERSION,
                cached_at=datetime.now(timezone.utc) - timedelta(days=4),
            ))
        mock_client = _make_mock_client(json.dumps(TRIAGE_RESPONSE))
        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            results = triage_articles([make_article(url=url)])
        mock_client.messages.create.assert_called_once()
        assert results[0]["relevance_score"] == 9


class TestRubricPrompts:
    def test_triage_prompt_is_relevance_only(self):
        from src.processor import TRIAGE_SYSTEM_PROMPT
        assert "relevance_score" in TRIAGE_SYSTEM_PROMPT
        assert "impact_score" not in TRIAGE_SYSTEM_PROMPT

    def test_scoring_prompt_has_anchored_impact_bands(self):
        from src.processor import SCORING_SYSTEM_PROMPT
        assert "impact_score" in SCORING_SYSTEM_PROMPT
        assert "authenticity_score" in SCORING_SYSTEM_PROMPT
        # Calibration guard against 6-8 clustering
        assert "4-6" in SCORING_SYSTEM_PROMPT

    def test_version_hashes_are_short_hex(self):
        from src.processor import TRIAGE_VERSION, SCORE_VERSION
        assert len(TRIAGE_VERSION) == 12
        assert len(SCORE_VERSION) == 12
        assert TRIAGE_VERSION != SCORE_VERSION

    def test_version_hash_tracks_prompt(self):
        from src.processor import _prompt_hash, TRIAGE_SYSTEM_PROMPT, TRIAGE_VERSION
        assert _prompt_hash(TRIAGE_SYSTEM_PROMPT) == TRIAGE_VERSION

    def test_relevance_score_in_system_prompt(self):
        from src.processor import TRIAGE_SYSTEM_PROMPT
        assert "relevance_score" in TRIAGE_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Test: score_articles
# ---------------------------------------------------------------------------

SCORE_RESPONSE = {
    "impact_score": 8, "authenticity_score": 7,
    "impact_reason": "Field-moving research.", "authenticity_reason": "Named lab source.",
}


def _triaged_article(url="https://example.com/s", relevance=9) -> dict:
    return {**make_article(url=url), "relevance_score": relevance, "relevance_reason": "on topic"}


class TestScoreArticles:
    def test_impact_and_auth_merged_preserving_triage(self):
        article = _triaged_article()
        mock_client = _make_mock_client(json.dumps(SCORE_RESPONSE))
        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = score_articles([article])
        assert results[0]["impact_score"] == 8
        assert results[0]["authenticity_score"] == 7
        assert results[0]["relevance_score"] == 9  # triage field preserved

    def test_local_scoring_uses_512_max_tokens(self):
        article = _triaged_article()
        mock_resp = _make_local_mock_response(json.dumps(SCORE_RESPONSE))
        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            score_articles([article])
        assert mock_post.call_args[1]["json"]["max_tokens"] == 512

    def test_failure_dead_letters_and_excludes(self):
        article = _triaged_article()
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("down")
        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.processor._write_failed") as mock_dlq:
            results = score_articles([article])
        assert len(results) == 0
        mock_dlq.assert_called_once()


class TestScoreCache:
    def test_cache_hit_skips_llm_when_version_matches(self, db):
        from src.processor import SCORE_VERSION
        url = "https://example.com/score-cached"
        with get_session() as session:
            session.add(ArticleScore(
                url=url, impact_score=8, authenticity_score=7,
                impact_reason="i", authenticity_reason="a", score_version=SCORE_VERSION,
            ))
        with patch("anthropic.Anthropic") as mock_cls, \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            results = score_articles([_triaged_article(url=url)])
        mock_cls.return_value.messages.create.assert_not_called()
        assert results[0]["impact_score"] == 8

    def test_stale_score_version_is_miss(self, db):
        url = "https://example.com/score-stale"
        with get_session() as session:
            session.add(ArticleScore(
                url=url, impact_score=2, authenticity_score=2,
                impact_reason="old", authenticity_reason="old", score_version="OLD000000000",
            ))
        mock_client = _make_mock_client(json.dumps(SCORE_RESPONSE))
        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            results = score_articles([_triaged_article(url=url)])
        mock_client.messages.create.assert_called_once()
        assert results[0]["impact_score"] == 8

    def test_cache_miss_writes_score_version(self, db):
        from src.processor import SCORE_VERSION
        url = "https://example.com/score-write"
        mock_client = _make_mock_client(json.dumps(SCORE_RESPONSE))
        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            score_articles([_triaged_article(url=url)])
        with get_session() as session:
            row = session.get(ArticleScore, url)
        assert row.impact_score == 8
        assert row.score_version == SCORE_VERSION


class TestCacheVersionColumns:
    def test_article_score_has_version_columns(self, db):
        from src.models import ArticleScore
        url = "https://example.com/versioned"
        with get_session() as session:
            session.add(ArticleScore(
                url=url, impact_score=8, authenticity_score=7, relevance_score=9,
                impact_reason="r", authenticity_reason="r", relevance_reason="r",
                triage_version="abc123", score_version="def456",
            ))
        with get_session() as session:
            row = session.get(ArticleScore, url)
        assert row.triage_version == "abc123"
        assert row.score_version == "def456"


# ---------------------------------------------------------------------------
# Test: summarize_articles
# ---------------------------------------------------------------------------

def _make_processed_article(url="http://example.com") -> dict:
    return {
        "url": url,
        "title": "Test Article",
        "publication": "example.com",
        "author": "Test Author",
        "description": "Some description text.",
        "impact_score": 8,
        "authenticity_score": 7,
        "relevance_score": 9,
        "rank_score": 7.6,
    }


class TestSummarizeArticles:
    """summarize_articles replaces summary with a full paragraph; keeps original on failure."""

    def test_summary_replaced_on_success(self):
        article = _make_processed_article()
        long_summary = "This is a detailed paragraph covering who what when where and why it matters to the AI field."
        mock_client = _make_mock_client(json.dumps({"summary": long_summary}))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = summarize_articles([article])

        assert results[0]["summary"] == long_summary

    def test_other_fields_preserved(self):
        article = _make_processed_article()
        mock_client = _make_mock_client(json.dumps({"summary": "Detailed summary."}))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = summarize_articles([article])

        assert results[0]["impact_score"] == article["impact_score"]
        assert results[0]["rank_score"] == article["rank_score"]
        assert results[0]["url"] == article["url"]

    def test_falls_back_to_description_on_api_failure(self):
        article = _make_processed_article()
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = summarize_articles([article])

        assert len(results) == 1
        assert results[0]["summary"] == article["description"]

    def test_uses_plain_text_when_json_parse_fails(self):
        # Local LLMs often return prose instead of JSON — use it directly as summary
        article = _make_processed_article()
        mock_client = _make_mock_client("not valid json but good prose summary here")

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = summarize_articles([article])

        assert len(results) == 1
        assert results[0]["summary"] == "not valid json but good prose summary here"

    def test_falls_back_to_description_on_empty_response(self):
        article = _make_processed_article()
        mock_client = _make_mock_client("")

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = summarize_articles([article])

        assert len(results) == 1
        assert results[0]["summary"] == article["description"]

    def test_description_truncated_at_400_chars_on_failure(self):
        long_description = "word " * 200  # 1000 chars, no HTML/arXiv prefix
        article = {**_make_processed_article(), "description": long_description}
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("down")

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = summarize_articles([article])

        summary = results[0]["summary"]
        assert len(summary) <= 401  # 400 chars + ellipsis character
        assert summary.endswith("…")

    def test_arxiv_prefix_stripped_on_failure(self):
        arxiv_desc = "arXiv:2604.14176v1 Announce Type: new Abstract: This is the real abstract text."
        article = {**_make_processed_article(), "description": arxiv_desc}
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("down")

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = summarize_articles([article])

        assert results[0]["summary"] == "This is the real abstract text."

    def test_html_tags_stripped_on_failure(self):
        html_desc = '<a href="http://example.com">Click here</a> for the full story.'
        article = {**_make_processed_article(), "description": html_desc}
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("down")

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = summarize_articles([article])

        assert results[0]["summary"] == "Click here for the full story."

    def test_processes_all_articles(self):
        articles = [_make_processed_article(f"http://example.com/{i}") for i in range(3)]
        mock_client = _make_mock_client(json.dumps({"summary": "Full paragraph."}))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = summarize_articles(articles)

        assert len(results) == 3
        assert all(r["summary"] == "Full paragraph." for r in results)

    def test_failure_does_not_drop_article(self):
        articles = [_make_processed_article(f"http://example.com/{i}") for i in range(3)]
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("down")

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = summarize_articles(articles)

        assert len(results) == 3

    def test_local_provider_called_for_summaries(self):
        article = _make_processed_article()
        mock_resp = _make_local_mock_response(json.dumps({"summary": "Full paragraph."}))

        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            results = summarize_articles([article])

        assert results[0]["summary"] == "Full paragraph."
        payload = mock_post.call_args[1]["json"]
        assert payload["max_tokens"] == 512

    def test_summary_input_uses_full_abstract_not_500(self):
        article = {**_make_processed_article(), "description": "z" * 2000}
        mock_resp = _make_local_mock_response(json.dumps({"summary": "ok"}))
        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            summarize_articles([article])
        sent = mock_post.call_args[1]["json"]["messages"][1]["content"]
        assert "z" * 1900 in sent      # full abstract, not truncated at 500
        assert "z" * 1901 not in sent  # still capped at SUMMARY_DESC_CHARS
