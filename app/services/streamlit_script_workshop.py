"""Streamlit views over the same script/cost API used by the React studio."""

import json

import streamlit as st

from app.services.streamlit_admin_service import ContentAPIError


def paid_authorization(api, configuration, context, key):
    fingerprint = json.dumps([configuration, context], sort_keys=True)
    if st.session_state.get(key + "_request") != fingerprint:
        st.session_state[key + "_request"] = fingerprint
        st.session_state.pop(key + "_quote", None)
        st.session_state[key + "_confirmed"] = False
    if st.button("Check access and estimate script cost", key=key + "_estimate"):
        try:
            st.session_state[key + "_quote"] = api.script_quote(configuration, context)
            st.session_state[key + "_confirmed"] = False
        except ContentAPIError as error:
            st.error(str(error))
    quote = st.session_state.get(key + "_quote")
    if quote and quote.get("paid"):
        st.info(f"Maximum estimated token cost: ${quote['maximum_cost_usd']}. One script request; quote expires after ten minutes. Actual provider billing may differ.")
        confirmed = st.checkbox("I authorize one paid script request up to this estimated token bound", key=key + "_confirmed")
        if confirmed:
            return {"quote_id": quote["quote_id"], "confirmed": True}
    return None


def script_workshop(api, configuration, category, region, limit):
    st.divider()
    st.subheader("Script workshop")
    st.caption("Draft and revise 30–60-second outlines, dialogue and narration. News remains unverified; drama/animation video and long drama are later phases.")
    try:
        attempts = api.scripts()
    except ContentAPIError as error:
        st.warning(str(error))
        return
    complete = {item["attempt_id"]: item for item in attempts if item["status"] == "completed"}
    parent = st.selectbox("Revise a completed script", [None, *complete],
        format_func=lambda value: "New script" if not value else complete[value]["production_plan"]["content_plan"]["title"], key="script_parent")
    revision = st.text_area("Revision instructions", max_chars=2000, key="script_revision")
    context = {"category": category, "region": region, "limit": limit, "purpose": "script",
               "parent_attempt_id": parent, "revision_instructions": revision}
    authorization = None
    if configuration["script_provider"] == "openai":
        st.warning("OpenAI receives your supplied material. This authorizes text generation only, never a video upload or cloud video request.")
        if st.button("Check available OpenAI models"):
            try:
                st.info("Available supported models: " + ", ".join(api.openai_models()))
            except ContentAPIError as error:
                st.error(str(error))
        authorization = paid_authorization(api, configuration, context, "script_draft")
    active = any(item["status"] in {"queued", "running"} for item in attempts)
    uncertain = st.session_state.get("script_submission_uncertain", False)
    if st.button("Generate script draft", disabled=active or uncertain or (
        configuration["script_provider"] == "openai" and not authorization
    )):
        try:
            result = api.create_script(configuration, context, authorization)
            st.session_state["script_latest"] = result["attempt_id"]
            st.session_state.pop("script_draft_quote", None)
            st.rerun()
        except ContentAPIError as error:
            st.error(str(error))
            if error.uncertain:
                st.session_state["script_submission_uncertain"] = True
    if uncertain:
        st.error("Script request outcome is unknown. Inspect history/provider usage before another request.")
        if st.checkbox("I inspected script history and provider usage", key="script_inspected"):
            if st.button("Allow a separately authorized new request"):
                st.session_state.pop("script_submission_uncertain", None)
                st.rerun()
    if st.button("Refresh script history"):
        st.rerun()
    for item in attempts:
        with st.expander(f"{item['model']} · {item['status']} · {item['attempt_id'][:8]}", expanded=item["attempt_id"] == st.session_state.get("script_latest")):
            if item.get("error"):
                st.warning(item["error"])
            production = item.get("production_plan")
            if production:
                st.write(production["content_plan"]["title"])
                st.caption("Content label: " + production["factual_status"])
                st.json(production)
            st.caption(f"Calculated token cost: {item.get('calculated_cost_usd') or 'unavailable'} USD. Actual billed cost is not reported by the provider.")
            if item["status"] == "needs_reconciliation":
                notes = st.text_input("Provider usage inspection notes", key="reconcile_notes_" + item["attempt_id"], max_chars=500)
                confirmed = st.checkbox("I checked provider usage and accept any prior charges", key="reconcile_confirm_" + item["attempt_id"])
                if st.button("Close uncertain request", key="reconcile_" + item["attempt_id"], disabled=not notes.strip() or not confirmed):
                    try:
                        api.reconcile_script(item["attempt_id"], notes, confirmed)
                        st.rerun()
                    except ContentAPIError as error:
                        st.error(str(error))
