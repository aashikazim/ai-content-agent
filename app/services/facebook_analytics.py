
import os
import requests


class FacebookAnalyticsService:
    def __init__(
        self,
        access_token=None,
        api_version=None,
        session=None
    ):
        self.access_token = (
            access_token
            or os.getenv(
                "FACEBOOK_PAGE_ACCESS_TOKEN"
            )
        )

        self.api_version = (
            api_version
            or os.getenv(
                "META_GRAPH_API_VERSION",
                "v26.0"
            )
        )

        self.session = (
            session if session is not None
            else requests.Session()
        )

        if not self.access_token:
            raise ValueError(
                "Facebook Page access token missing"
            )

    def get_video_details(
        self,
        video_id,
        fields="id"
    ):
        url = (
            f"https://graph.facebook.com/"
            f"{self.api_version}/{video_id}"
        )

        response = self.session.get(
            url,
            headers={
                "Authorization": (
                    f"Bearer {self.access_token}"
                )
            },
            params={"fields": fields},
            timeout=30
        )

        response.raise_for_status()
        return response.json()

    def get_insights(
        self,
        video_id,
        metric_names
    ):
        if not metric_names:
            raise ValueError(
                "At least one supported metric "
                "must be specified"
            )

        url = (
            f"https://graph.facebook.com/"
            f"{self.api_version}/"
            f"{video_id}/video_insights"
        )

        response = self.session.get(
            url,
            headers={
                "Authorization": (
                    f"Bearer {self.access_token}"
                )
            },
            params={
                "metric": ",".join(metric_names)
            },
            timeout=30
        )

        response.raise_for_status()
        return response.json()
