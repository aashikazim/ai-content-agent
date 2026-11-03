
import os

from ollama import Client
from dotenv import load_dotenv

from app.models.content_plan import ContentPlan

load_dotenv()


SYSTEM_PROMPT = """
You are a short-form video content planning assistant.

Your task is to create original video concepts for
YouTube Shorts and Facebook Reels.

Rules:
1. Use trending videos only as topic signals.
2. Never copy an existing video's script or narration.
3. Generate original explanations and visual concepts.
4. Do not invent facts, statistics, or quotations.
5. Avoid unsupported claims about recent events.
6. Do not request copyrighted footage or music.
7. Create 30-60 seconds of content.
8. Use short narration suitable for spoken delivery.
9. Use visual_type values: generated_image,
   text_card, or diagram.
10. Keep review_status as draft.
11. Return data matching the requested JSON schema.
"""


class LLMService:
    def __init__(self, client=None, model=None):
        self.client = client or Client(
            host=os.getenv(
                "OLLAMA_HOST",
                "http://127.0.0.1:11434"
            ),
            timeout=180
        )

        self.model = model or os.getenv(
            "OLLAMA_MODEL", "qwen3:8b"
        )

    def generate_plan(self, prompt):
        response = self.client.chat(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            format=ContentPlan.model_json_schema(),
            options={
                "temperature": 0.5,
                "num_predict": 2500
            },
            stream=False
        )

        return ContentPlan.model_validate_json(
            response.message.content
        )

    def generate_production_plan(self, prompt):
        from app.models.production_plan import ProductionPlan
        response = self.client.chat(
            model=self.model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": prompt}],
            format=ProductionPlan.model_json_schema(),
            options={"temperature": 0.5, "num_predict": 8000}, stream=False,
        )
        return ProductionPlan.model_validate_json(response.message.content)
