"""Streamlit uses the authenticated API; no live models or uploads are invoked."""

from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests
from streamlit.testing.v1 import AppTest
from streamlit.proto.TextInput_pb2 import TextInput

from app.models.generation_config import GenerationConfig
from app.services.provider_catalog import get_catalog, validate_for_execution
from app.services.streamlit_admin_service import ContentAPIError, StreamlitAdminClient


PAGE = Path(__file__).resolve().parents[1] / "pages" / "3_Create_Video.py"
COOKIE_SECRET = "test-private-session-cookie"
CSRF_SECRET = "test-private-csrf-token"
CLOUD_SECRET = "test-private-cloud-key"


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def json(self):
        return self.payload


class FakeCookies:
    def __init__(self):
        self.value = COOKIE_SECRET

    def clear(self):
        self.value = None


class FakeSession:
    """An API contract fake, with the real shared preflight validator."""

    def __init__(self, *, authenticated=True, configured=True):
        self.authenticated = authenticated
        self.configured = configured
        self.cookies = FakeCookies()
        self.calls = []
        self.drafts = []
        self.attempts = []
        self.fail_creation = False
        self.fail_cloud_creation = False
        self.block_next_preflight = False

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path.removeprefix("/api/v1")
        body = deepcopy(kwargs.get("json"))
        self.calls.append((method, path, {**kwargs, "json": body}))
        if path == "/auth/session":
            return FakeResponse({
                "authenticated": self.authenticated,
                "configured": self.configured,
                **({"username": "admin", "csrf_token": CSRF_SECRET, "expires_at": 9999999999} if self.authenticated else {}),
            })
        if path == "/auth/login":
            if body != {"username": "admin", "password": "test-password"}:
                return FakeResponse({"detail": "Never render " + COOKIE_SECRET}, status=401)
            self.authenticated = True
            self.cookies.value = COOKIE_SECRET
            return FakeResponse({"authenticated": True, "configured": True, "username": "admin", "csrf_token": CSRF_SECRET})
        if not self.authenticated:
            return FakeResponse({"detail": COOKIE_SECRET}, status=401)
        if method == "POST":
            assert kwargs["headers"] == {"X-CSRF-Token": CSRF_SECRET}
        if path == "/auth/logout":
            self.authenticated = False
            return FakeResponse({"authenticated": False})
        if path == "/generation/scripts" and method == "GET":
            return FakeResponse({"attempts": deepcopy(self.attempts)})
        if path == "/generation/openai-models":
            return FakeResponse({"models": ["gpt-6.1-sol"]})
        if path == "/generation/script-quotes":
            return FakeResponse({"paid": True, "maximum_cost_usd": "0.21", "quote_id": "ba7fb41f-eafa-493d-9b28-31358dc98847"})
        if path == "/generation/scripts" and method == "POST":
            return FakeResponse({"attempt_id": "ba7fb41f-eafa-493d-9b28-31358dc98848", "status": "queued"}, 202)
        if path == "/generation/references" and method == "GET":
            return FakeResponse({"references": []})
        if path == "/generation/videos" and method == "GET":
            return FakeResponse({"productions": []})
        if path == "/generation/video-quotes":
            return FakeResponse({"quote_id":"ba7fb41f-eafa-493d-9b28-31358dc98847","estimate_usd":"5.65","projected_estimate_usd":"5.86","shot_count":5,"notice":"Estimate only; no automatic retries"})
        if path == "/generation/runway/credits":
            return FakeResponse({'available_credits': 1234})
        if path == "/generation/videos" and method == "POST":
            if self.fail_cloud_creation:
                raise requests.Timeout("Raw exception with " + CLOUD_SECRET)
            return FakeResponse({"production_id":"ba7fb41f-eafa-493d-9b28-31358dc98846","job_id":"ba7fb41f-eafa-493d-9b28-31358dc98845","status":"queued"},202)
        if path == "/generation/catalog":
            return FakeResponse(get_catalog())
        if path == "/generation/configurations" and method == "GET":
            return FakeResponse({"configurations": deepcopy(self.drafts)})
        if path == "/generation/configurations":
            saved = {"config_id": "34c3f1aa-2b07-4929-98d0-d4771670efbc", "config": body["configuration"], "created_at": "2026-11-03T10:00:00Z"}
            self.drafts.append(saved)
            return FakeResponse(saved)
        if path == "/generation/preflight":
            errors = []
            try:
                validate_for_execution(GenerationConfig.model_validate(body["configuration"]))
            except ValueError as exc:
                errors.append(str(exc))
            if self.block_next_preflight:
                errors.append("Runtime settings changed")
                self.block_next_preflight = False
            return FakeResponse({"executable": not errors, "errors": errors, "warnings": [], "paid_request": False})
        if path == "/jobs":
            if self.fail_creation:
                raise requests.Timeout("A raw exception containing " + CLOUD_SECRET)
            return FakeResponse({"job_id": "5afcf54a-8c46-4511-8a14-6c79eca70d60", "status": "queued"}, status=202)
        if path.startswith("/jobs/"):
            return FakeResponse({"job_id": path.split("/")[-1], "status": "pending_review"})
        raise AssertionError(f"Unexpected API call {method} {path}")


@pytest.fixture
def api_session(monkeypatch):
    monkeypatch.setenv("CONTENT_API_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    monkeypatch.setenv("IMAGE_MODEL_ID", "stable-diffusion-v1-5/stable-diffusion-v1-5")
    monkeypatch.setenv("OPENAI_API_KEY", CLOUD_SECRET)
    monkeypatch.setenv("GEMINI_API_KEY", CLOUD_SECRET)
    monkeypatch.setenv("KLING_API_KEY", CLOUD_SECRET)
    session = FakeSession()
    monkeypatch.setattr("app.services.streamlit_admin_service.requests.Session", lambda: session)
    return session


def page():
    app = AppTest.from_file(str(PAGE), default_timeout=15).run()
    assert not app.exception
    return app


def button(app, label):
    return next(element for element in app.button if element.label == label)


def visible_text(app):
    return "\n".join(str(element.value) for element in [*app.markdown, *app.caption, *app.info, *app.warning, *app.error, *app.success])


def job_calls(session):
    return [call for call in session.calls if call[:2] == ("POST", "/jobs")]


def test_login_gates_controls_and_hides_password(api_session):
    api_session.authenticated = False
    app = page()
    assert [element.label for element in app.text_input] == ["Username", "Password"]
    assert app.text_input[1].proto.type == TextInput.PASSWORD
    assert not app.selectbox
    assert not job_calls(api_session)
    app.text_input[1].set_value("test-password")
    button(app, "Sign in").click().run()
    assert not app.exception
    assert app.selectbox(key="cv_content_type").value == "realistic_short"
    assert all(element.label != "Password" for element in app.text_input)
    assert not job_calls(api_session)
    assert COOKIE_SECRET not in visible_text(app)
    assert CSRF_SECRET not in visible_text(app)


def test_setup_required_page_has_no_registration_or_generation(api_session):
    api_session.authenticated = False
    api_session.configured = False
    app = page()
    assert "scripts/setup_admin.py" in app.code[0].value
    assert not app.text_input
    assert not app.button
    assert len(api_session.calls) == 1


def test_cloud_options_are_implemented_but_local_button_cannot_submit_them(api_session):
    app = page()
    assert len(app.selectbox(key="cv_content_type").options) == 5
    app.selectbox(key="cv_script_provider").select("openai").run()
    app.selectbox(key="cv_video_provider").select("google").run()
    assert not app.exception
    assert app.selectbox(key="cv_script_model_openai").value == "gpt-6.1-sol"
    assert app.selectbox(key="cv_video_model_google").value == "gemini-omni-1.1-flash"
    assert "configured · implemented" in visible_text(app)
    assert button(app, "Start local generation").disabled
    assert not job_calls(api_session)
    for secret in (COOKIE_SECRET, CSRF_SECRET, CLOUD_SECRET):
        assert secret not in visible_text(app)


def test_provider_selections_are_independent_and_survive_other_edits(api_session):
    app = page()
    app.selectbox(key="cv_script_provider").select("openai").run()
    assert app.selectbox(key="cv_video_provider").value == "local"
    app.selectbox(key="cv_video_provider").select("google").run()
    app.selectbox(key="cv_visual_style").select("comic_book").run()
    assert app.selectbox(key="cv_script_model_openai").value == "gpt-6.1-sol"
    assert app.selectbox(key="cv_video_model_google").value == "gemini-omni-1.1-flash"
    app.selectbox(key="cv_script_provider").select("ollama").run()
    assert app.selectbox(key="cv_video_provider").value == "google"
    assert app.selectbox(key="cv_video_model_google").value == "gemini-omni-1.1-flash"
    assert not job_calls(api_session)


def test_local_settings_are_sent_as_nested_generation_configuration(api_session):
    app = page()
    app.selectbox(key="cv_image_mode").select("diffusion")
    app.selectbox(key="cv_aspect_ratio").select("16:9")
    app.selectbox(key="cv_resolution").select("720p")
    app.selectbox(key="cv_fps").select(24)
    app.selectbox(key="cv_visual_style").select("sketch")
    app.number_input(key="cv_duration_seconds").set_value(55)
    app.number_input(key="cv_max_shots").set_value(8)
    button(app, "Start local generation").click().run()
    assert not app.exception
    assert len(job_calls(api_session)) == 1
    _, _, sent = job_calls(api_session)[0]
    assert sent["json"]["image_mode"] == "diffusion"
    config = sent["json"]["generation_config"]
    assert {field: config[field] for field in ("image_mode", "aspect_ratio", "resolution", "fps", "visual_style", "duration_seconds", "max_shots")} == {
        "image_mode": "diffusion", "aspect_ratio": "16:9", "resolution": "720p", "fps": 24,
        "visual_style": "sketch", "duration_seconds": 55, "max_shots": 8,
    }
    assert config["paid_generation_confirmed"] is False
    assert sent["headers"] == {"X-CSRF-Token": CSRF_SECRET}
    assert sent["timeout"] == (3.05, 15)
    assert sent["allow_redirects"] is False
    app.run()
    assert len(job_calls(api_session)) == 1
    assert not any("publish" in call[1] or "review" in call[1] for call in api_session.calls)


def test_planned_settings_can_be_saved_and_loaded_without_execution(api_session):
    app = page()
    app.selectbox(key="cv_content_type").select("short_drama").run()
    app.selectbox(key="cv_script_provider").select("openai").run()
    app.selectbox(key="cv_video_provider").select("google").run()
    app.selectbox(key="cv_fps").select(24)
    app.number_input(key="cv_duration_seconds").set_value(120)
    button(app, "Save settings").click().run()
    assert not app.exception
    assert api_session.drafts[0]["config"]["video_provider"] == "google"
    assert api_session.drafts[0]["config"]["duration_seconds"] == 120
    assert not job_calls(api_session)
    app.selectbox(key="cv_content_type").select("realistic_short").run()
    app.selectbox(key="cv_script_provider").select("ollama").run()
    app.selectbox(key="cv_video_provider").select("local").run()
    app.selectbox(key="cv_saved_draft").select(api_session.drafts[0]["config_id"]).run()
    button(app, "Load settings").click().run()
    assert not app.exception
    assert app.selectbox(key="cv_content_type").value == "short_drama"
    assert app.selectbox(key="cv_script_provider").value == "openai"
    assert app.selectbox(key="cv_video_provider").value == "google"
    assert app.number_input(key="cv_duration_seconds").value == 120
    assert button(app, "Start local generation").disabled
    assert not job_calls(api_session)


def test_creation_timeout_blocks_rerun_and_requires_manual_check(api_session):
    api_session.fail_creation = True
    app = page()
    button(app, "Start local generation").click().run()
    assert not app.exception
    assert len(job_calls(api_session)) == 1
    assert "uncertain outcome" in visible_text(app)
    assert CLOUD_SECRET not in visible_text(app)
    assert button(app, "Start local generation").disabled
    app.run()
    assert len(job_calls(api_session)) == 1
    button(app, "I checked existing jobs; allow a new request").click().run()
    assert not button(app, "Start local generation").disabled
    assert len(job_calls(api_session)) == 1


def test_session_expiry_hides_configuration_and_prevents_calls(api_session):
    app = page()
    api_session.authenticated = False
    app.run()
    assert not app.exception
    assert any(element.label == "Password" for element in app.text_input)
    assert not app.selectbox
    assert not job_calls(api_session)


def test_sign_out_revokes_session_and_returns_to_login(api_session):
    app = page()
    button(app, "Sign out").click().run()
    assert not app.exception
    assert not api_session.authenticated
    assert api_session.cookies.value is None
    assert any(element.label == "Password" for element in app.text_input)
    assert not job_calls(api_session)


@pytest.mark.parametrize("url", [
    "file:///tmp/api", "http://user:password@localhost:8000", "http://localhost:bad",
    "http://localhost:8000/?token=private", "https://localhost:8000/#token", "http://localhost:8000/api",
])
def test_admin_client_rejects_credential_urls_and_redirect_bases(url):
    with pytest.raises(ContentAPIError):
        StreamlitAdminClient(url, session=FakeSession())


def test_admin_client_errors_never_reflect_server_secrets():
    session = FakeSession(authenticated=False)
    client = StreamlitAdminClient("http://127.0.0.1:8000", session=session)
    with pytest.raises(ContentAPIError) as error:
        client.login("admin", "bad-password")
    assert error.value.unauthenticated
    assert COOKIE_SECRET not in str(error.value)


def test_missing_job_acknowledgement_is_uncertain_without_retry(monkeypatch):
    session = FakeSession()
    client = StreamlitAdminClient("http://127.0.0.1:8000", session=session)
    client.get_session()
    calls = []

    def missing_job_id(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse({"unexpected": CLOUD_SECRET}, status=202)

    monkeypatch.setattr(session, "request", missing_job_id)
    with pytest.raises(ContentAPIError) as error:
        client.create_job(GenerationConfig().model_dump(mode="json"), category="mixed", region="US", limit=20)
    assert error.value.uncertain
    assert CLOUD_SECRET not in str(error.value)
    assert len(calls) == 1


def test_script_workshop_local_submit_never_runs_on_rerender(api_session):
    app = page()
    assert not [call for call in api_session.calls if call[:2] == ('POST', '/generation/scripts')]
    button(app, 'Generate script draft').click().run()
    assert not app.exception
    calls = [call for call in api_session.calls if call[:2] == ('POST', '/generation/scripts')]
    assert len(calls) == 1
    assert calls[0][2]['json']['configuration']['script_provider'] == 'ollama'
    assert calls[0][2]['json']['purpose'] == 'script'
    app.run()
    assert len([call for call in api_session.calls if call[:2] == ('POST', '/generation/scripts')]) == 1
    assert not job_calls(api_session)


def test_paid_script_requires_quote_confirmation_and_resets_after_edit(api_session):
    app = page()
    app.selectbox(key='cv_script_provider').select('openai').run()
    assert button(app, 'Generate script draft').disabled
    app.button(key='script_draft_estimate').click().run()
    assert not app.exception
    assert button(app, 'Generate script draft').disabled
    app.checkbox(key='script_draft_confirmed').check().run()
    assert not button(app, 'Generate script draft').disabled
    app.selectbox(key='cv_visual_style').select('sketch').run()
    assert button(app, 'Generate script draft').disabled
    app.button(key='script_draft_estimate').click().run()
    app.checkbox(key='script_draft_confirmed').check().run()
    button(app, 'Generate script draft').click().run()
    assert not app.exception
    calls = [call for call in api_session.calls if call[:2] == ('POST', '/generation/scripts')]
    assert len(calls) == 1
    assert calls[0][2]['json']['confirmed'] is True
    assert calls[0][2]['json']['quote_id']
    assert button(app, 'Generate script draft').disabled
    assert not job_calls(api_session)


def cloud_script(session):
    from tests.test_gemini_video import cloud_production
    identifier = 'ba7fb41f-eafa-493d-9b28-31358dc98849'
    session.attempts = [{'attempt_id':identifier,'model':'gpt-6.1-sol','status':'completed','production_plan':cloud_production().model_dump(mode='json')}]
    return identifier


def test_streamlit_video_requires_separate_quote_and_confirmation(api_session):
    identifier = cloud_script(api_session)
    app = page()
    app.selectbox(key='cloud_script').select(identifier).run()
    button(app,'Estimate Gemini video cost').click().run()
    assert not app.exception
    assert button(app,'Confirm paid video generation').disabled
    assert any(element.value == 'Authorize Gemini video cost' for element in app.subheader)
    assert 'Check the Gemini video authorization checkbox above' in visible_text(app)
    calls = lambda: [call for call in api_session.calls if call[:2] == ('POST','/generation/videos')]
    assert not calls()
    app.checkbox(key='cloud_confirm_ba7fb41f-eafa-493d-9b28-31358dc98847').check().run()
    assert not button(app,'Confirm paid video generation').disabled
    assert 'Video cost authorized' in visible_text(app)
    button(app,'Confirm paid video generation').click().run()
    assert not app.exception
    assert len(calls()) == 1
    assert calls()[0][2]['json']['confirmed'] is True
    app.run()
    assert len(calls()) == 1
    assert not job_calls(api_session)


def test_streamlit_unknown_video_acknowledgement_does_not_retry(api_session):
    identifier = cloud_script(api_session)
    api_session.fail_cloud_creation = True
    app = page()
    app.selectbox(key='cloud_script').select(identifier).run()
    button(app,'Estimate Gemini video cost').click().run()
    app.checkbox(key='cloud_confirm_ba7fb41f-eafa-493d-9b28-31358dc98847').check().run()
    button(app,'Confirm paid video generation').click().run()
    app.run()
    assert not app.exception
    assert 'Submission outcome is unknown' in visible_text(app)
    assert CLOUD_SECRET not in visible_text(app)
    assert button(app,'Confirm paid video generation').disabled
    assert len([call for call in api_session.calls if call[:2] == ('POST','/generation/videos')]) == 1


def test_runway_controls_and_consent_are_independent_of_script_provider(api_session, monkeypatch):
    monkeypatch.setenv('RUNWAYML_API_SECRET', CLOUD_SECRET)
    identifier = cloud_script(api_session)
    api_session.attempts[0]['configuration'] = {'video_provider':'runway', 'video_model':'gen4_turbo', 'video_input_mode':'image'}
    app = page()
    app.selectbox(key='cv_video_provider').select('runway').run()
    app.selectbox(key='cv_video_input_mode').select('image').run()
    assert app.selectbox(key='cv_script_provider').value == 'ollama'
    assert button(app, 'Start local generation').disabled
    app.selectbox(key='cloud_script').select(identifier).run()
    assert any(element.value == 'Scene start frames' for element in app.subheader)
    button(app, 'Check Runway credits').click().run()
    assert 'Runway available credits: 1234' in visible_text(app)
    button(app, 'Estimate Runway video cost').click().run()
    assert button(app, 'Confirm paid video generation').disabled
    assert any(element.value == 'Authorize Runway video cost' for element in app.subheader)
    assert not job_calls(api_session)
    assert not [call for call in api_session.calls if call[:2] == ('POST','/generation/videos')]


def test_streamlit_long_drama_allows_long_duration_and_larger_shot_plan(api_session,monkeypatch):
    monkeypatch.setenv('RUNWAYML_API_SECRET',CLOUD_SECRET)
    app=page()
    app.selectbox(key='cv_content_type').select('long_drama').run()
    app.selectbox(key='cv_video_provider').select('runway').run()
    app.selectbox(key='cv_video_model_runway').select('kling3.0_standard').run()
    app.number_input(key='cv_duration_seconds').set_value(120).run()
    app.number_input(key='cv_max_shots').set_value(30).run()
    assert not app.exception
    button(app,'Save settings').click().run()
    assert not app.exception
    config=api_session.drafts[-1]['config']
    assert config['content_type']=='long_drama' and config['duration_seconds']==120 and config['max_shots']==30
    assert button(app,'Start local generation').disabled
    assert not job_calls(api_session)
