"""OpenAI Responses API transport for email evaluation."""
from .email_evaluator import EmailEvaluator, INSTRUCTIONS

RESPONSES_URL = "https://api.openai.com/v1/responses"


class OpenAIEvaluator(EmailEvaluator):
    provider = "OpenAI"
    key_variable = "OPENAI_API_KEY"
    model_variable = "JOB_TRACKER_OPENAI_MODEL"
    default_model = "gpt-4o-mini"

    def _request(self, evidence, schema):
        return self._post(
            RESPONSES_URL,
            {"Authorization": f"Bearer {self.api_key}"},
            {
                "model": self.model, "store": False, "instructions": INSTRUCTIONS, "input": evidence,
                "text": {"format": {
                    "type": "json_schema", "name": "application_email", "strict": True, "schema": schema,
                }},
                "max_output_tokens": 700,
            },
        )

    @staticmethod
    def _output_text(data):
        if data["status"] != "completed":
            return None
        texts = [part["text"] for item in data["output"] if item["type"] == "message"
                 for part in item["content"] if part["type"] == "output_text"]
        return texts[0] if len(texts) == 1 else None
