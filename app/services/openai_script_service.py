"""Explicit OpenAI Responses adapter; no SDK retries, fallback, or secret logging."""

import json
import os

import requests

from app.models.production_plan import ProductionPlan, strict_production_schema


OPENAI_SCRIPT_MODELS = ("gpt-6.1-sol", "gpt-6-sol")
MAX_OUTPUT_TOKENS = 8000
MAX_INPUT_BYTES = 60000
MAX_INPUT_TOKENS_ESTIMATE = 65000
INPUT_USD_PER_MILLION = 2
OUTPUT_USD_PER_MILLION = 10


def script_limits(config=None):
    long = config is not None and config.content_type == 'long_drama'
    return {'max_output_tokens': 64000 if long else MAX_OUTPUT_TOKENS,
            'max_input_bytes': 200000 if long else MAX_INPUT_BYTES,
            'input_tokens_estimate': 210000 if long else MAX_INPUT_TOKENS_ESTIMATE,
            'timeout': 900 if long else 120}


class PaidScriptError(RuntimeError):
    def __init__(self, message, *, uncertain=False):
        self.uncertain = uncertain
        super().__init__(message)


class OpenAIScriptService:
    def __init__(self, model="gpt-6.1-sol", session=None):
        if model not in OPENAI_SCRIPT_MODELS:
            raise PaidScriptError("Unsupported OpenAI script model; select a documented catalogue model")
        self.model = model
        self.session = session if session is not None else requests.Session()
        self.session.trust_env = False
        # Explicitly replace any custom retry adapters on an injected Session.
        if isinstance(self.session, requests.Session):
            self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
        self.last_usage = None
        self.last_response_id = None

    def _headers(self):
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise PaidScriptError("OPENAI_API_KEY is not configured")
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def available_models(self):
        try:
            response = self.session.get("https://api.openai.com/v1/models",
                headers=self._headers(), timeout=(5, 20), allow_redirects=False)
        except requests.RequestException:
            raise PaidScriptError("Could not check OpenAI model access; no generation was submitted") from None
        if response.status_code != 200:
            raise PaidScriptError("OpenAI model access could not be verified; check the API key and project permissions")
        try:
            ids = {entry["id"] for entry in response.json()["data"]}
        except (ValueError, KeyError, TypeError):
            raise PaidScriptError("OpenAI returned an invalid model catalogue") from None
        return [model for model in OPENAI_SCRIPT_MODELS if model in ids]

    def check_model(self):
        if self.model not in self.available_models():
            raise PaidScriptError(
                "The selected OpenAI model is unavailable to this account. Check access or explicitly select another available catalogue model."
            )

    def generate_production_plan(self, prompt, *, configuration=None):
        limits = script_limits(configuration)
        payload = {
            "model": self.model,
            "instructions": (
                "Create an original production screenplay at the requested length. Return only the required structured output. "
                "All content remains draft. Fiction and satire must be labelled. News is unverified; "
                "never invent citations, evidence, statistics or quotations. Include all dialogue in each "
                "scene's spoken narration and preserve the requested timing and word budgets."
            ),
            "input": prompt,
            "text": {"format": {"type": "json_schema", "name": "production_plan",
                                "strict": True, "schema": strict_production_schema()}},
            "max_output_tokens": limits['max_output_tokens'],
            "reasoning": {"effort": "low"},
            "service_tier": "default",
            "store": False,
        }
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > limits['max_input_bytes']:
            raise PaidScriptError("Script input exceeds the authorized input limit; shorten the topic, script or revision")
        try:
            response = self.session.post("https://api.openai.com/v1/responses",
                headers=self._headers(), json=payload, timeout=(5, limits['timeout']), allow_redirects=False)
        except requests.RequestException:
            raise PaidScriptError(
                "The OpenAI request outcome is uncertain. Inspect its attempt record; it will not be retried automatically.",
                uncertain=True,
            ) from None
        if response.status_code != 200:
            if response.status_code >= 500 or response.status_code in {408, 409}:
                raise PaidScriptError("OpenAI returned an uncertain outcome; reconcile before another request", uncertain=True)
            if response.status_code in {401, 403, 404}:
                raise PaidScriptError("OpenAI key, project access or selected model is unavailable; no fallback was selected")
            raise PaidScriptError("OpenAI rejected the script request. Check the selected model, quota and settings.")
        try:
            body = response.json()
            self.last_usage = body.get("usage")
            self.last_response_id = body.get("id")
            if body.get("status") != "completed":
                raise PaidScriptError(
                    "OpenAI did not complete the script; inspect usage before requesting a revision",
                    uncertain=body.get("status") not in {"incomplete", "failed", "cancelled"},
                )
            content = [part for output in body.get("output", []) if output.get("type") == "message"
                       for part in output.get("content", [])]
            if any(part.get("type") == "refusal" for part in content):
                raise PaidScriptError("OpenAI declined this script request; no automatic retry was made")
            text = "".join(part.get("text", "") for part in content if part.get("type") == "output_text")
            return ProductionPlan.model_validate_json(text)
        except PaidScriptError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError):
            raise PaidScriptError("OpenAI returned an invalid production script; inspect usage before requesting a revision",
                                  uncertain=self.last_response_id is None) from None

    def generate_plan(self, prompt):
        return self.generate_production_plan(prompt).content_plan
