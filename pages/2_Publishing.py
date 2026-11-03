
import os
import hmac
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from app.services.job_service import JobService
from app.services.publishing_service import PublishingService
from app.services.publishing_storage import PublishingStorage
from app.services.content_storage import ContentStorage

load_dotenv(".env")

st.set_page_config(
    page_title="Manual Publishing",
    page_icon="📤",
    layout="wide"
)

st.title("Manual Publishing")
st.warning(
    "Publishing is never automatic. "
    "You must explicitly authorize each upload."
)

expected = os.getenv(
    "CONTENT_PUBLISH_API_KEY", ""
)

if not expected:
    st.error(
        "CONTENT_PUBLISH_API_KEY is not configured."
    )
    st.stop()

entered = st.text_input(
    "Publishing access key",
    type="password"
)

if not entered or not hmac.compare_digest(
    entered,
    expected
):
    st.info("Enter the publishing key.")
    st.stop()


jobs = JobService()
storage = PublishingStorage()
content_storage = ContentStorage()

approved = jobs.list_jobs(
    status="approved"
)

if not approved:
    st.info("No approved jobs available.")
    st.stop()


selected_id = st.selectbox(
    "Approved job",
    [job["job_id"] for job in approved]
)

job = jobs.get_job(selected_id)

video_path = job.get("video_path")

if not video_path or not Path(video_path).is_file():
    st.error("Rendered video is unavailable.")
    st.stop()

st.video(video_path)

plan = content_storage.get_plan(
    job["plan_id"]
)

if hasattr(plan, "model_dump"):
    plan = plan.model_dump()

if plan:
    st.subheader("Publishing metadata")
    st.write("Title:", plan.get("title"))
    st.write(
        "Description:",
        plan.get("description")
    )
    st.write(
        "Hashtags:",
        " ".join(plan.get("hashtags", []))
    )


platform = st.selectbox(
    "Destination platform",
    ["youtube", "facebook"]
)

existing = storage.get_by_job(
    selected_id,
    platform
)

if existing and existing['platform'] == 'facebook' and existing['status'] == 'retry_authorized' and not existing.get('platform_video_id'):
    st.info('A manually inspected Facebook retry is authorized. Uploading still requires your separate publishing action below.')
elif existing:
    st.warning(
        "A publication attempt already exists. "
        f"Status: {existing['status']}. "
        "Do not upload the same job again."
    )
    st.stop()


st.subheader("Pre-publication check")

st.write(
    "YouTube uploads default to private. "
    "Facebook publishing may make the Reel "
    "available to its configured audience."
)

dry_run = st.checkbox(
    "Dry run only (no upload)",
    value=True
)

confirmed = st.checkbox(
    "I authorize this upload to the "
    "selected platform."
)

service = PublishingService(
    jobs=jobs,
    content_storage=content_storage,
    publications=storage
)

if st.button(
    "Validate" if dry_run else "Publish video",
    type="primary",
    disabled=not confirmed
):
    try:
        result = service.publish(
            job_id=selected_id,
            platform=platform,
            dry_run=dry_run
        )

        if dry_run:
            st.success(
                "Validation completed. "
                "No upload was performed."
            )
        else:
            st.success(
                "Upload request completed. "
                "Check processing and "
                "visibility before treating "
                "the video as published."
            )

        st.json(result)

    except Exception as exc:
        st.error(
            f"Publishing failed: {exc}"
        )

        st.warning(
            "If an upload was attempted, "
            "inspect the publication record "
            "before retrying."
        )
