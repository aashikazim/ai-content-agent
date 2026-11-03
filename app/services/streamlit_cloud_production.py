"""Manual cloud controls over the same authenticated API used by React."""
import base64
import json

import streamlit as st
from app.services.streamlit_admin_service import ContentAPIError


def cloud_production(api):
    st.divider()
    st.header('Cloud video production')
    st.caption('Reuse a completed shot plan, explicitly authorize moving clips, assemble locally, then inspect quality before manual review. No automatic local fallback. Runway-hosted Kling models use the Runway key; direct Kling is retired.')
    try:
        scripts = [item for item in api.scripts() if item['status'] == 'completed' and item.get('production_plan', {}).get('shots')]
        references = api.cloud('references')['references']
        productions = api.cloud('videos')['productions']
    except ContentAPIError as error:
        st.warning(str(error)); return
    st.markdown('[Powered by Runway](https://runwayml.com)')
    if st.button('Check Runway credits'):
        try:
            account = api.cloud('runway/credits')
            st.info(f"Runway available credits: {account.get('available_credits', 'unavailable')} (current snapshot)")
        except ContentAPIError as error: st.error(str(error))
    with st.expander('Character reference registry'):
        st.caption('For character images, use IDs from the script. For scene start frames, use an ID such as scene_1. Upload images you may use; selected images are sent to the selected cloud provider only after paid confirmation. Voice editing is unsupported and continuity is not guaranteed.')
        with st.form('cloud_character_upload'):
            fields = {key: st.text_input(label, key='cloud_ref_' + key, max_chars=limit) for key,label,limit in [
                ('character_id','Character ID',32), ('name','Character name',80), ('description','Appearance description',1000),
                ('wardrobe','Clothing',500), ('voice_notes','Voice notes',500), ('rights_note','Image source/rights note',500)]}
            image = st.file_uploader('Character reference image (128px minimum, 4MB maximum)', type=['png','jpg','jpeg','webp'], key='cloud_ref_image')
            rights = st.checkbox('I have permission to use this image and any depicted person', key='cloud_ref_rights')
            upload = st.form_submit_button('Save character reference locally')
        if upload and (not rights or not image):
            st.error('Select an image and confirm permission to use it.')
        if upload and image and rights:
            try:
                if image.size > 4 * 1024 * 1024:
                    st.error('Choose an image smaller than 4MB.')
                else:
                    api.cloud('references', {**fields,'rights_confirmed':True,'image_base64':base64.b64encode(image.getvalue()).decode()})
                    st.rerun()
            except ContentAPIError as error: st.error(str(error))
        for reference in references:
            st.write(f"{reference['name']} · {reference['character_id']}: {reference['description']}")
    by_script = {item['attempt_id']:item for item in scripts}
    selected = st.selectbox('Completed script with a shot plan', [None,*by_script], format_func=lambda value: 'Choose a script' if value is None else by_script[value]['production_plan']['content_plan']['title'], key='cloud_script')
    by_reference = {item['reference_id']:item for item in references}
    selected_refs = st.multiselect('Character references to send (maximum 3)', list(by_reference), max_selections=3,
        format_func=lambda value: by_reference[value]['name'] + ' · ' + by_reference[value]['character_id'], key='cloud_references')
    config = (by_script.get(selected, {}).get('configuration') or {})
    provider_name = 'Runway' if config.get('video_provider') == 'runway' else 'Gemini'
    frame, shot_frames = None, {}
    if config.get('video_provider') == 'runway' and config.get('video_input_mode') == 'image':
        st.subheader('Scene start frames')
        st.caption('Select a composed scene per shot. A shared frame explicitly starts multiple shots from the same image. Use the registry above to upload permission-confirmed scene images.')
        label = lambda value: by_reference[value]['name'] if value else 'Use individual frame / shared frame'
        frame = st.selectbox('Shared start frame (optional)', [None, *by_reference], format_func=label, key='cloud_frame_' + selected)
        for shot in by_script[selected]['production_plan']['shots']:
            image = st.selectbox(f"Start frame for shot {shot['shot_number']}", [None, *by_reference], format_func=label, key=f"cloud_frame_{selected}_{shot['shot_number']}")
            if image: shot_frames[shot['shot_number']] = image
    fingerprint = json.dumps([selected,selected_refs,frame,shot_frames])
    if st.session_state.get('cloud_quote_fingerprint') != fingerprint:
        st.session_state['cloud_quote_fingerprint'] = fingerprint
        st.session_state.pop('cloud_quote',None)
    uncertain = st.session_state.get('cloud_uncertain',False)
    if st.button(f'Estimate {provider_name} video cost', disabled=not selected or uncertain):
        try:
            quote = api.cloud('video-quotes', {'script_attempt_id':selected,'reference_ids':selected_refs, **({'start_image_reference_id':frame,'shot_image_reference_ids':shot_frames} if config.get('video_input_mode') == 'image' else {})})
            st.session_state['cloud_quote'] = {**quote,'kind':'create'}
        except ContentAPIError as error: st.error(str(error))
    if quote := st.session_state.get('cloud_quote'):
        st.info(f"Estimated request cost: ${quote['estimate_usd']}; {quote['shot_count']} shot(s). Projected production estimate: ${quote['projected_estimate_usd']}.")
        st.write(quote['notice'])
        st.write(quote.get('request_summary',{}))
        provider_name = quote.get('provider_label', provider_name)
        if quote.get('estimated_credits') is not None:
            st.info(f"Runway estimated credits: {quote['estimated_credits']}; available at quote time: {quote.get('available_credits', 'unavailable')}.")
        st.subheader(f'Authorize {provider_name} video cost')
        confirmed = st.checkbox(f'I authorize these {provider_name} requests and transfer of the selected material at the displayed estimated cost', key='cloud_confirm_' + quote['quote_id'])
        if uncertain:
            st.warning('A previous submission has an unknown outcome. Inspect production history and provider usage before requesting a new quote.')
        elif not confirmed:
            st.info(f'Check the {provider_name} video authorization checkbox above to enable Confirm paid video generation. Script-cost authorization is separate.')
        else:
            st.caption('Video cost authorized. Generation starts only when you click Confirm paid video generation.')
        if st.button('Confirm paid video generation', disabled=not confirmed or uncertain):
            try:
                path = 'videos' if quote['kind'] == 'create' else f"videos/{quote['production_id']}/shots"
                result = api.cloud(path, {'quote_id':quote['quote_id'],'confirmed':True}, paid=True)
                from uuid import UUID
                try: UUID(result['production_id'])
                except (KeyError,ValueError,TypeError): raise ContentAPIError('No valid production acknowledgement received; inspect history', uncertain=True)
                st.session_state['cloud_current'] = result['production_id']
                st.session_state.pop('cloud_quote',None)
                st.rerun()
            except ContentAPIError as error:
                st.error(str(error))
                if error.uncertain: st.session_state['cloud_uncertain'] = True
    if uncertain or st.session_state.get('cloud_uncertain'):
        st.error('Submission outcome is unknown. Inspect production history and provider usage before a new paid request. No automatic retry is sent.')
        if st.checkbox('I inspected production history and provider usage', key='cloud_inspected'):
            if st.button('Allow a separately quoted request'):
                st.session_state.pop('cloud_uncertain',None); st.session_state.pop('cloud_quote',None); st.rerun()
    by_id = {item['production_id']:item for item in productions}
    if st.button('Refresh production history'):
        current = st.session_state.get('cloud_current')
        try:
            if current: api.cloud(f'videos/{current}/refresh', {})
            st.rerun()
        except ContentAPIError as error: st.error(str(error))
    if not productions:
        st.info('No cloud video productions yet. Generate a Google- or Runway-selected script with a shot plan in the workshop above.'); return
    current_id = st.selectbox('Existing production', list(by_id), key='cloud_current',
        format_func=lambda value: by_id[value]['production_plan']['content_plan']['title'] + ' · ' + by_id[value]['status'])
    value = by_id[current_id]
    st.subheader(value['production_plan']['content_plan']['title'])
    st.write(f"Status: {value['status']}. Factual label: {value['production_plan']['factual_status']}. Job: {value['job_id']}")
    if value.get('error'): st.error(value['error'])
    if any(item['status'] == 'queued' for item in value['shots']):
        confirmed = st.checkbox('Continue only previously authorized, never-submitted shots after inspecting this batch', key='cloud_continue_confirm')
        if st.button('Continue unsubmitted authorized shots', disabled=not confirmed or uncertain):
            try: api.cloud(f'videos/{current_id}/continue', {'confirmed':True}, paid=True); st.rerun()
            except ContentAPIError as error:
                st.error(str(error))
                if error.uncertain: st.session_state['cloud_uncertain'] = True
        if st.button('Discard never-submitted shots'):
            try: api.cloud(f'videos/{current_id}/discard-queued',{'confirmed':True}); st.rerun()
            except ContentAPIError as error: st.error(str(error))
    for shot in value['shots']:
        attempt_id = shot['attempt_id']
        with st.expander(f"Shot {shot['shot_number']} · v{shot['version']} · {shot['status']}" + (' · selected' if shot['selected'] else '')):
            st.write(f"Estimated request cost: ${shot['estimate_usd']}. Calculated token cost: {shot.get('calculated_cost_usd') or 'unavailable'}. Actual bill is not reported.")
            if shot.get('error'): st.warning(shot['error'])
            if shot.get('video_url') and st.button('Preview this clip', key='cloud_preview_' + attempt_id):
                try: st.video(api.media_bytes(shot['video_url'].removeprefix('/api/v1')))
                except ContentAPIError as error: st.error(str(error))
            if shot['status'] == 'completed':
                trim = st.number_input('Trim start (seconds)', min_value=0.0,max_value=40.0,step=.1,value=float(shot.get('trim_start_seconds',0)),key='cloud_trim_' + attempt_id)
                if st.button('Use this shot and assemble', key='cloud_select_' + attempt_id):
                    try: api.cloud(f'videos/{current_id}/select',{'attempt_id':attempt_id,'trim_start_seconds':trim}); st.rerun()
                    except ContentAPIError as error: st.error(str(error))
            if shot['status'] in {'completed','failed','validation_failed'}:
                operation = st.selectbox('Shot operation', ['generate','edit','extend'] if shot['status'] == 'completed' and value.get('configuration', {}).get('video_provider') != 'runway' else ['generate'],key='cloud_operation_' + attempt_id)
                instructions = st.text_area('Shot change instructions',max_chars=2000,key='cloud_instructions_' + attempt_id)
                extension = st.number_input('Extension seconds',min_value=3,max_value=10,value=3,key='cloud_extension_' + attempt_id) if operation == 'extend' else 3
                st.caption('Extended candidates preserve the parent. Inspect output and select a trim window that fits the existing planned shot; no automatic timeline extension.')
                if st.button('Estimate this shot change',key='cloud_quote_' + attempt_id,disabled=uncertain):
                    try:
                        quote = api.cloud(f'videos/{current_id}/shot-quotes',{'shot_number':shot['shot_number'],'parent_attempt_id':attempt_id,'operation':operation,'instructions':instructions,'extension_seconds':extension})
                        st.session_state['cloud_quote'] = {**quote,'kind':'change','production_id':current_id}; st.rerun()
                    except ContentAPIError as error: st.error(str(error))
            if shot['status'] in {'needs_reconciliation','download_failed'}:
                notes = st.text_input('Provider usage inspection notes',max_chars=500,key='cloud_notes_' + attempt_id)
                remote = st.text_input('Interaction ID found (optional)',max_chars=200,key='cloud_remote_' + attempt_id)
                checked = st.checkbox('I inspected provider usage and accept any prior charge',key='cloud_reconciled_' + attempt_id)
                close_attempt = st.checkbox('Close this attempt locally; this does not cancel or refund provider generation',key='cloud_close_' + attempt_id)
                if st.button('Record reconciliation without resubmitting',key='cloud_reconcile_' + attempt_id,disabled=not checked or not notes.strip()):
                    try: api.cloud(f'videos/{current_id}/shots/{attempt_id}/reconcile',{'notes':notes,'confirmed':True,'interaction_id':remote or None,'close_attempt':close_attempt}); st.rerun()
                    except ContentAPIError as error: st.error(str(error))
    quality_key = current_id + '_' + '_'.join(item['attempt_id'] + str(item.get('trim_start_seconds',0)) for item in value['shots'] if item['selected'])
    if value['status'] == 'technical_ready':
        st.subheader('Check the assembled video')
        if st.button('Preview assembled video'):
            try: st.video(api.media_bytes(f"/jobs/{value['job_id']}/video"))
            except ContentAPIError as error: st.error(str(error))
        continuity = st.checkbox('I checked character appearance, clothing, actions and voice continuity',key='cloud_quality_continuity_' + quality_key)
        captions = st.checkbox('I checked spoken words, caption timing, readability and audio synchronization',key='cloud_quality_captions_' + quality_key)
        rights = st.checkbox('I checked factual claims, fiction/satire labeling, reference rights and provider terms',key='cloud_quality_rights_' + quality_key)
        st.caption('Caption times are estimated phrase timings, not forced speech alignment. Sending to review never approves or publishes.')
        if st.button('Send video to manual review',disabled=not all((continuity,captions,rights))):
            try: api.cloud(f'videos/{current_id}/review',{'continuity_confirmed':continuity,'captions_confirmed':captions,'rights_confirmed':rights}); st.rerun()
            except ContentAPIError as error: st.error(str(error))
