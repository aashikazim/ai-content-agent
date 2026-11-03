
import os
import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st
from app.services.feedback_service import FeedbackService

from dotenv import load_dotenv

load_dotenv(".env")

DB_PATH = os.getenv(
    "ANALYTICS_DATABASE_PATH",
    "data/trends.db"
)


def query(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            sql,
            conn,
            params=params
        )


st.set_page_config(
    page_title="AI Content Agent",
    page_icon="🎬",
    layout="wide"
)

st.title("AI Content Agent")
st.caption(
    "Content generation, publishing, "
    "and performance analytics"
)

if st.button("Refresh data"):
    st.rerun()


# -------------------------
# Job summary
# -------------------------

jobs = query("""
    SELECT job_id, category, status,
           created_at
    FROM jobs
""")

publications = query("""
    SELECT publication_id, job_id,
           platform, status,
           platform_video_id, url,
           created_at
    FROM publications
""")

latest = query("""
    SELECT a.*
    FROM analytics_snapshots a
    WHERE a.snapshot_id = (
        SELECT MAX(b.snapshot_id)
        FROM analytics_snapshots b
        WHERE b.publication_id =
              a.publication_id
    )
""")


total_jobs = len(jobs)

approved_jobs = (
    int((jobs["status"] == "approved").sum())
    if not jobs.empty else 0
)

total_publications = len(publications)

total_views = (
    int(latest["views"].fillna(0).sum())
    if not latest.empty else 0
)

c1, c2, c3, c4 = st.columns(4)

c1.metric("Generated jobs", total_jobs)
c2.metric("Approved jobs", approved_jobs)
c3.metric("Publication attempts", total_publications)
c4.metric("Reported views", total_views)


# -------------------------
# Publishing status
# -------------------------

st.subheader("Publishing status")

if publications.empty:
    st.info("No publication records yet.")
else:
    counts = (
        publications
        .groupby(["platform", "status"])
        .size()
        .reset_index(name="count")
    )

    fig = px.bar(
        counts,
        x="platform",
        y="count",
        color="status",
        barmode="group",
        title="Publication attempts by platform"
    )

    st.plotly_chart(
        fig,
        use_container_width=True
    )


# -------------------------
# Video performance
# -------------------------

st.subheader("Video performance")

if latest.empty:
    st.info(
        "No analytics snapshots yet. "
        "Run collect_analytics.py."
    )
else:
    performance = latest.merge(
        publications[
            [
                "publication_id",
                "job_id",
                "url"
            ]
        ],
        on="publication_id",
        how="left"
    )

    performance = performance.merge(
        jobs[["job_id", "category"]],
        on="job_id",
        how="left"
    )

    st.dataframe(
        performance[
            [
                "platform",
                "platform_video_id",
                "category",
                "views",
                "likes",
                "comments",
                "watch_time_minutes",
                "average_view_duration",
                "url"
            ]
        ].sort_values(
            "views",
            ascending=False,
            na_position="last"
        ),
        use_container_width=True,
        hide_index=True
    )


# -------------------------
# Historical performance
# -------------------------

st.subheader("Views over time")

if not latest.empty:
    options = latest[
        "publication_id"
    ].tolist()

    selected = st.selectbox(
        "Select publication",
        options=options
    )

    history = query("""
        SELECT collected_at, views
        FROM analytics_snapshots
        WHERE publication_id = ?
        ORDER BY collected_at
    """, (selected,))

    if not history.empty:
        history["collected_at"] = (
            pd.to_datetime(
                history["collected_at"],
                utc=True
            )
        )

        fig = px.line(
            history,
            x="collected_at",
            y="views",
            markers=True,
            title="Cumulative reported views"
        )

        st.plotly_chart(
            fig,
            use_container_width=True
        )


# -------------------------
# Category comparison
# -------------------------

st.subheader("Performance by category")

if not latest.empty:
    category_stats = (
        performance
        .groupby("category", dropna=False)
        .agg(
            videos=("publication_id", "count"),
            avg_views=("views", "mean"),
            total_views=("views", "sum")
        )
        .reset_index()
    )

    st.dataframe(
        category_stats,
        use_container_width=True,
        hide_index=True
    )

    fig = px.bar(
        category_stats,
        x="category",
        y="avg_views",
        title="Average reported views by category"
    )

    st.plotly_chart(
        fig,
        use_container_width=True
    )


# -------------------------
# Publication records
# -------------------------

st.subheader("Publication records")

st.dataframe(
    publications,
    use_container_width=True,
    hide_index=True
)





st.subheader("Content feedback")

if not latest.empty:
    feedback = FeedbackService()

    ranked = feedback.rank_categories(
        performance.to_dict(
            orient="records"
        )
    )

    if ranked:
        st.dataframe(
            pd.DataFrame(ranked),
            use_container_width=True,
            hide_index=True
        )

        eligible = [
            item
            for item in ranked
            if item["video_count"] >= 3
        ]

        if eligible:
            best = eligible[0]

            st.info(
                "Highest-scoring category among "
                "categories with at least three "
                f"videos: {best['category']}"
            )
        else:
            st.info(
                "Collect at least three videos "
                "per category before using "
                "category rankings for decisions."
            )
