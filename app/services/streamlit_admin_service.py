"""Server-side session client for the Streamlit generation page.

The browser receives neither API keys nor the admin session cookie. This client
has no retry policy: an uncertain mutation must be checked by the administrator.
"""

import os
from urllib.parse import urlsplit
from uuid import UUID

import requests


class ContentAPIError(Exception):
    def __init__(self, message, *, unauthenticated=False, uncertain=False):
        super().__init__(message)
        self.unauthenticated = unauthenticated
        self.uncertain = uncertain


class StreamlitAdminClient:
    """One requests session per Streamlit session, never shared through a cache."""

    def __init__(self, api_url=None, session=None):
        value = (
            api_url if api_url is not None
            else os.getenv("CONTENT_API_URL", "http://127.0.0.1:8000")
        ).rstrip("/")
        try:
            parsed = urlsplit(value)
            parsed.port  # Reject malformed ports; standard ports may be omitted.
            valid = (
                parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            valid = False
        if not valid:
            raise ContentAPIError("CONTENT_API_URL must be an HTTP or HTTPS server URL without credentials.")
        self.api_url = value
        self.session = session if session is not None else requests.Session()
        # Avoid environment proxy/netrc credentials on these local admin calls.
        self.session.trust_env = False
        self.csrf_token = None

    def _request(self, method, path, body=None, *, csrf=True, uncertain=False):
        headers = {}
        if method != "GET" and csrf:
            if not self.csrf_token:
                raise ContentAPIError("Your session has expired. Sign in again.", unauthenticated=True)
            headers["X-CSRF-Token"] = self.csrf_token
        try:
            response = self.session.request(
                method,
                f"{self.api_url}/api/v1{path}",
                json=body,
                headers=headers,
                timeout=(3.05, 15),
                allow_redirects=False,
            )
        except requests.RequestException:
            message = (
                "The request outcome is uncertain. Check existing jobs before submitting again."
                if uncertain else "Cannot reach the local content API. Check that the API is running."
            )
            # Do not include exception text: it may contain URLs or credentials.
            raise ContentAPIError(message, uncertain=uncertain) from None
        if response.status_code in {401, 403}:
            self.forget_session()
            raise ContentAPIError(
                "Sign-in failed or your session has expired. Sign in again.",
                unauthenticated=True,
            )
        if response.status_code == 429:
            raise ContentAPIError("Too many sign-in attempts. Please try again later.")
        if not 200 <= response.status_code < 300:
            # A server failure after job creation may still have queued a job.
            is_uncertain = uncertain and response.status_code >= 500
            detail = None
            if not is_uncertain:
                try:
                    detail = response.json().get("detail")
                except (ValueError, AttributeError):
                    pass
            raise ContentAPIError(
                "The request outcome is uncertain. Check existing jobs before submitting again."
                if is_uncertain else detail[:500] if isinstance(detail, str) else "The content API could not complete this request. Check the API and selected settings.",
                uncertain=is_uncertain,
            )
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, requests.JSONDecodeError):
            raise ContentAPIError(
                "The content API returned an unexpected response. Check existing jobs before submitting again."
                if uncertain else "The content API returned an unexpected response.",
                uncertain=uncertain,
            ) from None
        return payload

    def get_session(self):
        payload = self._request("GET", "/auth/session")
        self.csrf_token = payload.get("csrf_token") if payload.get("authenticated") else None
        if not payload.get("authenticated"):
            self.session.cookies.clear()
        return payload

    def login(self, username, password):
        payload = self._request(
            "POST", "/auth/login", {"username": username, "password": password}, csrf=False
        )
        self.csrf_token = payload.get("csrf_token")
        return payload

    def forget_session(self):
        self.csrf_token = None
        self.session.cookies.clear()

    def logout(self):
        try:
            return self._request("POST", "/auth/logout")
        finally:
            self.forget_session()

    def catalog(self):
        return self._request("GET", "/generation/catalog")

    def configurations(self):
        return self._request("GET", "/generation/configurations").get("configurations", [])

    def preflight(self, configuration):
        return self._request("POST", "/generation/preflight", {"configuration": configuration})

    def save_configuration(self, configuration):
        return self._request("POST", "/generation/configurations", {"configuration": configuration})

    def create_job(self, configuration, *, category, region, limit, authorization=None):
        payload = self._request(
            "POST", "/jobs",
            {
                "category": category,
                "region": region,
                "limit": limit,
                "image_mode": configuration["image_mode"],
                "generation_config": configuration,
                **({"script_quote_id": authorization["quote_id"], "script_cost_confirmed": True}
                   if authorization else {}),
            },
            uncertain=True,
        )
        try:
            UUID(payload["job_id"])
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ContentAPIError(
                "The content API did not confirm a job ID. Check existing jobs before submitting again.",
                uncertain=True,
            ) from None
        return payload

    def get_job(self, job_id):
        return self._request("GET", f"/jobs/{job_id}")

    def script_quote(self, configuration, context):
        return self._request("POST", "/generation/script-quotes", {"configuration": configuration, **context})

    def scripts(self):
        return self._request("GET", "/generation/scripts").get("attempts", [])

    def openai_models(self):
        return self._request("GET", "/generation/openai-models").get("models", [])

    def create_script(self, configuration, context, authorization=None):
        result = self._request("POST", "/generation/scripts", {
            "configuration": configuration, **context, **(authorization or {}),
        }, uncertain=True)
        try:
            UUID(result["attempt_id"])
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ContentAPIError("Script submission was not acknowledged. Inspect script history before another request.", uncertain=True) from None
        return result

    def reconcile_script(self, identifier, notes, confirmed):
        return self._request("POST", f"/generation/scripts/{identifier}/reconcile", {
            "notes": notes, "confirmed": confirmed,
        })
