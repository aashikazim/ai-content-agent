
import sqlite3
import pytest

from app.services.publishing_storage import PublishingStorage

from unittest.mock import Mock
from app.services.youtube_publisher import YouTubePublisher



def test_duplicate_reservation_is_blocked(tmp_path):
    storage = PublishingStorage(
        tmp_path / "publishing.db"
    )

    # The database schema is now created
    # automatically by PublishingStorage.
    first = storage.reserve_publication(
        "job-1",
        "youtube"
    )

    assert first

    # The same job cannot be published twice
    # on the same platform.
    with pytest.raises(sqlite3.IntegrityError):
        storage.reserve_publication(
            "job-1",
            "youtube"
        )


def test_event_recording(tmp_path):
    storage = PublishingStorage(
        tmp_path / "publishing.db"
    )

    # The publication_events table is
    # automatically created during initialization.
    storage.record_event(
        "pub-1",
        "status_checked",
        "processing"
    )

    with storage.connect() as conn:
        row = conn.execute("""
            SELECT
                publication_id,
                event_type,
                details
            FROM publication_events
            WHERE publication_id = ?
        """, ("pub-1",)).fetchone()

    assert row is not None
    assert row["publication_id"] == "pub-1"
    assert row["event_type"] == "status_checked"
    assert row["details"] == "processing"



def test_private_youtube_video_not_public():
    client = Mock()

    client.videos.return_value.list.return_value.execute.return_value = {
        "items": [
            {
                "status": {
                    "privacyStatus": "private",
                    "uploadStatus": "processed"
                },
                "processingDetails": {
                    "processingStatus": "succeeded"
                }
            }
        ]
    }

    publisher = YouTubePublisher(
        client=client
    )

    result = publisher.get_video_status(
        "video-123"
    )

    assert result["status"] == "private"


def test_processing_youtube_video():
    client = Mock()

    client.videos.return_value.list.return_value.execute.return_value = {
        "items": [
            {
                "status": {
                    "privacyStatus": "public",
                    "uploadStatus": "uploaded"
                },
                "processingDetails": {
                    "processingStatus": "processing"
                }
            }
        ]
    }

    publisher = YouTubePublisher(
        client=client
    )

    result = publisher.get_video_status(
        "video-123"
    )

    assert result["status"] == "processing"
