
from app.services.llm_service import LLMService


class ContentAgent:
    def __init__(self, llm=None):
        self.llm = llm or LLMService()

    def create_plan(
        self,
        videos,
        category="mixed",
        language="English"
    ):
        if not videos:
            raise ValueError(
                "No videos available for planning"
            )

        candidates = videos[:5]

        source_text = "\n".join(
            f"- {video.title} "
            f"(category={video.category_id}, "
            f"views={video.views}, "
            f"score={video.score})"
            for video in candidates
        )

        prompt = f"""
Create ONE original short-form video plan.

Content category: {category}
Language: {language}
Target platforms: YouTube Shorts, Facebook Reels
Target duration: approximately 45 seconds
Format: vertical 9:16 video

Trending topic signals:
{source_text}

Instructions:
- Select one suitable topic from the signals.
- Do not reproduce an existing video.
- Prefer topics that can be explained without
  relying on unverified recent facts.
- Create an engaging opening hook.
- Create 4-6 scenes.
- Each scene needs narration, duration,
  visual prompt, and on-screen text.
- Keep total duration between 30 and 60 seconds.
- Make narration fit its allocated duration.
- Use only original visual concepts.
- Set source_video_ids to an empty list;
  source IDs will be assigned by the application.
- Set review_status to draft.
"""

        plan = self.llm.generate_plan(prompt)

        # Do not trust model-generated source IDs or status.
        plan.source_video_ids = [
            video.video_id for video in candidates
        ]
        plan.review_status = "draft"

        return plan
