import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

import src.config as _cfg
from src.db import get_session
from src.models import ArticleScore
from src.processor import process_articles, summarize_articles


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


VALID_CLAUDE_RESPONSE = {
    "impact_score": 8,
    "authenticity_score": 7,
    "impact_reason": "Significant advancement in agentic AI reliability with broad implications.",
    "authenticity_reason": "Published by named researcher at credible AI lab.",
    "relevance_score": 9,
    "relevance_reason": "Directly about agentic AI systems and autonomous task completion.",
}


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


class TestValidJsonResponse:
    """(a) Valid Claude JSON response is parsed and keys merged into the article dict."""

    def test_valid_json_keys_merged_into_article(self):
        article = make_article()
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([article])

        result = results[0]
        assert result["impact_score"] == VALID_CLAUDE_RESPONSE["impact_score"]
        assert result["authenticity_score"] == VALID_CLAUDE_RESPONSE["authenticity_score"]
        assert result["impact_reason"] == VALID_CLAUDE_RESPONSE["impact_reason"]
        assert result["authenticity_reason"] == VALID_CLAUDE_RESPONSE["authenticity_reason"]

    def test_original_article_keys_preserved(self):
        article = make_article()
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([article])

        result = results[0]
        assert result["title"] == article["title"]
        assert result["url"] == article["url"]
        assert result["description"] == article["description"]
        assert result["author"] == article["author"]
        assert result["publication"] == article["publication"]
        assert result["published_at"] == article["published_at"]

    def test_processes_multiple_articles(self):
        articles = [make_article(url=f"https://example.com/{i}") for i in range(3)]
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles(articles)

        assert len(results) == 3
        for result in results:
            assert "impact_score" in result

    def test_returns_list(self):
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([make_article()])

        assert isinstance(results, list)


class TestFallbackOnMalformedResponse:
    """(b) When Claude returns malformed/non-JSON or raises, article is excluded (no fallback scores)."""

    def test_malformed_json_excludes_article(self):
        article = make_article()
        mock_client = _make_mock_client("This is not valid JSON at all!")

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.processor._write_failed"):
            results = process_articles([article])

        assert len(results) == 0

    def test_api_exception_excludes_article(self):
        article = make_article()
        mock_messages = MagicMock()
        mock_messages.create.side_effect = Exception("API error")
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.processor._write_failed"):
            results = process_articles([article])

        assert len(results) == 0


class TestPromptCachingSystemMessage:
    """(c) The messages.create call includes a system message with cache_control type='ephemeral'."""

    def test_system_message_has_cache_control_ephemeral(self):
        article = make_article()
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            process_articles([article])

        call_kwargs = mock_client.messages.create.call_args
        # Support both positional and keyword argument styles
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
        system = kwargs.get("system") or (call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None)

        assert system is not None, "system parameter must be present in messages.create call"
        assert isinstance(system, list), "system must be a list of content blocks"
        assert len(system) > 0, "system must have at least one block"

        first_block = system[0]
        assert "cache_control" in first_block, "system block must have cache_control"
        assert first_block["cache_control"]["type"] == "ephemeral"

    def test_system_message_has_type_text(self):
        article = make_article()
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            process_articles([article])

        call_kwargs = mock_client.messages.create.call_args
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
        system = kwargs.get("system")

        assert system[0]["type"] == "text"

    def test_user_message_contains_article_fields(self):
        article = make_article()
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            process_articles([article])

        call_kwargs = mock_client.messages.create.call_args
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
        messages = kwargs.get("messages")

        assert messages is not None
        assert len(messages) > 0
        user_msg = messages[0]
        assert user_msg["role"] == "user"
        content = user_msg["content"]
        assert article["author"] in content
        assert article["publication"] in content
        assert article["title"] in content
        assert article["description"] in content

    def test_messages_create_called_once_per_article(self):
        articles = [make_article(url=f"https://example.com/{i}") for i in range(4)]
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            process_articles(articles)

        assert mock_client.messages.create.call_count == 4

    def test_model_parameter_passed_to_create(self):
        article = make_article()
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            process_articles([article])

        call_kwargs = mock_client.messages.create.call_args
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
        assert "model" in kwargs

    def test_max_tokens_parameter_passed_to_create(self):
        article = make_article()
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            process_articles([article])

        call_kwargs = mock_client.messages.create.call_args
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
        assert "max_tokens" in kwargs
        assert kwargs["max_tokens"] == 1024


def _make_local_mock_response(content: str) -> MagicMock:
    """Build a mock requests.Response for a llama.cpp /v1/chat/completions call."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    mock_resp.raise_for_status.return_value = None
    return mock_resp


class TestLocalLLMProvider:
    """Tests for LLM_PROVIDER='local' (llama.cpp OpenAI-compatible endpoint)."""

    def test_local_provider_returns_parsed_scores(self):
        article = make_article()
        mock_resp = _make_local_mock_response(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch("requests.post", return_value=mock_resp):
            results = process_articles([article])

        assert results[0]["impact_score"] == VALID_CLAUDE_RESPONSE["impact_score"]
        assert results[0]["authenticity_score"] == VALID_CLAUDE_RESPONSE["authenticity_score"]

    def test_local_provider_calls_correct_url(self):
        article = make_article()
        mock_resp = _make_local_mock_response(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch.object(_cfg, "LOCAL_LLM_URL", "http://localhost:8080"), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            process_articles([article])

        called_url = mock_post.call_args[0][0]
        assert called_url == "http://localhost:8080/v1/chat/completions"

    def test_local_provider_sends_system_and_user_messages(self):
        article = make_article()
        mock_resp = _make_local_mock_response(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            process_articles([article])

        payload = mock_post.call_args[1]["json"]
        messages = payload["messages"]
        roles = [m["role"] for m in messages]
        assert "system" in roles
        assert "user" in roles

    def test_local_provider_user_message_contains_article_fields(self):
        article = make_article()
        mock_resp = _make_local_mock_response(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            process_articles([article])

        payload = mock_post.call_args[1]["json"]
        user_msg = next(m for m in payload["messages"] if m["role"] == "user")
        assert article["title"] in user_msg["content"]
        assert article["author"] in user_msg["content"]
        assert article["publication"] in user_msg["content"]

    def test_local_provider_sends_model_name(self):
        article = make_article()
        mock_resp = _make_local_mock_response(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch.object(_cfg, "LOCAL_LLM_MODEL", "qwen2.5-7b"), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            process_articles([article])

        payload = mock_post.call_args[1]["json"]
        assert payload["model"] == "qwen2.5-7b"

    def test_local_provider_excludes_article_on_request_error(self):
        article = make_article()

        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("requests.post", side_effect=Exception("connection refused")), \
             patch("src.processor._write_failed"):
            results = process_articles([article])

        assert len(results) == 0

    def test_local_provider_excludes_article_on_malformed_json(self):
        article = make_article()
        mock_resp = _make_local_mock_response("not valid json")

        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("requests.post", return_value=mock_resp), \
             patch("src.processor._write_failed"):
            results = process_articles([article])

        assert len(results) == 0

    def test_local_provider_does_not_call_anthropic(self):
        article = make_article()
        mock_resp = _make_local_mock_response(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch("requests.post", return_value=mock_resp), \
             patch("anthropic.Anthropic") as mock_anthropic:
            process_articles([article])

        mock_anthropic.assert_not_called()


class TestArticleTqdmBar:
    """process_articles should tick tqdm once per article."""

    def test_tqdm_called_with_correct_total(self):
        articles = [
            make_article(url="https://example.com/1"),
            make_article(url="https://example.com/2"),
        ]

        bar_mock = MagicMock()
        bar_mock.__enter__ = MagicMock(return_value=bar_mock)
        bar_mock.__exit__ = MagicMock(return_value=False)
        tqdm_cls = MagicMock(return_value=bar_mock)

        good_response = json.dumps({
            "summary": "s", "impact_score": 7, "authenticity_score": 6,
            "relevance_score": 8, "impact_reason": "r", "authenticity_reason": "r",
            "relevance_reason": "r",
        })

        with (
            patch.object(_cfg, "LLM_PROVIDER", "local"),
            patch("requests.post", return_value=_make_local_mock_response(good_response)),
            patch("src.processor.tqdm", tqdm_cls),
        ):
            results = process_articles(articles)

        tqdm_cls.assert_called_once_with(total=2, desc="Articles", unit="art", leave=False)
        assert bar_mock.update.call_count == 2
        assert len(results) == 2


class TestRelevanceScore:
    def test_relevance_score_in_result(self):
        article = make_article()
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([article])

        assert results[0]["relevance_score"] == 9

    def test_relevance_reason_in_result(self):
        article = make_article()
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([article])

        assert results[0]["relevance_reason"] == VALID_CLAUDE_RESPONSE["relevance_reason"]

    def test_relevance_score_in_system_prompt(self):
        from src.processor import SYSTEM_PROMPT
        assert "relevance_score" in SYSTEM_PROMPT


class TestRetryAndDeadLetter:
    def test_retries_on_api_failure_before_giving_up(self):
        article = make_article()
        mock_messages = MagicMock()
        mock_messages.create.side_effect = Exception("transient error")
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 2), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.processor._write_failed") as mock_dlq:
            results = process_articles([article])

        assert mock_client.messages.create.call_count == 3  # 1 attempt + 2 retries
        mock_dlq.assert_called_once()
        assert len(results) == 0  # failed article excluded

    def test_succeeds_on_second_attempt(self):
        article = make_article()

        call_count = {"n": 0}

        def side_effect(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("transient")
            # Return a valid mock response on second attempt
            mock_msg = MagicMock()
            mock_msg.content = [MagicMock(text=json.dumps(VALID_CLAUDE_RESPONSE))]
            return mock_msg

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = side_effect

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 2), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([article])

        assert len(results) == 1
        assert results[0]["relevance_score"] == VALID_CLAUDE_RESPONSE["relevance_score"]

    def test_dead_letter_receives_article_url_and_reason(self):
        article = make_article()
        mock_messages = MagicMock()
        mock_messages.create.side_effect = Exception("api down")
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        written = {}

        def capture_dlq(article, reason, run_id):
            written["url"] = article["url"]
            written["reason"] = reason

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.processor._write_failed", side_effect=capture_dlq):
            process_articles([article])

        assert written["url"] == article["url"]
        assert "api down" in written["reason"]


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


class TestScoreCache:
    """Score caching: cache hit skips LLM; cache miss calls LLM and writes cache."""

    def test_cache_hit_skips_llm(self, db):
        url = "https://example.com/cached"
        with get_session() as session:
            session.add(ArticleScore(
                url=url, impact_score=8, authenticity_score=7, relevance_score=9,
                impact_reason="big impact", authenticity_reason="credible source",
                relevance_reason="on topic",
            ))

        article = make_article(url=url)

        with patch("anthropic.Anthropic") as mock_anthropic_cls, \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            results = process_articles([article])

        mock_anthropic_cls.return_value.messages.create.assert_not_called()
        assert len(results) == 1
        assert results[0]["impact_score"] == 8
        assert results[0]["relevance_score"] == 9
        assert results[0]["impact_reason"] == "big impact"

    def test_cache_miss_calls_llm(self, db):
        article = make_article(url="https://example.com/new")
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            results = process_articles([article])

        mock_client.messages.create.assert_called_once()
        assert results[0]["impact_score"] == VALID_CLAUDE_RESPONSE["impact_score"]

    def test_cache_miss_writes_scores_to_db(self, db):
        url = "https://example.com/write-test"
        article = make_article(url=url)
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            process_articles([article])

        with get_session() as session:
            row = session.get(ArticleScore, url)
        assert row is not None
        assert row.impact_score == VALID_CLAUDE_RESPONSE["impact_score"]
        assert row.relevance_score == VALID_CLAUDE_RESPONSE["relevance_score"]

    def test_expired_cache_entry_treated_as_miss(self, db):
        url = "https://example.com/expired"
        with get_session() as session:
            session.add(ArticleScore(
                url=url, impact_score=3, authenticity_score=3, relevance_score=3,
                impact_reason="old", authenticity_reason="old", relevance_reason="old",
                cached_at=datetime.now(timezone.utc) - timedelta(days=4),
            ))

        article = make_article(url=url)
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            results = process_articles([article])

        mock_client.messages.create.assert_called_once()
        assert results[0]["impact_score"] == VALID_CLAUDE_RESPONSE["impact_score"]


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
