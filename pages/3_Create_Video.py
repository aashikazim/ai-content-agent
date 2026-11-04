"""Configure generation through the existing local API and single admin login."""

import streamlit as st
from dotenv import load_dotenv

from app.agents.trend_agent import CATEGORIES
from app.services.streamlit_admin_service import ContentAPIError, StreamlitAdminClient
from app.services.streamlit_script_workshop import paid_authorization, script_workshop
from app.services.streamlit_cloud_production import cloud_production


load_dotenv(".env")
st.set_page_config(page_title="Create Video", page_icon="🎬", layout="wide")
st.title("Create Video")
st.caption("Choose a format, configure your models, and save settings or start local generation.")


def api_error(error):
    st.error(str(error))
    if error.unauthenticated:
        st.session_state.pop("_create_video_initialized", None)
    st.stop()


if "_create_video_api" not in st.session_state:
    try:
        st.session_state["_create_video_api"] = StreamlitAdminClient()
    except ContentAPIError as error:
        api_error(error)
api = st.session_state["_create_video_api"]
if notice := st.session_state.pop("_create_video_logout_notice", None):
    st.warning(notice)

# Revalidate on every rerun; the API owns expiry, credential reset, and logout.
try:
    session = api.get_session()
except ContentAPIError as error:
    api_error(error)

if not session.get("configured"):
    st.info("Set up the single admin account locally before using Create Video.")
    st.code("python scripts/setup_admin.py --username admin", language="bash")
    st.stop()

if not session.get("authenticated"):
    st.session_state.pop("_create_video_initialized", None)
    st.info("Sign in with your existing admin account. No API key is needed.")
    with st.form("create_video_login", clear_on_submit=True):
        username = st.text_input("Username", value="admin", max_chars=64)
        password = st.text_input("Password", type="password", max_chars=1024)
        signed_in = st.form_submit_button("Sign in", type="primary")
    if signed_in:
        try:
            api.login(username, password)
        except ContentAPIError as error:
            api_error(error)
        st.rerun()
    st.stop()

identity, sign_out = st.columns([4, 1])
identity.caption(f"Signed in as {session.get('username', 'admin')}")
if sign_out.button("Sign out", use_container_width=True):
    try:
        api.logout()
    except ContentAPIError:
        # Local cookies are cleared even when server-side revocation is unknown.
        st.session_state["_create_video_logout_notice"] = (
            "Signed out locally. The API could not confirm session revocation."
        )
    st.session_state.pop("_create_video_initialized", None)
    st.rerun()

try:
    catalog = api.catalog()
    drafts = api.configurations()
except ContentAPIError as error:
    api_error(error)

defaults = catalog["defaults"]


def apply_configuration(configuration):
    """Run before widgets so loading a draft never triggers a generation job."""
    merged = {**defaults, **configuration}
    for field, value in merged.items():
        if field not in {"script_model", "video_model", "max_cost_usd"}:
            st.session_state[f"cv_{field}"] = value
    st.session_state["cv_max_cost_usd"] = float(merged["max_cost_usd"])
    for kind in ("script", "video"):
        st.session_state[f"cv_{kind}_model_{merged[f'{kind}_provider']}"] = merged[f"{kind}_model"]
    st.session_state["_create_video_initialized"] = True


if not st.session_state.get("_create_video_initialized"):
    apply_configuration(defaults)

if st.session_state.get("_create_video_success"):
    st.success(st.session_state["_create_video_success"])
if st.session_state.get("_create_video_last_job"):
    job_id = st.session_state["_create_video_last_job"]
    st.caption(f"Most recent generation job: {job_id}. Follow its progress in the React studio or review queue.")
    if st.button("Refresh job status"):
        try:
            job = api.get_job(job_id)
            st.info(f"Generation status: {job.get('status', 'unknown')}")
        except ContentAPIError as error:
            api_error(error)

st.info(
    "Local illustrated previews support trends, topics and supplied scripts. OpenAI script generation requires "
    "a configured key, account model check and explicit cost confirmation. cloud production below uses moving "
    "clips and character references. Long drama uses Runway with 120–1200 seconds and up to240shots. News still requires human verification. Saving settings never starts generation."
)

with st.expander("Saved settings", expanded=bool(drafts)):
    by_id = {draft["config_id"]: draft for draft in drafts}

    def draft_label(config_id):
        if not config_id:
            return "Choose saved settings"
        draft = by_id[config_id]
        config = draft.get("configuration", draft.get("config", {}))
        return f"{config.get('content_type', 'Video')} · {draft.get('created_at', '')[:19]} · {config_id[:8]}"

    selected_draft = st.selectbox(
        "Saved configuration", [None, *by_id], format_func=draft_label, key="cv_saved_draft"
    )
    if st.button("Load settings", disabled=not selected_draft):
        saved = by_id[selected_draft]
        apply_configuration(saved.get("configuration", saved.get("config", {})))
        st.session_state["_create_video_success"] = "Saved settings loaded. Generation has not started."

st.subheader("1. Format and source")
formats = {item["id"]: item for item in catalog["content_types"]}
def change_content_type():
    long = st.session_state['cv_content_type'] == 'long_drama'
    st.session_state['cv_duration_seconds'] = max(120, st.session_state.get('cv_duration_seconds',300)) if long else min(60,st.session_state.get('cv_duration_seconds',45))
    st.session_state['cv_max_shots'] = max(60,st.session_state.get('cv_max_shots',12)) if long else min(12,st.session_state.get('cv_max_shots',12))

content_type = st.selectbox(
    "Content type", list(formats), key="cv_content_type", on_change=change_content_type,
    format_func=lambda value: f"{formats[value]['label']} · {formats[value].get('status', 'planned').replace('_', ' ')}",
)
st.caption(formats[content_type].get("description", ""))
source_mode = st.radio(
    "Source", ["trends", "topic", "script"], key="cv_source_mode", horizontal=True,
    format_func=lambda value: {"trends": "Trending topics", "topic": "Your topic", "script": "Your script"}[value],
)
if source_mode == "topic":
    st.text_area("Topic", key="cv_topic", max_chars=1000)
elif source_mode == "script":
    st.text_area("Script", key="cv_supplied_script", max_chars=20000, height=180)

trend_columns = st.columns([2, 1, 1])
category = trend_columns[0].selectbox("Trend category", ["mixed", *CATEGORIES], key="cv_trend_category")
region = trend_columns[1].text_input("Region", value="US", max_chars=2, key="cv_region").upper()
limit = trend_columns[2].number_input("Trend results", min_value=1, max_value=50, value=20, key="cv_limit")

st.subheader("2. Models")
script_column, video_column = st.columns(2)


def model_controls(kind, column):
    providers = {provider["id"]: provider for provider in catalog[f"{kind}_providers"]}
    selected_provider = st.session_state.get(f"cv_{kind}_provider")
    if selected_provider and selected_provider not in providers:
        providers[selected_provider] = {"label":selected_provider + " · retired", "models": [{"id": st.session_state.get(f"cv_{kind}_model_{selected_provider}", "unavailable")}], "reason":"Retired provider; explicitly select Google or local", "implemented":False}
    with column:
        provider_id = st.selectbox(
            f"{kind.title()} provider", list(providers), key=f"cv_{kind}_provider",
            format_func=lambda value: providers[value]["label"],
        )
        provider = providers[provider_id]
        models = {model["id"]: model for model in provider["models"]}
        key = f"cv_{kind}_model_{provider_id}"
        if key not in st.session_state:
            st.session_state[key] = next(iter(models))
        # Preserve a stale model from a saved draft so preflight can reject it
        # explicitly instead of substituting a different model silently.
        selected = st.session_state[key]
        if selected not in models:
            models[selected] = {"id": selected, "label": selected, "status": "unavailable"}
        model_id = st.selectbox(
            f"{kind.title()} model", list(models), key=key,
            format_func=lambda value: f"{models[value].get('label', value)} · {models[value].get('status', 'planned').replace('_', ' ')}",
        )
        configured = "configured" if provider.get("configured") else "not configured"
        readiness = "implemented" if provider.get("implemented") else "planned integration"
        st.caption(f"{provider['label']}: {configured} · {readiness}.")
        if provider.get("reason"):
            st.caption(provider["reason"])
        selected_model = models[model_id]
        if selected_model.get("verified_on"):
            st.caption(f"Catalogue verified {selected_model['verified_on']}; account access has not been checked.")
        return provider_id, model_id


script_provider, script_model = model_controls("script", script_column)
video_provider, video_model = model_controls("video", video_column)

video_input_mode = st.selectbox("Runway input mode" if video_provider == 'runway' else 'Local cinematic input mode', ["text", "image"], key="cv_video_input_mode",
    format_func=lambda value: "Text to video" if value == "text" else "Image to video") if video_provider in {"runway", "local_cinematic"} else "text"
if video_provider == 'local_cinematic':
    st.info('Use the production panel below after the script workshop. LTX/hybrid support text or composed scene frames. Wan-only requires image mode, one actor per shot and a permitted driving video. Models run in a separate environment; native 512px short-edge output is upscaled by FFmpeg.')
if video_provider == "runway":
    st.caption("Gen-4 Turbo needs image input. Gen-4 and Kling 2.5 require local narration. Select scene start frames in the production panel after generating the script. Native model resolution differs from final FFmpeg output.")

st.subheader("3. Look and delivery")
look = st.columns(3)
styles = {style["id"]: style for style in catalog["styles"]}
visual_style = look[0].selectbox(
    "Visual style", list(styles), key="cv_visual_style", format_func=lambda value: styles[value]["label"]
)
image_mode = look[1].selectbox(
    "Local image mode", ["text", "diffusion"], key="cv_image_mode",
    format_func=lambda value: {"text": "Text cards · baseline", "diffusion": "Generated images · experimental"}[value],
)
image_models = list(dict.fromkeys([
    *[model["id"] for model in catalog.get("image_models", [])],
    defaults["image_model"], st.session_state["cv_image_model"],
]))
image_model = look[2].selectbox("Local image model", image_models, key="cv_image_model")

delivery = st.columns(4)
duration = delivery[0].number_input("Target seconds", min_value=30, max_value=1200, step=1, key="cv_duration_seconds")
aspect_ratio = delivery[1].selectbox("Aspect ratio", catalog["aspect_ratios"], key="cv_aspect_ratio")
resolution = delivery[2].selectbox("Resolution", catalog["resolutions"], key="cv_resolution")
fps = delivery[3].selectbox("Frames per second", catalog["fps"], key="cv_fps")
sound = st.columns(2)
narration = sound[0].selectbox(
    "Narration", ["local_tts", "model_audio"], key="cv_narration",
    format_func=lambda value: {"local_tts": "Local TTS · current baseline", "model_audio": "Selected model audio"}[value],
)
captions = sound[1].selectbox(
    "Captions", ["scene_text", "timed"], key="cv_captions",
    format_func=lambda value: {"scene_text": "Scene text · current baseline", "timed": "Timed phrases · cloud assembly"}[value],
)
with st.expander("Limits and character references"):
    max_shots = st.number_input("Maximum shots", min_value=1, max_value=240 if content_type == "long_drama" else 12, key="cv_max_shots")
    max_cost = st.number_input("Maximum cost (USD)", min_value=0.0, max_value=1000.0, step=1.0, key="cv_max_cost_usd")
    require_motion = st.checkbox("Require motion footage", key="cv_require_motion")
    st.caption("Upload character references and select them in the cloud production panel below. Character IDs must match the completed script.")
    if st.session_state.get("cv_character_reference_ids"):
        st.caption("Character-reference video generation is planned; these references are saved only.")

configuration = {
    **defaults,
    "content_type": content_type,
    "script_provider": script_provider,
    "script_model": script_model,
    "video_provider": video_provider,
    "video_model": video_model,
    "video_input_mode": video_input_mode,
    "start_image_reference_id": None,
    "shot_image_reference_ids": {},
    "image_mode": image_mode,
    "image_model": image_model,
    "duration_seconds": int(duration),
    "aspect_ratio": aspect_ratio,
    "visual_style": visual_style,
    "resolution": resolution,
    "fps": fps,
    "narration": narration,
    "source_mode": source_mode,
    "topic": st.session_state.get("cv_topic", "") if source_mode == "topic" else "",
    "supplied_script": st.session_state.get("cv_supplied_script", "") if source_mode == "script" else "",
    "character_reference_ids": st.session_state.get("cv_character_reference_ids", []),
    "captions": captions,
    "max_shots": int(max_shots),
    "max_cost_usd": str(max_cost),
    "require_motion": require_motion,
    # Saved settings never authorize a paid request; use a fresh cost quote below.
    "paid_generation_confirmed": False,
}

st.divider()
st.subheader("4. Check and create")
try:
    preflight = api.preflight(configuration)
except ContentAPIError as error:
    api_error(error)
for warning in preflight.get("warnings", []):
    st.warning(warning)
for reason in preflight.get("errors", []):
    st.warning(reason)
executable = preflight.get("executable", False)
if video_provider != "local":
    executable = False
    st.info("Use the script workshop, then the production panel below for local cinematic or cloud moving footage.")
if executable:
    st.success("These settings can run locally. Generation will stop at manual review.")
elif video_provider == 'local':
    st.info("Generation is unavailable for these settings. Planned integrations can be saved as drafts after any validation issues are resolved.")
st.caption("Paid scripts require separate paid authorization. Approval and publishing remain separate manual actions.")

video_authorization = None
if catalog.get("phase", 1) >= 2 and script_provider in {"openai", "anthropic"} and video_provider == "local":
    st.subheader("Authorize this video's script cost")
    video_authorization = paid_authorization(api, configuration, {
        "category": category, "region": region, "limit": int(limit), "purpose": "video"
    }, "video_script")

if st.session_state.get("_create_video_uncertain"):
    st.error("The previous generation request has an uncertain outcome. Check the studio job list before creating another job.")
    if st.button("I checked existing jobs; allow a new request"):
        st.session_state.pop("_create_video_uncertain", None)
        st.rerun()

save, create = st.columns(2)
if save.button("Save settings", use_container_width=True):
    try:
        api.save_configuration(configuration)
    except ContentAPIError as error:
        api_error(error)
    st.session_state["_create_video_success"] = "Settings saved. No generation started."
    st.rerun()

if create.button(
    "Start local generation", type="primary", use_container_width=True,
    disabled=not executable or bool(st.session_state.get("_create_video_uncertain")) or (
        script_provider in {"openai", "anthropic"} and not video_authorization
    ),
):
    try:
        # Recheck at the moment of submission; no cached approval of settings.
        latest = api.preflight(configuration)
        if not latest.get("executable"):
            st.error("These settings can no longer execute. Check the configuration and try again.")
            st.stop()
        if len(region) != 2 or not region.isascii() or not region.isalpha():
            st.error("Region must be a two-letter country code.")
            st.stop()
        options = {"authorization": video_authorization} if video_authorization else {}
        created = api.create_job(configuration, category=category, region=region, limit=int(limit), **options)
    except ContentAPIError as error:
        if error.uncertain:
            st.session_state["_create_video_uncertain"] = True
            st.rerun()
        api_error(error)
    st.session_state["_create_video_last_job"] = created["job_id"]
    st.session_state["_create_video_success"] = "Generation queued. Review and publishing still require separate manual actions."
    st.rerun()

if catalog.get("phase", 1) >= 2:
    script_workshop(api, configuration, category, region, int(limit))

if catalog.get("phase", 1) >= 3:
    cloud_production(api, catalog)
