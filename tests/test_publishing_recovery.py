
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


@pytest.mark.parametrize("error_type", ["server_error", "timeout"])
def test_youtube_uncertain_chunk_is_never_retried(tmp_path, error_type):
    from googleapiclient.errors import HttpError
    from httplib2 import Response

    path = tmp_path / "fixture.mp4"
    path.write_bytes(b"test-video")
    client = Mock()
    request = client.videos.return_value.insert.return_value
    error = (
        HttpError(Response({"status": "503"}), b'{"error":"unavailable"}')
        if error_type == "server_error" else TimeoutError("uncertain upload")
    )
    request.next_chunk.side_effect = error
    with pytest.raises(type(error)):
        YouTubePublisher(client).upload(path, "Fixture", "Description")
    request.next_chunk.assert_called_once()


def test_youtube_advances_acknowledged_resumable_chunks(tmp_path):
    path = tmp_path / "fixture.mp4"
    path.write_bytes(b"test-video")
    client = Mock()
    request = client.videos.return_value.insert.return_value
    request.next_chunk.side_effect = [(None, None), (None, {"id": "fixture-id"})]
    result = YouTubePublisher(client).upload(path, "Fixture", "Description")
    assert result["video_id"] == "fixture-id"
    assert request.next_chunk.call_count == 2
