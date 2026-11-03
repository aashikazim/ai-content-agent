
import os
import hmac
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from app.services.review_dashboard_service import (
    ReviewDashboardService
)

load_dotenv(".env")

st.set_page_config(
    page_title="Content Review",
    page_icon="🎬",
    layout="wide"
)

st.title("Content Review Queue")

st.caption(
    "Review generated content before publishing. "
    "Approval does not publish the video."
)


def authorized():
    expected = os.getenv(
        "CONTENT_REVIEW_API_KEY", ""
    )

    if not expected:
        st.error(
            "CONTENT_REVIEW_API_KEY is not configured."
        )
        return False

    entered = st.text_input(
        "Review access key",
        type="password"
    )

    return bool(
        entered
        and hmac.compare_digest(
            entered,
            expected
        )
    )


if not authorized():
    st.info(
        "Enter the review key to access "
        "the review queue."
    )
    st.stop()


service = ReviewDashboardService()

pending = service.list_pending()

st.metric(
    "Awaiting review",
    len(pending)
)

if not pending:
    st.success(
        "No videos are currently awaiting review."
    )
    st.stop()


job_ids = [
    job["job_id"]
    for job in pending
]

selected_id = st.selectbox(
    "Select a job",
    options=job_ids,
    format_func=lambda job_id: next(
        (
            f"{job.get('category', 'unknown')} "
            f"| {job_id[:8]}"
            for job in pending
            if job["job_id"] == job_id
        ),
        job_id
    )
)

details = service.get_review_details(
    selected_id
)

job = details["job"]
plan = details["plan"]

st.subheader("Job information")

st.write("Job ID:", job["job_id"])
st.write("Category:", job.get("category"))
st.write("Status:", job["status"])

if details["video_exists"]:
    st.subheader("Generated video")

    st.video(
        job["video_path"]
    )
else:
    st.error(
        "Rendered video file is missing."
    )


if plan:
    st.subheader("Content plan")

    st.markdown(
        f"**Title:** {plan.get('title', '')}"
    )

    st.markdown(
        f"**Description:** "
        f"{plan.get('description', '')}"
    )

    st.markdown(
        "**Hashtags:** "
        + " ".join(
            plan.get("hashtags", [])
        )
    )

    st.markdown(
        f"**Hook:** {plan.get('hook', '')}"
    )

    st.subheader("Script and storyboard")

    for scene in plan.get("scenes", []):
        with st.expander(
            f"Scene {scene['scene_number']} "
            f"— {scene['duration_seconds']} seconds"
        ):
            st.markdown("**Narration**")
            st.write(
                scene.get("narration", "")
            )

            st.markdown("**Visual prompt**")
            st.write(
                scene.get("visual_prompt", "")
            )

            st.markdown("**On-screen text**")
            st.write(
                scene.get("on_screen_text", "")
            )


st.divider()

st.subheader("Manual review decision")

confirmed = st.checkbox(
    "I reviewed the video, narration, "
    "metadata, factual claims, and "
    "copyright-related concerns."
)

col1, col2 = st.columns(2)

with col1:
    if st.button(
        "Approve",
        type="primary",
        disabled=(
            not confirmed
            or not details["video_exists"]
        ),
        use_container_width=True
    ):
        try:
            service.review(
                selected_id,
                "approved"
            )

            st.success(
                "Video approved. "
                "It has NOT been published."
            )

            st.rerun()

        except Exception as exc:
            st.error(str(exc))


with col2:
    if st.button(
        "Reject",
        disabled=not confirmed,
        use_container_width=True
    ):
        try:
            service.review(
                selected_id,
                "rejected"
            )

            st.success(
                "Video rejected."
            )

            st.rerun()

        except Exception as exc:
            st.error(str(exc))
