
import os
import time
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError


SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload"
]


class YouTubePublisher:
    def __init__(self, client=None):
        self.client = client

    def get_client(self):
        if self.client is not None:
            return self.client

        token_path = Path(
            os.getenv(
                "YOUTUBE_TOKEN_FILE",
                "credentials/youtube_token.json"
            )
        )

        if not token_path.exists():
            raise RuntimeError(
                "YouTube OAuth token missing. "
                "Run scripts/youtube_auth.py."
            )

        credentials = Credentials.from_authorized_user_file(
            str(token_path),
            SCOPES
        )

        if not credentials.valid:
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
                token_path.write_text(
                    credentials.to_json(),
                    encoding="utf-8"
                )
                token_path.chmod(0o600)
            else:
                raise RuntimeError(
                    "YouTube authorization expired. "
                    "Run the OAuth setup again."
                )

        self.client = build(
            "youtube",
            "v3",
            credentials=credentials,
            cache_discovery=False
        )

        return self.client

    def upload(
        self,
        video_path,
        title,
        description,
        tags=None,
        privacy="private"
    ):
        video_path = Path(video_path)

        if not video_path.is_file():
            raise FileNotFoundError(video_path)

        if privacy not in {
            "private", "unlisted", "public"
        }:
            raise ValueError("Invalid privacy setting")

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "categoryId": "22",
                "tags": (tags or [])[:30]
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False
            }
        }

        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            chunksize=8 * 1024 * 1024,
            resumable=True
        )

        request = self.get_client().videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        response = None
        retries = 0

        while response is None:
            try:
                _, response = request.next_chunk()
                retries = 0
            except HttpError as exc:
                if exc.resp.status not in {
                    500, 502, 503, 504
                }:
                    raise

                retries += 1

                if retries > 5:
                    raise

                time.sleep(
                    min(2 ** retries, 30)
                )

        video_id = response.get("id")

        if not video_id:
            raise RuntimeError(
                "YouTube did not return a video ID"
            )

        return {
            "platform": "youtube",
            "video_id": video_id,
            "url": (
                "https://www.youtube.com/watch?v="
                + video_id
            ),
            "status": "uploaded"
        }
    
    def get_video_status(self, video_id):
        response = (
            self.get_client()
            .videos()
            .list(
                part="status,processingDetails",
                id=video_id
            )
            .execute()
        )

        items = response.get("items", [])

        if not items:
            return {
                "status": "unknown",
                "external_status": "not_accessible"
            }

        video = items[0]

        privacy = video.get(
            "status", {}
        ).get("privacyStatus")

        upload_status = video.get(
            "status", {}
        ).get("uploadStatus")

        processing = video.get(
            "processingDetails", {}
        ).get("processingStatus")

        if upload_status in {
            "failed", "rejected", "deleted"
        }:
            internal_status = "failed"

        elif processing in {
            "processing", "pending"
        }:
            internal_status = "processing"

        elif processing == "failed":
            internal_status = "failed"

        elif (
            upload_status == "processed"
            and processing in {
                "succeeded", "terminated"
            }
        ):
            if privacy == "public":
                internal_status = "published"
            elif privacy == "private":
                internal_status = "private"
            else:
                internal_status = "uploaded"

        else:
            internal_status = "processing"

        return {
            "status": internal_status,
            "external_status": (
                f"upload={upload_status};"
                f"processing={processing};"
                f"privacy={privacy}"
            )
        }

