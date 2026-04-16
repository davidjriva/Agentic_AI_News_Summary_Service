import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import src.config as _cfg
from src.processor import process_articles


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
    "summary": "Anthropic researchers demonstrate agentic AI completing complex tasks. The study shows significant improvements in reliability. This marks a key milestone for the field.",
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


class TestValidJsonResponse:
    """(a) Valid Claude JSON response is parsed and keys merged into the article dict."""

    def test_valid_json_keys_merged_into_article(self):
        article = make_article()
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([article])

        result = results[0]
        assert result["summary"] == VALID_CLAUDE_RESPONSE["summary"]
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
            assert "summary" in result
            assert "impact_score" in result

    def test_returns_list(self):
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([make_article()])

        assert isinstance(results, list)


class TestFallbackOnMalformedResponse:
    """(b) When Claude returns malformed/non-JSON, article gets fallback values."""

    def test_fallback_impact_score_on_invalid_json(self):
        article = make_article()
        mock_client = _make_mock_client("This is not valid JSON at all!")

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([article])

        assert results[0]["impact_score"] == 5

    def test_fallback_authenticity_score_on_invalid_json(self):
        article = make_article()
        mock_client = _make_mock_client("not json")

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([article])

        assert results[0]["authenticity_score"] == 5

    def test_fallback_summary_is_description_truncated_to_200(self):
        long_description = "A" * 300
        article = make_article(description=long_description)
        mock_client = _make_mock_client("not json")

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([article])

        assert results[0]["summary"] == long_description[:200]

    def test_fallback_summary_short_description_unchanged(self):
        short_description = "Short description."
        article = make_article(description=short_description)
        mock_client = _make_mock_client("{broken json")

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([article])

        assert results[0]["summary"] == short_description

    def test_fallback_on_api_exception(self):
        article = make_article()
        mock_messages = MagicMock()
        mock_messages.create.side_effect = Exception("API error")
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([article])

        assert results[0]["impact_score"] == 5
        assert results[0]["authenticity_score"] == 5
        assert results[0]["summary"] == article["description"][:200]

    def test_fallback_original_keys_still_present(self):
        article = make_article()
        mock_client = _make_mock_client("not json")

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([article])

        result = results[0]
        assert result["title"] == article["title"]
        assert result["url"] == article["url"]
        assert result["author"] == article["author"]


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
        assert kwargs["max_tokens"] == 512


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
        assert results[0]["summary"] == VALID_CLAUDE_RESPONSE["summary"]

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

    def test_local_provider_fallback_on_request_error(self):
        article = make_article()

        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch("requests.post", side_effect=Exception("connection refused")):
            results = process_articles([article])

        assert results[0]["impact_score"] == 5
        assert results[0]["authenticity_score"] == 5
        assert results[0]["summary"] == article["description"][:200]

    def test_local_provider_fallback_on_malformed_json(self):
        article = make_article()
        mock_resp = _make_local_mock_response("not valid json")

        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch("requests.post", return_value=mock_resp):
            results = process_articles([article])

        assert results[0]["impact_score"] == 5

    def test_local_provider_does_not_call_anthropic(self):
        article = make_article()
        mock_resp = _make_local_mock_response(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch("requests.post", return_value=mock_resp), \
             patch("anthropic.Anthropic") as mock_anthropic:
            process_articles([article])

        mock_anthropic.assert_not_called()


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
