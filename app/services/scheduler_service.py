
import os
import requests


class SchedulerService:
    def __init__(
        self,
        api_url=None,
        api_key=None,
        session=None
    ):
        self.api_url = (
            api_url
            or os.getenv(
                "CONTENT_API_URL",
                "http://127.0.0.1:8000"
            )
        ).rstrip("/")

        self.api_key = (
            api_key
            if api_key is not None
            else os.getenv("CONTENT_API_KEY")
        )

        self.session = (
            session
            if session is not None
            else requests.Session()
        )

    def create_generation_job(
        self,
        category="science",
        region="US",
        limit=5,
        image_mode="text"
    ):
        if not self.api_key:
            raise ValueError(
                "CONTENT_API_KEY is missing"
            )

        if limit < 1 or limit > 50:
            raise ValueError(
                "limit must be between 1 and 50"
            )

        response = self.session.post(
            f"{self.api_url}/api/v1/jobs",
            headers={
                "X-API-Key": self.api_key
            },
            json={
                "category": category,
                "region": region,
                "limit": limit,
                "image_mode": image_mode
            },
            timeout=30
        )

        response.raise_for_status()
        return response.json()
