"""Anthropic Messages API transport for email evaluation."""
from .email_evaluator import EmailEvaluator, INSTRUCTIONS

MESSAGES_URL = "https://api.anthropic.com/v1/messages"


class AnthropicEvaluator(EmailEvaluator):
    provider = "Anthropic"
    key_variable = "ANTHROPIC_API_KEY"
    model_variable = "JOB_TRACKER_ANTHROPIC_MODEL"
    default_model = "claude-haiku-5-5"

    def _request(self, evidence, schema):
        return self._post(
            MESSAGES_URL,
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            {
                "model": self.model, "system": INSTRUCTIONS,
                "messages": [{"role": "user", "content": evidence}],
                "output_config": {"format": {"type": "json_schema", "schema": schema}},
                "max_tokens": 1200,
            },
        )

    @staticmethod
    def _output_text(data):
        if data["stop_reason"] != "end_turn":
            return None
        texts = [part["text"] for part in data["content"] if part["type"] == "text"]
        return texts[0] if len(texts) == 1 else None
