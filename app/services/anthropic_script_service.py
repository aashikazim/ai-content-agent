"""Claude Messages API adapter; explicit requests, no retries or model fallback.

Uses the existing requests dependency so the isolated GPU environment is untouched.
Model, pricing and structured-output contract checked against official docs 2026-11-04.
"""

import json
import os

import requests

from app.models.production_plan import ProductionPlan, strict_production_schema
from app.services.openai_script_service import PaidScriptError, script_limits


ANTHROPIC_MODEL = "claude-sonnet-5-5"
INPUT_USD_PER_MILLION = 2
OUTPUT_USD_PER_MILLION = 10
PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing"


def production_schema():
    """Remove unsupported grammar constraints; Pydantic still enforces all bounds."""
    schema = strict_production_schema()

    def transform(value):
        if isinstance(value, dict):
            constraints = []
            for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                        "multipleOf", "minLength", "maxLength", "maxItems", "pattern"):
                if key in value:
                    constraints.append(f"{key}={value.pop(key)}")
            if value.get("minItems", 0) > 1:
                constraints.append(f"minItems={value.pop('minItems')}")
            if constraints:
                value["description"] = (value.get("description", "") + "; Required: " + ", ".join(constraints)).strip("; ")
            for child in value.values():
                transform(child)
        elif isinstance(value, list):
            for child in value:
                transform(child)

    transform(schema)
    return schema


class AnthropicScriptService:
    def __init__(self, model=ANTHROPIC_MODEL, session=None):
        if model != ANTHROPIC_MODEL:
            raise PaidScriptError("Unsupported Claude model; choose a model from the catalogue")
        self.model = model
        self.session = session if session is not None else requests.Session()
        self.session.trust_env = False
        if isinstance(self.session, requests.Session):
            self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
        self.last_usage = None
        self.last_response_id = None

    def _headers(self):
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise PaidScriptError("ANTHROPIC_API_KEY is not configured")
        return {"Authorization": f"Bearer {key}", "anthropic-version": "2026-10-21",
                "Content-Type": "application/json"}

    def check_model(self):
        try:
            response = self.session.get(f"https://api.anthropic.com/v1/models/{self.model}",
                headers=self._headers(), timeout=(5, 20), allow_redirects=False)
            if response.status_code == 200 and response.json().get("id") == self.model:
                return
        except (requests.RequestException, ValueError, AttributeError):
            pass
        raise PaidScriptError("Claude model access could not be verified. Check ANTHROPIC_API_KEY and account access; no generation was submitted.")

    def generate_production_plan(self, prompt, *, configuration=None):
        limits = script_limits(configuration)
        payload = {
            "model": self.model,
            "max_tokens": limits["max_output_tokens"],
            "system": "Create an original production screenplay. All content remains draft. "
                      "Label fiction and satire; news remains unverified. Never invent evidence or citations. "
                      "Preserve exact requested timing, dialogue, character IDs and narration word budgets.",
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"format": {"type": "json_schema", "schema": production_schema()}, "effort": "low"},
        }
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > limits["max_input_bytes"]:
            raise PaidScriptError("Script input exceeds the authorized input limit; shorten the supplied material or revision")
        try:
            response = self.session.post("https://api.anthropic.com/v1/messages",
                headers=self._headers(), json=payload, timeout=(5, limits["timeout"]), allow_redirects=False)
        except requests.RequestException:
            raise PaidScriptError("The Claude request outcome is uncertain. Inspect provider usage before another request; no automatic retry was made.", uncertain=True) from None
        if response.status_code != 200:
            if response.status_code >= 500 or response.status_code in {408, 409}:
                raise PaidScriptError("Claude returned an uncertain outcome; reconcile before another request", uncertain=True)
            raise PaidScriptError("Claude rejected the script request. Check the API key, selected model, credits and settings; no fallback was selected.")
        try:
            body = response.json()
            self.last_usage = body.get("usage")
            self.last_response_id = body.get("id")
            if (body.get("stop_details") or {}).get("type") == "refusal" or body.get("stop_reason") == "refusal":
                raise PaidScriptError("Claude declined this script request; no automatic retry was made")
            if body.get("stop_reason") != "end_turn":
                raise PaidScriptError("Claude did not complete the screenplay. Inspect usage and request an explicit revision; no automatic retry was made.",
                    uncertain=body.get("stop_reason") not in {"max_tokens", "stop_sequence"})
            text = "".join(part["text"] for part in body["content"] if part.get("type") == "text")
            return ProductionPlan.model_validate_json(text)
        except PaidScriptError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError):
            raise PaidScriptError("Claude returned an invalid screenplay. Inspect usage before requesting a revision.",
                uncertain=self.last_response_id is None) from None

    def generate_plan(self, prompt):
        return self.generate_production_plan(prompt).content_plan
