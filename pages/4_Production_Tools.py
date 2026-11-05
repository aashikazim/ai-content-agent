"""Shared production tooling remains in the existing React/FastAPI application."""
import os
import streamlit as st
from dotenv import load_dotenv

load_dotenv('.env')
st.set_page_config(page_title='Production Tools',page_icon='🛠️',layout='wide')
st.title('Production Tools')
st.write('Neural narration, source-linked research, licensed stock, speech-aligned captions and immutable scene regeneration are available in the existing web dashboard.')
st.link_button('Open Production tools',os.getenv('CONTENT_API_URL','http://127.0.0.1:8000').rstrip('/')+'/#production-tools')
st.info('Use your existing administrator account. Approval and publishing remain separate manual actions. Voice and research selections are also available in Create Video.')
