"""Tests for AI agent module (PydanticDeep)."""

from unittest.mock import patch

from app.agents.pydantic_deep_assistant import PydanticDeepAssistant


class TestGetModelString:
    """Tests for PydanticDeepAssistant._get_model_string."""

    def test_openai_provider_uses_responses_api_prefix(self):
        assistant = PydanticDeepAssistant(model_name="gpt-5.5")
        with patch("app.agents.pydantic_deep_assistant.settings.LLM_PROVIDER", "openai"):
            assert assistant._get_model_string() == "openai-responses:gpt-5.5"

    def test_anthropic_provider_prefix(self):
        assistant = PydanticDeepAssistant(model_name="claude-opus-4-7")
        with patch("app.agents.pydantic_deep_assistant.settings.LLM_PROVIDER", "anthropic"):
            assert assistant._get_model_string() == "anthropic:claude-opus-4-7"

    def test_google_provider_prefix(self):
        assistant = PydanticDeepAssistant(model_name="gemini-2.5-flash")
        with patch("app.agents.pydantic_deep_assistant.settings.LLM_PROVIDER", "google"):
            assert assistant._get_model_string() == "google-gla:gemini-2.5-flash"

    def test_explicit_openai_prefix_rewritten_to_responses(self):
        assistant = PydanticDeepAssistant(model_name="openai:gpt-5.5")
        assert assistant._get_model_string() == "openai-responses:gpt-5.5"

    def test_explicit_responses_prefix_unchanged(self):
        assistant = PydanticDeepAssistant(model_name="openai-responses:gpt-5.5")
        assert assistant._get_model_string() == "openai-responses:gpt-5.5"

    def test_non_openai_explicit_prefix_unchanged(self):
        assistant = PydanticDeepAssistant(model_name="anthropic:claude-opus-4-7")
        assert assistant._get_model_string() == "anthropic:claude-opus-4-7"
