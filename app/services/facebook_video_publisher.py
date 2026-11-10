"""Facebook Page long-video upload (non-Reels) via the resumable Graph Video API.

Used only when a caller explicitly allows long Facebook uploads (the opt-in
Governor for series episodes). Reels stay on FacebookPublisher. No retries: any
failed phase raises and the publication is held for reconciliation.
"""
import os
from pathlib import Path

import requests

CHUNK_LIMIT = 64 * 1024 * 1024


class FacebookVideoPublisher:
    def __init__(self, page_id=None, access_token=None, api_version=None, session=None):
        self.page_id = page_id or os.getenv("FACEBOOK_PAGE_ID")
        self.access_token = access_token or os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
        self.api_version = api_version or os.getenv("META_GRAPH_API_VERSION", "v26.0")
        self.session = session if session is not None else requests.Session()
        if not self.page_id or not self.access_token:
            raise ValueError("Facebook Page ID and token are required")

    @property
    def endpoint(self):
        return f"https://graph-video.facebook.com/{self.api_version}/{self.page_id}/videos"

    def headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    def _post(self, data, files=None, timeout=120):
        response = self.session.post(self.endpoint, headers=self.headers(), data=data, files=files, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def upload(self, video_path, title, description):
        path = Path(video_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        size = path.stat().st_size
        started = self._post({"upload_phase": "start", "file_size": str(size)})
        video_id, session_id = started.get("video_id"), started.get("upload_session_id")
        if not video_id or not session_id:
            raise RuntimeError("Meta did not return a video ID and upload session")
        start, end = int(started["start_offset"]), int(started["end_offset"])
        with path.open("rb") as source:
            while start < end:
                if end - start > CHUNK_LIMIT or end > size:
                    raise RuntimeError("Unexpected Meta upload chunk range")
                source.seek(start)
                moved = self._post({"upload_phase": "transfer", "upload_session_id": session_id, "start_offset": str(start)},
                    files={"video_file_chunk": ("chunk", source.read(end - start), "application/octet-stream")}, timeout=900)
                next_start, next_end = int(moved["start_offset"]), int(moved["end_offset"])
                if next_start <= start and next_end <= end:
                    raise RuntimeError("Meta upload did not advance")
                start, end = next_start, next_end
        result = self._post({"upload_phase": "finish", "upload_session_id": session_id,
                             "title": title[:250], "description": description})
        if not result.get("success"):
            raise RuntimeError("Meta did not confirm the finished upload")
        return {"platform": "facebook", "video_id": str(video_id), "url": None, "status": "submitted", "response": result}
