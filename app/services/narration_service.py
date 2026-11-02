
import math
import re

from dataclasses import dataclass

from app.models.content_plan import ContentPlan


@dataclass
class NarrationIssue:
    scene_number: int
    duration_seconds: int
    actual_words: int
    allowed_words: int
    estimated_seconds: float


class NarrationService:
    def __init__(
        self,
        words_per_minute=160,
        safety_factor=0.90
    ):
        if words_per_minute <= 0:
            raise ValueError(
                "words_per_minute must be positive"
            )

        if not 0 < safety_factor <= 1:
            raise ValueError(
                "safety_factor must be in (0, 1]"
            )

        self.wpm = words_per_minute
        self.safety_factor = safety_factor

    def count_words(self, text):
        return len(
            re.findall(
                r"\b[\w]+(?:['’-][\w]+)*\b",
                text,
                flags=re.UNICODE
            )
        )

    def estimate_duration(self, text):
        return (
            self.count_words(text)
            / self.wpm * 60
        )

    def max_words(self, duration_seconds):
        return math.floor(
            duration_seconds
            * self.wpm
            / 60
            * self.safety_factor
        )

    def validate_plan(
        self,
        plan: ContentPlan
    ) -> list[NarrationIssue]:
        issues = []

        for scene in plan.scenes:
            actual = self.count_words(
                scene.narration
            )

            allowed = self.max_words(
                scene.duration_seconds
            )

            if actual > allowed:
                issues.append(
                    NarrationIssue(
                        scene_number=scene.scene_number,
                        duration_seconds=(
                            scene.duration_seconds
                        ),
                        actual_words=actual,
                        allowed_words=allowed,
                        estimated_seconds=(
                            self.estimate_duration(
                                scene.narration
                            )
                        )
                    )
                )

        return issues

    def format_issues(self, issues):
        return "\n".join(
            (
                f"Scene {issue.scene_number}: "
                f"{issue.actual_words} words, "
                f"maximum {issue.allowed_words} "
                f"for {issue.duration_seconds}s."
            )
            for issue in issues
        )
