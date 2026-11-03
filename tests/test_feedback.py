
from app.services.feedback_service import FeedbackService


def test_feedback_score():
    service = FeedbackService()

    score = service.score_video({
        "views": 1000,
        "likes": 50,
        "comments": 10
    })

    assert score > 0


def test_missing_views():
    service = FeedbackService()

    score = service.score_video({
        "views": None,
        "likes": 10,
        "comments": 2
    })

    assert score is None


def test_category_ranking():
    service = FeedbackService()

    videos = [
        {
            "category": "science",
            "views": 1000,
            "likes": 100,
            "comments": 20
        },
        {
            "category": "science",
            "views": 1200,
            "likes": 110,
            "comments": 15
        },
        {
            "category": "comedy",
            "views": 1000,
            "likes": 10,
            "comments": 2
        }
    ]

    result = service.rank_categories(
        videos
    )

    assert result[0]["category"] == "science"
    assert result[0]["video_count"] == 2
