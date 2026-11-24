"""Streamlit page tests using Streamlit's AppTest runner. Skipped automatically if Streamlit or Plotly is missing."""
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app.py"
PAGES = [
    "Overview Dashboard",
    "AI Task Workspace",
    "Agent Management",
    "Execution Monitor",
    "Reports",
    "Execution History",
    "Settings",
]


def new_app():
    pytest.importorskip("streamlit")
    pytest.importorskip("plotly")
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(APP), default_timeout=120)


def test_app_starts_on_the_overview_without_exceptions():
    app = new_app().run()
    assert not app.exception


@pytest.mark.parametrize("page", PAGES)
def test_every_dashboard_page_renders_without_exceptions(page):
    app = new_app().run()
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception
