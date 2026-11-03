
import os
from pathlib import Path
from urllib.parse import urlparse

import requests


class FacebookPublisher:
    def __init__(
        self,
        page_id=None,
        access_token=None,
        api_version=None,
        session=None
    ):
        self.page_id = (
            page_id or os.getenv("FACEBOOK_PAGE_ID")
        )
        self.access_token = (
            access_token
            or os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
        )
        self.api_version = (
            api_version
            or os.getenv("META_GRAPH_API_VERSION", "v26.0")
        )
        self.session = (
            session if session is not None
            else requests.Session()
        )

        if not self.page_id or not self.access_token:
            raise ValueError(
                "Facebook Page ID and token are required"
            )

    @property
    def endpoint(self):
        return (
            f"https://graph.facebook.com/"
            f"{self.api_version}/"
            f"{self.page_id}/video_reels"
        )

    def headers(self):
        return {
            "Authorization": (
                f"Bearer {self.access_token}"
            )
        }

    def start_upload(self):
        response = self.session.post(
            self.endpoint,
            headers=self.headers(),
            data={"upload_phase": "start"},
            timeout=30
        )
        response.raise_for_status()

        result = response.json()

        if not result.get("video_id"):
            raise RuntimeError(
                "Meta did not return a video ID"
            )

        if not result.get("upload_url"):
            raise RuntimeError(
                "Meta did not return an upload URL"
            )

        return result

    def transfer_video(
        self, upload_url, video_path
    ):
        path = Path(video_path)
        size = path.stat().st_size

        parsed = urlparse(upload_url)

        if (
            parsed.scheme != "https"
            or parsed.hostname != "rupload.facebook.com"
        ):
            raise ValueError(
                "Unexpected Meta upload URL"
            )

        headers = {
            **self.headers(),
            "offset": "0",
            "file_size": str(size),
            "Content-Type": "application/octet-stream"
        }

        with path.open("rb") as file:
            response = self.session.post(
                upload_url,
                headers=headers,
                data=file,
                timeout=600
            )

        response.raise_for_status()
        return response.json()

    def finish_upload(
        self, video_id, description
    ):
        response = self.session.post(
            self.endpoint,
            headers=self.headers(),
            data={
                "upload_phase": "finish",
                "video_id": video_id,
                "video_state": "PUBLISHED",
                "description": description
            },
            timeout=60
        )

        response.raise_for_status()
        return response.json()

    def get_status(self, video_id):
        response = self.session.get(
            (
                f"https://graph.facebook.com/"
                f"{self.api_version}/{video_id}"
            ),
            headers=self.headers(),
            params={"fields": "status"},
            timeout=30
        )

        response.raise_for_status()
        return response.json()

    def upload(
        self,
        video_path,
        title,
        description
    ):
        path = Path(video_path)

        if not path.is_file():
            raise FileNotFoundError(path)

        session = self.start_upload()
        video_id = session["video_id"]

        self.transfer_video(
            session["upload_url"],
            path
        )

        result = self.finish_upload(
            video_id,
            f"{title}\n\n{description}"
        )

        return {
            "platform": "facebook",
            "video_id": video_id,
            "url": None,
            "status": "submitted",
            "response": result
        }
