
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
        language="English",
        target_duration_seconds=None,
        aspect_ratio="9:16",
        visual_style="cinematic",
        max_scenes=12,
        topic="",
        supplied_script="",
    ):
        if not videos and not topic.strip() and not supplied_script.strip():
            raise ValueError(
                "No videos available for planning"
            )

        if target_duration_seconds is not None and not 30 <= target_duration_seconds <= 60:
            raise ValueError("Local content plans require a 30–60 second target")
        if not 1 <= max_scenes <= 12:
            raise ValueError("max_scenes must be between 1 and 12")
        target = (
            f"exactly {target_duration_seconds} seconds"
            if target_duration_seconds is not None else "approximately 45 seconds"
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
        if topic.strip():
            source_text = "User-selected topic:\n" + topic
        elif supplied_script.strip():
            source_text = "User-provided script to adapt while retaining its intent:\n" + supplied_script

        wpm = self.narration_service.wpm

        prompt = f"""
Create ONE original short-form video plan.

Category: {category}
Language: {language}
Platforms: YouTube Shorts and Facebook Reels
Target duration: {target}
Video aspect ratio: {aspect_ratio}
Visual style: {visual_style.replace('_', ' ')}

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
- Create {min(4, max_scenes)}-{min(6, max_scenes)} scenes.
- Never exceed {max_scenes} scenes.
- Total duration must be 30-60 seconds.
- Provide narration, visual prompt,
  visual type and on-screen text per scene.
- Keep scene numbers sequential.
- Set review_status to draft.
- Set source_video_ids to an empty list.
- Return valid JSON matching the schema.
"""

        last_feedback = ""

        for attempt in range(
            1, self.max_attempts + 1
        ):
            plan = self.llm.generate_plan(prompt)

            issues = (
                self.narration_service.validate_plan(
                    plan
                )
            )
            planning_issues = []
            if target_duration_seconds is not None and plan.total_duration != target_duration_seconds:
                planning_issues.append(
                    f"Total duration is {plan.total_duration}s; it must be exactly {target_duration_seconds}s."
                )
            if len(plan.scenes) > max_scenes:
                planning_issues.append(f"Plan has {len(plan.scenes)} scenes; maximum is {max_scenes}.")
            if not issues and not planning_issues:
                plan.source_video_ids = [
                    video.video_id
                    for video in candidates
                ]
                plan.review_status = "draft"
                return plan

            feedback = "\n".join(filter(None, [
                self.narration_service.format_issues(issues), *planning_issues
            ]))
            last_feedback = feedback

            print(
                f"Content timing validation failed "
                f"(attempt {attempt}/"
                f"{self.max_attempts}):"
            )
            print(feedback)

            prompt += f"""

The previous plan did not meet the timing or scene requirements:

{feedback}

Generate a revised COMPLETE content plan.
Shorten narration in the affected scenes.
Keep total duration between 30 and 60 seconds.
Respect the selected target duration and scene limit above.
"""

        raise ValueError(
            "Unable to generate a narration-safe "
            "script after "
            f"{self.max_attempts} attempts:\n"
            + last_feedback
        )
