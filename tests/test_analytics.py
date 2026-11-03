
from app.services.analytics_storage import AnalyticsStorage


def test_save_and_read_snapshot(tmp_path):
    storage = AnalyticsStorage(
        tmp_path / "analytics.db"
    )

    storage.save_snapshot(
        publication_id="pub-1",
        platform="youtube",
        platform_video_id="video-1",
        metrics={
            "views": 100,
            "likes": 10,
            "comments": 2,
            "watch_time_minutes": 45.0,
            "average_view_duration": 27.0
        }
    )

    snapshots = storage.latest_snapshots()

    assert len(snapshots) == 1
    assert snapshots[0]["views"] == 100
    assert snapshots[0]["likes"] == 10


def test_latest_snapshot(tmp_path):
    storage = AnalyticsStorage(
        tmp_path / "analytics.db"
    )

    for views in [100, 200, 300]:
        storage.save_snapshot(
            publication_id="pub-1",
            platform="youtube",
            platform_video_id="video-1",
            metrics={"views": views}
        )

    latest = storage.latest_snapshots()

    assert len(latest) == 1
    assert latest[0]["views"] == 300

    history = storage.history("pub-1")

    assert len(history) == 3


def test_missing_metrics_are_null(tmp_path):
    storage = AnalyticsStorage(
        tmp_path / "analytics.db"
    )

    storage.save_snapshot(
        publication_id="pub-2",
        platform="youtube",
        platform_video_id="video-2",
        metrics={"views": 50}
    )

    latest = storage.latest_snapshots()

    assert latest[0]["likes"] is None
    assert latest[0]["comments"] is None
