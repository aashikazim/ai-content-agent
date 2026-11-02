
from datetime import datetime, timezone

from app.models.video import Video
from app.services.storage_service import StorageService


def test_save_and_update_video(tmp_path):
    storage = StorageService(
        tmp_path / "test.db"
    )

    video = Video(
        video_id="abc123",
        title="Example Video",
        category_id="28",
        channel="Test Channel",
        published_at=datetime.now(timezone.utc),
        duration_seconds=50,
        views=1000,
        likes=100,
        comments=10,
        url="https://youtube.com/watch?v=abc123",
        score=2.5
    )

    storage.save_videos([video])
    assert storage.count_videos() == 1

    video.views = 2000
    storage.save_videos([video])

    # Upsert should not create another record
    assert storage.count_videos() == 1

    with storage.connect() as conn:
        views = conn.execute(
            "SELECT views FROM videos WHERE video_id=?",
            ("abc123",)
        ).fetchone()[0]

    assert views == 2000
