
from app.services.llm_service import LLMService
from app.services.narration_service import NarrationService


class ContentAgent:
    def __init__(
        self,
        llm=None,
        narration_service=None,
        max_attempts=3
    ):
        self.llm = (
            llm if llm is not None
            else LLMService()
        )

        self.narration_service = (
            narration_service
            if narration_service is not None
            else NarrationService()
        )

        self.max_attempts = max_attempts

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
            (
                f"- {video.title} "
                f"(category={video.category_id}, "
                f"views={video.views}, "
                f"score={video.score})"
            )
            for video in candidates
        )

        wpm = self.narration_service.wpm

        prompt = f"""
Create ONE original short-form video plan.

Category: {category}
Language: {language}
Platforms: YouTube Shorts and Facebook Reels
Target duration: approximately 45 seconds
Video format: vertical 9:16

Trending topic signals:
{source_text}

Narration timing requirements:
- Target speaking rate: {wpm} words per minute.
- Reserve 10% of scene duration for pauses.
- Maximum words for a 5-second scene:
  {self.narration_service.max_words(5)}
- Maximum words for a 6-second scene:
  {self.narration_service.max_words(6)}
- Maximum words for a 10-second scene:
  {self.narration_service.max_words(10)}
- Maximum words for a 15-second scene:
  {self.narration_service.max_words(15)}
- For any other scene duration, calculate:
  floor(duration_seconds * {wpm} / 60 * 0.90)

Content requirements:
- Generate an original explanation.
- Do not copy existing video scripts.
- Avoid unsupported factual claims.
- Create 4-6 scenes.
- Total duration must be 30-60 seconds.
- Provide narration, visual prompt,
  visual type and on-screen text per scene.
- Keep scene numbers sequential.
- Set review_status to draft.
- Set source_video_ids to an empty list.
- Return valid JSON matching the schema.
"""

        last_issues = []

        for attempt in range(
            1, self.max_attempts + 1
        ):
            plan = self.llm.generate_plan(prompt)

            issues = (
                self.narration_service.validate_plan(
                    plan
                )
            )

            if not issues:
                plan.source_video_ids = [
                    video.video_id
                    for video in candidates
                ]
                plan.review_status = "draft"
                return plan

            last_issues = issues

            feedback = (
                self.narration_service.format_issues(
                    issues
                )
            )

            print(
                f"Narration validation failed "
                f"(attempt {attempt}/"
                f"{self.max_attempts}):"
            )
            print(feedback)

            prompt += f"""

Previous generated script exceeded
the narration word budget:

{feedback}

Generate a revised COMPLETE content plan.
Shorten narration in the affected scenes.
Keep total duration between 30 and 60 seconds.
"""

        raise ValueError(
            "Unable to generate a narration-safe "
            "script after "
            f"{self.max_attempts} attempts:\n"
            + self.narration_service.format_issues(
                last_issues
            )
        )
