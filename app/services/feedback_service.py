
import math
from collections import defaultdict


class FeedbackService:
    def score_video(self, metrics):
        views = metrics.get("views")
        likes = metrics.get("likes")
        comments = metrics.get("comments")

        if views is None:
            return None

        views = max(int(views), 0)
        likes = max(int(likes or 0), 0)
        comments = max(int(comments or 0), 0)

        engagement = (
            likes + comments + 2
        ) / (views + 100)

        view_score = math.log10(
            1 + views
        )

        score = (
            0.7 * view_score
            + 0.3 * (100 * engagement)
        )

        return round(score, 4)

    def rank_categories(self, videos):
        """
        Each input item:
        {
            "category": "science",
            "views": 1000,
            "likes": 50,
            "comments": 5
        }
        """
        grouped = defaultdict(list)

        for video in videos:
            score = self.score_video(video)

            if score is None:
                continue

            category = (
                video.get("category")
                or "unknown"
            )

            grouped[category].append(score)

        results = []

        for category, scores in grouped.items():
            results.append({
                "category": category,
                "video_count": len(scores),
                "average_score": round(
                    sum(scores) / len(scores),
                    4
                )
            })

        return sorted(
            results,
            key=lambda item: item[
                "average_score"
            ],
            reverse=True
        )
