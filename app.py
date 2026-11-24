"""Multi-Agent Orchestration System: Streamlit entry point.

Run from the project folder with:  streamlit run app.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st  # noqa: E402

from ui import pages  # noqa: E402
from ui.bootstrap import get_services  # noqa: E402

st.set_page_config(page_title="Multi-Agent Orchestration", page_icon="🧭", layout="wide")

PAGES = {
    "Overview Dashboard": pages.page_overview,
    "AI Task Workspace": pages.page_workspace,
    "Agent Management": pages.page_agents,
    "Execution Monitor": pages.page_monitor,
    "Reports": pages.page_reports,
    "Execution History": pages.page_history,
    "Settings": pages.page_settings,
}

services = get_services()
with st.sidebar:
    st.markdown("## Multi-Agent Orchestration")
    choice = st.radio("Navigate", list(PAGES), label_visibility="collapsed")
    st.divider()
    if services.settings.is_mock:
        st.caption("Demo mode: no API key required.")
    else:
        st.caption(f"Live mode: {services.settings.provider}.")

PAGES[choice](services)
