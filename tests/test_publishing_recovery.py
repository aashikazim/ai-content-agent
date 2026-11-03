
import sqlite3
import pytest

from app.services.publishing_storage import PublishingStorage

from unittest.mock import Mock
from app.services.youtube_publisher import YouTubePublisher
from tests.test_publishing import publishing_setup



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


@pytest.mark.parametrize('status,platform,remote', [
    ('uploaded', 'facebook', None), ('uploading', 'facebook', None),
    ('pending', 'facebook', None), ('needs_reconciliation', 'youtube', None),
    ('needs_reconciliation', 'facebook', 'known-video'),
])
def test_retry_cannot_release_an_active_uploaded_or_remote_known_attempt(tmp_path, status, platform, remote):
    storage = PublishingStorage(tmp_path / 'test.db')
    identifier = storage.reserve_publication('job', platform)
    storage.update(identifier, status, platform_video_id=remote)
    with pytest.raises(ValueError, match='Retry authorization requires'):
        storage.authorize_retry(identifier, confirmed_not_uploaded=True, notes='Inspected the account')
    assert storage.get(identifier)['status'] == status


def test_manual_retry_requires_confirmation_and_preserves_inspection_history(tmp_path):
    import json
    storage = PublishingStorage(tmp_path / 'test.db')
    identifier = storage.reserve_publication('job', 'facebook')
    storage.update(identifier, 'needs_reconciliation', error='401 Unauthorized for https://graph.facebook.com/path?access_token=private-token')
    for confirmed, notes in [(False,'Inspected'), (True,''), (1,'Inspected')]:
        with pytest.raises(ValueError, match='Confirm'):
            storage.authorize_retry(identifier, confirmed_not_uploaded=confirmed, notes=notes)
    value = storage.authorize_retry(identifier, confirmed_not_uploaded=True, notes='Start rejected with expired token; no upload was accepted')
    assert value['status'] == 'retry_authorized'
    assert value['error'].startswith('401')
    with storage.connect() as conn:
        event = conn.execute("SELECT details FROM publication_events WHERE event_type='retry_authorized'").fetchone()
    details = json.loads(event['details'])
    assert details['previous_status'] == 'needs_reconciliation'
    assert details['previous_attempt_started_at']
    assert 'private-token' not in event['details']
    assert len(storage.list_publications()) == 1
    with pytest.raises(ValueError):
        storage.authorize_retry(identifier, confirmed_not_uploaded=True, notes='Already authorized')


def test_authorized_retry_can_only_be_reserved_once_under_concurrency(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    storage = PublishingStorage(tmp_path / 'test.db')
    identifier = storage.reserve_publication('job','facebook')
    storage.update(identifier, 'needs_reconciliation')
    storage.authorize_retry(identifier, confirmed_not_uploaded=True, notes='Verified rejected start')
    def claim(_):
        try: return storage.reserve_authorized_retry(identifier)
        except ValueError: return None
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(claim, range(3)))
    assert results.count(identifier) == 1
    assert storage.get(identifier)['status'] == 'pending'
    assert len(storage.list_publications()) == 1


def test_facebook_retry_still_needs_separate_manual_publish_and_is_single_use(publishing_setup):
    jobs, job_id, service, _ = publishing_setup
    jobs.review_job(job_id, 'approved')
    facebook = Mock()
    facebook.upload.return_value = {'video_id':'fb-accepted', 'status':'submitted'}
    service.facebook = facebook
    storage = service.publications
    identifier = storage.reserve_publication(job_id,'facebook')
    storage.update(identifier,'needs_reconciliation',error='401 Unauthorized')
    with pytest.raises(ValueError, match='already exists'):
        service.publish(job_id, 'facebook', dry_run=False)
    storage.authorize_retry(identifier, confirmed_not_uploaded=True, notes='Meta rejected start; no video exists')
    facebook.upload.assert_not_called()
    assert service.publish(job_id,'facebook',dry_run=True)['status'] == 'dry_run'
    assert storage.get(identifier)['status'] == 'retry_authorized'
    result = service.publish(job_id, 'facebook', dry_run=False)
    assert result['publication_id'] == identifier and result['status'] == 'submitted'
    with pytest.raises(ValueError, match='already exists'):
        service.publish(job_id, 'facebook', dry_run=False)
    facebook.upload.assert_called_once()
    assert len(storage.list_publications()) == 1


def test_uncertain_manual_retry_needs_new_human_inspection(publishing_setup):
    jobs, job_id, service, _ = publishing_setup
    jobs.review_job(job_id,'approved')
    facebook = Mock(); facebook.upload.side_effect = TimeoutError('Uncertain fixture')
    service.facebook = facebook
    identifier = service.publications.reserve_publication(job_id,'facebook')
    service.publications.update(identifier,'needs_reconciliation')
    service.publications.authorize_retry(identifier, confirmed_not_uploaded=True, notes='Inspected prior rejected start')
    with pytest.raises(TimeoutError): service.publish(job_id,'facebook',dry_run=False)
    assert service.publications.get(identifier)['status'] == 'needs_reconciliation'
    with pytest.raises(ValueError): service.publish(job_id,'facebook',dry_run=False)
    facebook.upload.assert_called_once()
